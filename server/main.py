"""FastAPI app: pages, the create-game API and the WebSocket endpoint.

Run:  python -m server.main --sim
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .adapters import BRIDGES, MIRRORS, VOICES, make_adapters
from .engine import SYSTEM, EngineError
from .rooms import GameRoom
from .rules.monopoly import board, cards
from .store import Store
from .account_routes import COOKIE, install_account_routes, same_origin
from .tunnel import Tunnel
from . import tts
from .camera import CameraProcess, CameraStatus

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DEFAULT_DB = ROOT / "games.db"
log = logging.getLogger("server")


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """KEY=value lines into os.environ (existing variables win). Keeps the Muse key out of git."""
    import os
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def lan_ip() -> str:
    """This laptop's Wi-Fi address, so join links work on phones (not "localhost").

    connect() on a UDP socket sends no packets; it just makes the OS pick
    the outgoing interface, whose address we then read.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def translate_vision_event(ev: dict) -> dict | None:
    """vision/watch.py vocabulary -> engine actions. None = nothing for the engine.
    Raises ValueError on a malformed body."""
    t = ev.get("type")
    try:
        if t == "dice_rolled":
            return {"type": "dice", "value": int(ev["total"])}
        if t == "dice_unclear":
            return {"type": "dice_not_visible"}
        if t == "card_drawn":
            return {"type": "card", "id": str(ev["card_id"])}
        if t == "card_unknown":
            return {"type": "card_unreadable"}
        if t == "player_card":
            return {"type": "player_card", "player": str(ev["player"])}
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"bad {t} event: {e}") from None
    return None          # dice_cleared, card_removed, card_unknown: the engine has no use for these


def is_local(request: Request) -> bool:
    """True only for requests from this laptop. A tunnel (cloudflared, ngrok) connects
    from 127.0.0.1 too, but it always adds a forwarding header, so those count as remote."""
    if request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for"):
        return False
    return request.client is None or request.client.host in ("127.0.0.1", "::1", "testclient")


class PlayerSpec(BaseModel):
    name: str
    kind: str  # "in_person" | "remote"
    position: int | None = None   # debug: the square the physical token already sits on (default 0)


class CreateGame(BaseModel):
    players: list[PlayerSpec]


def create_app(sim: bool = True, move_delay: float = 1.0, seed: int | None = None,
               db_path: str | Path | None = ":memory:", bridge: str = "stub",
               public: str | None = None, tunnel_port: int | None = None,
               voice: str = "stub", voice_factory=None, mirror: str = "template",
               mirror_factory=None, belt_url: str | None = None, belt_lanes: int = 1,
               bridge_factory=None, belt_key: str | None = None, debug_sync: bool = False,
               speech: str = "browser", dev: bool = False, camera_cmd: list[str] | None = None) -> FastAPI:
    """db_path: SQLite file that games are saved to and resumed from on startup.
    ":memory:" (the default, used by tests) keeps nothing across restarts.
    bridge: "stub" fakes moves; "arduino" drives lane 1 through arduino/server.py.
    public: a base URL phones can reach (e.g. an ngrok address), used for join links.
    tunnel_port: start a cloudflared tunnel to this port and use its URL as `public`.
    voice: "stub" (dev-panel buttons) or "muse" (Meta Muse Voice Transcribe via /voice/clip).
    voice_factory: tests pass a callable returning a Voice to stand in for `voice`.
    mirror: "template" or "muse" (Meta Muse Spark writes the narrator's lines, templates as fallback).
    speech: "browser" (the device's own voice, free) or "elevenlabs" (ElevenLabs reads the Mirror's lines).
    dev: show the board page's dev panel (manual dice, cards, voice, payments, motors).
    camera_cmd: start this command (vision/live.py --post) as a child once the server is up, stop it with the server."""
    tts.use(speech == "elevenlabs")
    rooms: dict[str, GameRoom] = {}

    def adapters():
        a = make_adapters(sim, move_delay, bridge if bridge_factory is None else "stub",
                          voice if voice_factory is None else "stub",
                          mirror if mirror_factory is None else "template", belt_url, belt_lanes, belt_key)
        if bridge_factory is not None:
            a.bridge = bridge_factory()
        if voice_factory is not None:
            a.voice = voice_factory()
        if mirror_factory is not None:
            a.mirror = mirror_factory()
        return a

    store = Store(db_path or ":memory:")
    net = {"public": public.rstrip("/") if public else None}
    tunnel = Tunnel(tunnel_port) if tunnel_port else None

    camera = CameraStatus()
    GameRoom.camera = camera
    camera_proc = CameraProcess(camera_cmd) if camera_cmd else None

    async def publish_camera(msg: dict) -> None:
        room = live_room(None)
        if room is not None:
            await room.broadcast(msg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if tunnel and not net["public"]:
            net["public"] = await tunnel.start()
        watchdog = asyncio.create_task(camera.watch(publish_camera))
        if camera_proc:
            camera_proc.start()
        # Startup: bring every unfinished saved game back to life.
        for saved in store.games(status="active"):
            try:
                rooms[saved["game_id"]] = await GameRoom.restore(saved, adapters(), store)
                if debug_sync:
                    await rooms[saved["game_id"]].set_debug_sync(True)
                log.info("resumed game %s (turn %s, %s)", saved["game_id"],
                         saved["state"].get("turn"), saved["state"].get("phase"))
            except Exception as e:                       # one bad row must not block the server
                log.warning("could not resume game %s: %s", saved["game_id"], e)
        yield
        watchdog.cancel()
        if camera_proc:
            camera_proc.stop()
        if tunnel:
            await tunnel.stop()
        store.close()

    app = FastAPI(title="Magic Mirror Monopoly", lifespan=lifespan)
    app.state.rooms = rooms
    app.state.store = store
    install_account_routes(app, store, rooms, is_local)
    if bridge == "arduino":
        # Avish's motor endpoints (/board/*, /move, /stop, /pos), unchanged. His /vision/events
        # only logged; ours below feeds the engine, so his is left out.
        from .hardware import motor_routes
        app.router.routes.extend(r for r in motor_routes() if getattr(r, "path", None) != "/vision/events")

    if not sim or (voice == "muse" and voice_factory is None) or (mirror == "muse" and mirror_factory is None) \
            or (bridge in ("http", "step") and bridge_factory is None):
        adapters()  # fail at startup, not at first game, if a real adapter is missing (e.g. no MUSE_API_KEY)

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC / name)

    def get_room(game_id: str) -> GameRoom:
        room = rooms.get(game_id)
        if room is None:
            raise HTTPException(404, "no such game")
        return room

    def links(room: GameRoom) -> dict:
        return {
            "game_id": room.game_id,
            "host_token": room.host_token,
            "board_url": f"/board/{room.game_id}",
            "join_links": room.join_links(),
        }

    # --- pages ---
    @app.get("/")
    async def create_page():
        return page("create.html")

    @app.get("/game/{game_id}/join/{seat_token}")
    async def kid_page(game_id: str, seat_token: str):
        room = rooms.get(game_id)
        if room and room.state.phase.value == "LOBBY":
            pid = room.seat_tokens.get(seat_token)
            if pid and room.state.player(pid).kind == "remote":
                return RedirectResponse(f"/?game={game_id}&seat={seat_token}", status_code=302, headers={"Cache-Control": "no-store"})
        return page("kid.html")

    @app.get("/board/{game_id}")
    async def board_page(game_id: str):
        return page("board.html")

    # --- API ---
    @app.post("/api/games")
    async def create_game(body: CreateGame, request: Request):
        same_origin(request)
        if not is_local(request):
            raise HTTPException(403, "games are created on the board laptop")
        account = store.accounts.current(request.cookies.get(COOKIE))
        try:
            room = await GameRoom.create([p.model_dump() for p in body.players],
                                         adapters(), seed, store, owner_id=account["id"] if account else None)
        except EngineError as e:
            raise HTTPException(400, str(e))
        rooms[room.game_id] = room
        if debug_sync:
            await room.set_debug_sync(True)
        return links(room)

    # --- the physical side reports where a token is ---
    @app.post("/belt/position")
    async def belt_position(request: Request, lane: int = 1, square: int = 0, key: str | None = None,
                            game: str | None = None):
        """Called by the belt gateway (or the bench tool) whenever its tracked square changes:
        POST /belt/position?lane=1&square=14&key=<BELT_KEY>. Local callers need no key."""
        import os
        expected = belt_key or os.environ.get("BELT_KEY")
        if not is_local(request) and (not expected or key != expected):
            raise HTTPException(403, "wrong key")
        if not 0 <= square < 40:
            raise HTTPException(422, "square must be 0-39")
        targets = [rooms[game]] if game and game in rooms else \
            [r for r in rooms.values() if r.state.phase.value != "GAME_OVER"]
        if game and game not in rooms:
            raise HTTPException(404, "no such game")
        for room in targets:
            await room.belt_reported(lane, square, source="push")
        return {"ok": True, "games": [r.game_id for r in targets]}

    @app.post("/api/games/{game_id}/abandon")
    async def abandon_game(game_id: str, host: str):
        """Host gives up on a game: it stops being resumed on restart, its links die, its history stays."""
        room = rooms.get(game_id)
        saved = store.game(game_id)
        token = room.host_token if room else (saved or {}).get("host_token")
        if token is None:
            raise HTTPException(404, "no such game")
        if host != token:
            raise HTTPException(403, "wrong host token")
        if room:
            if room._poll_task:
                room._poll_task.cancel()
            for ws in room.all_sockets():
                await room._safe_send(ws, {"type": "error", "message": "this game was ended by the host", "fatal": True})
                try:
                    await ws.close(code=4410)
                except Exception:
                    pass
            rooms.pop(game_id, None)
        store.set_status(game_id, "abandoned")
        return {"ok": True, "game_id": game_id, "status": "abandoned"}

    @app.get("/api/games")
    async def list_games(request: Request):
        """Saved games, newest first, for the resume picker. Host links are only
        included for requests from the laptop itself (the host's own browser)."""
        local = is_local(request)
        out = []
        for g in store.games():
            st = g["state"]
            row = {
                "game_id": g["game_id"], "status": g["status"], "created_at": g["created_at"],
                "updated_at": g["updated_at"], "turn": st.get("turn"), "phase": st.get("phase"),
                "players": [{"id": p["id"], "name": p["name"], "balance": p["balance"],
                             "position": p["position"], "bankrupt": p["bankrupt"]} for p in st["players"]],
                "winner": st.get("winner"), "live": g["game_id"] in rooms,
                "board_url": f"/board/{g['game_id']}",
            }
            if local:
                row["host_url"] = f"/?game={g['game_id']}&host={g['host_token']}"
            out.append(row)
        return out

    @app.get("/api/games/{game_id}/history")
    async def game_history(game_id: str):
        if store.game(game_id) is None:
            raise HTTPException(404, "no such game")
        return store.history(game_id)

    @app.get("/api/games/{game_id}/stats")
    async def game_stats(game_id: str):
        room = rooms.get(game_id)
        if room is None and store.game(game_id) is None:
            raise HTTPException(404, "no such game")
        from .engine import to_public
        return store.stats(game_id, to_public(room.state) if room else None)

    @app.get("/api/games/{game_id}/links")
    async def game_links(game_id: str, host: str):
        room = get_room(game_id)
        if host != room.host_token:
            raise HTTPException(403, "wrong host token")
        return links(room)

    # --- camera process -> engine ---
    # vision/live.py POSTs its events here. It knows nothing about the rules, so
    # its vocabulary is mapped onto the engine's and the engine decides whether a
    # read is wanted (a roll nobody is waiting for is rejected, not applied).
    def live_room(game: str | None) -> GameRoom | None:
        """The game the camera is watching: the one asked for, else the room being played right now."""
        if game is not None:
            room = rooms.get(game)
            if room is None:
                raise HTTPException(404, "no such game")
            return room
        live = [r for r in rooms.values() if r.state.phase.value not in ("LOBBY", "GAME_OVER")]
        if not live:
            return None
        return max(live, key=lambda r: (r.has_board(), r.updated_at))   # prefer the room the laptop shows

    def wanted_mode(game: str | None) -> dict:
        """What the camera should look for right now: "card" while the game waits for a drawn card to be
        read, "dice" otherwise."""
        room = live_room(game)
        if room is None:
            return {"mode": "dice", "game": None, "phase": None, "detail": "no game running"}
        s = room.state
        phase = s.phase.value
        cur = s.current_player
        out = {"mode": "dice", "game": room.game_id, "phase": phase, "player": cur.name, "deck": None}
        if phase == "WAITING_FOR_CARD":
            pend = s.pending or {}
            deck = pend.get("deck")
            who = s.player(pend["player"]).name if pend.get("player") else cur.name
            deck_name = cards()["decks"].get(deck, {}).get("name", deck) if deck else "a card"
            out.update(mode="card", deck=deck, player=who, detail=f"{who} drew from {deck_name}: show the card")
        elif phase == "WAITING_FOR_ROLL":
            out["detail"] = f"{cur.name} to roll"
        elif phase == "MOVING":
            out["detail"] = f"{cur.name} is moving"
        elif phase == "WAITING_FOR_DECISION":
            out["detail"] = f"{cur.name} is deciding"
        elif phase == "WAITING_FOR_PAYMENT":
            out["detail"] = "waiting for a payment"
        else:
            out["detail"] = phase.replace("_", " ").lower()
        return out

    @app.get("/vision/mode")
    async def vision_mode(request: Request, game: str | None = None):
        """The camera process polls this to switch between the dice tray and the card zone by itself."""
        if not is_local(request):
            raise HTTPException(403, "vision state is for the camera process on this machine only")
        return wanted_mode(game)

    @app.post("/vision/sync")
    async def vision_sync(report: dict, request: Request, game: str | None = None):
        """The camera's heartbeat: what it is looking at right now (mode, dice state, what it sees).
        The board page shows it live; the answer is the same as GET /vision/mode."""
        if not is_local(request):
            raise HTTPException(403, "vision state is for the camera process on this machine only")
        camera.update(report)
        snap = camera.changed()
        if snap is not None:
            await publish_camera(snap)
        return wanted_mode(game)

    @app.post("/vision/events")
    async def vision_event(event: dict, request: Request, game: str | None = None):
        if not is_local(request):          # the camera process runs on this laptop; nobody else may inject reads
            raise HTTPException(403, "vision events are accepted from this machine only")
        try:
            mapped = translate_vision_event(event)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if mapped is None:
            return {"ok": True, "ignored": event.get("type")}
        room = live_room(game)
        if room is None:
            raise HTTPException(409, "no running game to send this to")
        await room.on_adapter_event(mapped)
        # A frame of the table along with the roll: the Mirror describes it for the players who are not there.
        frame = event.get("frame_b64")
        if frame and hasattr(room.adapters.mirror, "describe_scene"):
            async def narrate_scene():
                names = ", ".join(p.name for p in room.state.players)
                text = await room.adapters.mirror.describe_scene(
                    frame, f"Players: {names}. The dice were just rolled: {event.get('values')}.")
                if text:
                    await room.scene(text)
            asyncio.create_task(narrate_scene())
        return {"ok": True, "game": room.game_id, "sent": mapped}

    # --- microphone clip -> Muse Voice Transcribe -> yes/no decision ---
    @app.post("/voice/clip")
    async def voice_clip(request: Request, game: str | None = None):
        """Body: a WAV clip (mono 16-bit PCM, 16 kHz) recorded by the board page while the
        engine waits for the in-person player's yes/no. Returns what was heard and understood."""
        if not is_local(request):
            raise HTTPException(403, "voice clips are accepted from this machine only")
        if game is not None:
            room = rooms.get(game)
            if room is None:
                raise HTTPException(404, "no such game")
        else:
            waiting = [r for r in rooms.values() if r.state.phase.value == "WAITING_FOR_DECISION"]
            if not waiting:
                return {"ok": False, "reason": "nobody is deciding right now"}
            room = max(waiting, key=lambda r: (r.has_board(), r.updated_at))
        v = room.adapters.voice
        if not hasattr(v, "hear"):
            raise HTTPException(503, "this server's voice adapter cannot transcribe (start with --voice muse)")
        pend = room.state.pending or {}
        if room.state.phase.value != "WAITING_FOR_DECISION" or pend.get("kind") != "decision":
            return {"ok": False, "reason": "nobody is deciding right now"}
        wav = await request.body()
        if len(wav) < 1000:
            raise HTTPException(422, "clip too short")
        try:
            heard = await v.hear(wav, pend["player"])
        except Exception as e:
            log.warning("voice transcription failed: %s", e)
            raise HTTPException(502, f"transcription failed: {e}")
        return {"ok": True, "game": room.game_id, **heard}

    # --- the Mirror's voice: ElevenLabs audio for a line the server broadcast ---
    @app.get("/tts")
    async def speak(text: str):
        if not tts.enabled():
            raise HTTPException(503, "no ELEVENLABS_API_KEY; pages use the browser voice")
        try:
            mp3 = await tts.audio(text)
        except KeyError:
            raise HTTPException(404, "not a Mirror line")
        except Exception as e:
            raise HTTPException(502, f"text-to-speech failed: {e}")
        return Response(mp3, media_type="audio/mpeg", headers={"cache-control": "private, max-age=3600"})

    @app.get("/api/config")
    async def config():
        """What the pages should show: the board's dev panel only in dev mode."""
        return {"dev": dev}

    @app.get("/api/lan")
    async def lan(request: Request):
        """Where phones should point: the public tunnel if there is one, else the LAN address."""
        lan_base = f"http://{lan_ip()}:{request.url.port or 80}"
        return {"base": net["public"] or lan_base, "lan": lan_base, "public": net["public"]}

    @app.get("/api/board")
    async def board_data():
        return board()

    @app.get("/api/cards")
    async def card_data():
        return cards()

    # --- WebSocket ---
    @app.websocket("/ws/{game_id}")
    async def ws_endpoint(ws: WebSocket, game_id: str, seat: str | None = None,
                          host: str | None = None, role: str | None = None):
        # Who you are comes from the URL credential, never from message contents:
        #   ?seat=<token>  kid's phone, acts as that seat's player
        #   ?host=<token>  create/lobby page, acts as "system" (can start)
        #   ?role=board    board laptop, acts as "system" (start + dev panel)
        await ws.accept()
        room = rooms.get(game_id)

        async def reject(message: str, code: int) -> None:
            await ws.send_json({"type": "error", "message": message, "fatal": True})
            await ws.close(code=code)

        if room is None:
            return await reject("this game doesn't exist (the server may have restarted)", 4404)
        if seat is not None:
            pid = room.seat_tokens.get(seat)
            if pid is None:
                return await reject("that join link isn't valid", 4403)
            if room.state.player(pid).kind != "remote":
                return await reject("This player is attending in person. Ask the host to switch the seat to virtual to use this link.", 4405)
            actor, kind = pid, "seat"
            await room.connect_seat(pid, ws)
        elif host is not None:
            if host != room.host_token:
                return await reject("wrong host link", 4403)
            actor, kind = SYSTEM, "host"
            await room.connect_viewer(kind, ws)
        elif role == "board":
            # The board page on the laptop acts as the system (dev panel, recovery). The same page opened
            # through the public tunnel is a read-only viewer: it sees everything and can change nothing.
            local = not (ws.headers.get("cf-connecting-ip") or ws.headers.get("x-forwarded-for")) and \
                (ws.client is None or ws.client.host in ("127.0.0.1", "::1", "testclient"))
            actor, kind = (SYSTEM, "board") if local else (None, "viewer")
            await room.connect_viewer(kind, ws)
        else:
            return await reject("missing seat, host or role", 4400)

        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    await ws.send_json({"type": "error", "message": "messages must be JSON objects"})
                    continue
                await room.handle_message(ws, actor, kind, msg)
        except WebSocketDisconnect:
            pass
        finally:
            await room.disconnect(ws)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Magic Mirror Monopoly server")
    parser.add_argument("--sim", action="store_true", help="use stub adapters (no hardware/camera/Meta APIs)")
    parser.add_argument("--host", default="0.0.0.0", help="0.0.0.0 lets phones on the same Wi-Fi connect")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=None, help="seed the dice for repeatable games")
    parser.add_argument("--bridge", choices=BRIDGES, default="stub",
                        help="stub: fake moves; arduino: lane 1 through arduino/server.py loaded in this process "
                             "(set DRY_RUN=1 without an Arduino); http: POST moves to a running arduino/server.py "
                             "(--belt-url), lanes above --belt-lanes stubbed; step: POST <belt-url>/step?squares=n&key= "
                             "(relative moves on track 1)")
    parser.add_argument("--belt-url", default=None,
                        help="with --bridge http: where arduino/server.py is running, e.g. http://192.168.1.20:8001")
    parser.add_argument("--belt-key", default=None, help="with --bridge step: the service's key (or BELT_KEY in .env)")
    parser.add_argument("--debug-sync", action="store_true",
                        help="testing: the game adopts the belt's reported square automatically (polls the gateway "
                             "and accepts pushes to /belt/position); off = only show the mismatch")
    parser.add_argument("--belt-lanes", type=int, default=1,
                        help="how many lanes the belt tool really drives (lanes above this are simulated)")
    parser.add_argument("--voice", choices=VOICES, default="stub",
                        help="stub: yes/no from the dev panel; muse: Meta Muse Voice Transcribe on clips from the board "
                             "page's microphone (needs MUSE_API_KEY)")
    parser.add_argument("--mirror", choices=MIRRORS, default="template",
                        help="template: fixed narrator lines; muse: Meta Muse Spark writes them (needs MUSE_API_KEY)")
    parser.add_argument("--speech", choices=("browser", "elevenlabs"), default="browser",
                        help="browser: the laptop's/phone's own voice (free); elevenlabs: ElevenLabs reads the Mirror's "
                             "lines (uses credits; ELEVENLABS_API_KEY in .env)")
    parser.add_argument("--dev", action="store_true",
                        help="show the board page's dev panel (manual dice, cards, voice, payments, motors); "
                             "off = a clean board for the demo")
    parser.add_argument("--camera", type=int, default=None, metavar="INDEX",
                        help="also start the camera process (vision/live.py INDEX --post) and stop it with the server, "
                             "so the table is one command; it reads dice, and the card markers while a card is wanted")
    parser.add_argument("--camera-args", default="", help="extra flags for the camera process, e.g. \"--frames\"")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file games are saved to and resumed from")
    parser.add_argument("--tunnel", action="store_true", help="start a cloudflared tunnel and use its public URL in links")
    parser.add_argument("--public", default=None, help="public base URL to use in links instead (e.g. an ngrok address)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    load_dotenv()
    from .camera import camera_command
    camera_cmd = camera_command(args.camera, args.port, args.camera_args.split()) if args.camera is not None else None
    uvicorn.run(create_app(sim=args.sim, seed=args.seed, db_path=args.db, bridge=args.bridge, public=args.public,
                           tunnel_port=args.port if args.tunnel else None, voice=args.voice, mirror=args.mirror,
                           belt_url=args.belt_url, belt_lanes=args.belt_lanes, belt_key=args.belt_key,
                           debug_sync=args.debug_sync, speech=args.speech, dev=args.dev, camera_cmd=camera_cmd),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
