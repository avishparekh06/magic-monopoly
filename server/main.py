"""FastAPI app: pages, the create-game API and the WebSocket endpoint.

Run:  python -m server.main --sim
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hmac
import random
import time
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .adapters import BRIDGES, MIRRORS, VOICES, make_adapters, make_mirror, make_voice
from .adapters.mirror import TemplateMirror
from .demo import DEMO_FIRST_PLAYER, DEMO_FORCED_ROLLS, DEMO_MOVES, DEMO_PLAYERS, is_demo_game, is_fresh_demo
from .engine import SYSTEM, EngineError, new_game, to_public
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
               speech: str = "browser", dev: bool = False, camera_cmd: list[str] | None = None,
               virtual_axis: int | None = None, bench_url: str | None = None,
               lane_axes: dict[int, int] | None = None) -> FastAPI:
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
    # Muse can be switched on and off while the server runs (the demo controls' toggle); the flags give the start value.
    muse = {"mirror": mirror == "muse", "voice": voice == "muse"}
    TemplateMirror.hears_all = muse["voice"]      # the Mirror tells every player to answer aloud
    rooms: dict[str, GameRoom] = {}

    def adapters():
        a = make_adapters(sim, move_delay, bridge if bridge_factory is None else "stub",
                          ("muse" if muse["voice"] else "stub") if voice_factory is None else "stub",
                          ("muse" if muse["mirror"] else "template") if mirror_factory is None else "template", belt_url, belt_lanes, belt_key,
                          virtual_axis, bench_url, lane_axes)
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

    jog_axes = lane_axes if bridge == "http" and lane_axes else {2: virtual_axis} if virtual_axis is not None else {}
    demo_on = bool(dev and jog_axes)       # demo mode: the belt tool drives the tokens and the panel is on

    def arm_demo(room: GameRoom) -> None:
        room.forced_rolls = {pid: list(rolls) for pid, rolls in DEMO_FORCED_ROLLS.items()}
        room.require_joined = True

    async def new_demo_room() -> GameRoom:
        """A started game with Player 1 and 2 on their lined-up squares. It is saved like any other game, so it
        shows up under "Continue game"; the scripted belt moves play when its board opens."""
        room = await GameRoom.create(DEMO_PLAYERS, adapters(), seed, store)
        room.dev = dev
        arm_demo(room)
        rooms[room.game_id] = room
        await room.dispatch(SYSTEM, {"action": "start", "force": True, "first": DEMO_FIRST_PLAYER})
        return room

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
                rooms[saved["game_id"]] = await GameRoom.restore(saved, adapters(), store, sync_belt=not demo_on)
                rooms[saved["game_id"]].dev = dev
                if debug_sync:
                    await rooms[saved["game_id"]].set_debug_sync(True)
                log.info("resumed game %s (turn %s, %s)", saved["game_id"],
                         saved["state"].get("turn"), saved["state"].get("phase"))
            except Exception as e:                       # one bad row must not block the server
                log.warning("could not resume game %s: %s", saved["game_id"], e)
        if demo_on:
            ready = [r for r in rooms.values() if is_fresh_demo(r.state)]
            for r in ready:
                arm_demo(r)
            if not ready:
                await new_demo_room()
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

    jog_on = demo_on
    if jog_on:   # the demo panel's motor buttons, passed on to the belt tool that owns the Arduino
        import os
        from .jog import jog_router
        app.include_router(jog_router(is_local, bench_url or belt_url or os.environ.get("BENCH_URL", "http://127.0.0.1:8001"),
                                      jog_axes))

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
        return page("phone.html")

    @app.get("/board/{game_id}")
    async def board_page(game_id: str, request: Request):
        room = rooms.get(game_id)
        if demo_on and room and is_local(request) and is_demo_game(room.state) and not room.demo_started \
                and not is_fresh_demo(room.state):
            # A demo game that was already played before this server started: ignore its saved state and
            # give the full demo again, from the squares the tokens are lined up on.
            fresh = await new_demo_room()
            return RedirectResponse(f"/board/{fresh.game_id}", status_code=302, headers={"Cache-Control": "no-store"})
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
        room.dev = dev
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

    management_lock = asyncio.Lock()

    async def manage_game(game_id: str, host: str, request: Request, operation: str):
        same_origin(request)
        async with management_lock:
            room = rooms.get(game_id)
            async with room.lock if room else asyncio.Lock():
                saved = store.game(game_id)
                if not saved:
                    raise HTTPException(404, "no such game")
                if not hmac.compare_digest(host.encode(), saved["host_token"].encode()):
                    raise HTTPException(403, "wrong host token")
                state = room.state if room else None
                if operation != 'end' and state and state.phase.value == 'MOVING':
                    raise HTTPException(409, "Wait for the current token move to finish before resetting or deleting this game.")
                if operation == 'reset':
                    snapshot = to_public(state) if state else saved['state']
                    specs = [{'name': player['name'], 'kind': player['kind']} for player in snapshot['players']]
                    fresh_state = new_game(game_id, specs)
                    for player in fresh_state.players:
                        player.connected = player.kind == 'in_person' or bool(room and player.id in room.seats)
                    if not room:
                        room = GameRoom(fresh_state, adapters(), random.Random(saved['seed']), store,
                                        host_token=saved['host_token'], seat_tokens=saved['seat_tokens'])
                        await room._start_adapters()
                    store.reset_game(game_id, specs, to_public(fresh_state))
                    room.cancel_background()
                    room.generation += 1
                    room.state = fresh_state
                    room.seq = 0
                    room.rng.seed(saved['seed'])
                    room.dev = dev
                    room.debug_sync = False
                    room.belt_positions.clear()
                    room.lobby_positions = {p.id: {'x': .25 + i * .25, 'y': .62, 'jump': 0} for i, p in enumerate(fresh_state.players)}
                    room._lobby_sent.clear()
                    room._lobby_jumped.clear()
                    room.updated_at = time.time()
                    rooms[game_id] = room
                    await room.broadcast({'type': 'state', 'state': to_public(fresh_state)})
                    await room.broadcast({'type': 'lobby_presence', 'players': room.lobby_positions})
                    return {'ok': True, 'game_id': game_id, 'status': 'active', 'host_url': f'/?game={game_id}&host={room.host_token}'}
                if operation == 'delete':
                    store.delete_game(game_id)
                else:
                    store.set_status(game_id, 'abandoned')
                if room:
                    await room.close_game('This game was deleted by the host.' if operation == 'delete' else 'This game was ended by the host.')
                    rooms.pop(game_id, None)
                return {'ok': True, 'game_id': game_id, 'status': 'deleted' if operation == 'delete' else 'abandoned'}

    @app.post("/api/games/{game_id}/abandon")
    async def abandon_game(game_id: str, host: str, request: Request):
        return await manage_game(game_id, host, request, 'end')

    @app.post("/api/games/{game_id}/reset")
    async def reset_game(game_id: str, host: str, request: Request):
        return await manage_game(game_id, host, request, 'reset')

    @app.post("/api/games/{game_id}/delete")
    async def delete_game(game_id: str, host: str, request: Request):
        return await manage_game(game_id, host, request, 'delete')

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
        if demo_on:     # only this run's demo game: old saved games must not steal the camera or its dice reads
            live = [r for r in live if r.require_joined] or live
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
        question = TemplateMirror().question(room.state) or ""
        try:
            heard = await v.hear(wav, pend["player"], question)
        except Exception as e:
            log.warning("voice transcription failed: %s", e)
            raise HTTPException(502, f"transcription failed: {e}")
        if heard.get("transcript") and not heard.get("answer") and room.state.phase.value == "WAITING_FOR_DECISION":
            # Something was said but it was neither a clear yes nor no: the Mirror asks again, aloud.
            again = TemplateMirror().ask_again(room.state)
            if again:
                await room.broadcast({"type": "mirror", "text": again})
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
        """What the pages should show: the dev panel and on-screen dice only in dev mode, and whether
        yes/no is answered by voice (Muse) so the board can leave out the Buy / No buttons."""
        return {"dev": dev, "voice": "muse" if muse["voice"] else "stub", "jog": jog_on, "jog_players": sorted(jog_axes),
                "demo": demo_on, "muse": muse["voice"] or muse["mirror"]}

    def demo_guard(request: Request) -> None:
        if not dev:
            raise HTTPException(404, "demo mode needs --dev")
        if not is_local(request):
            raise HTTPException(403, "demo controls are for the board laptop")

    @app.post("/api/muse")
    async def set_muse(request: Request, on: bool):
        """Dev, laptop only: switch Meta Muse (the narrator's lines and spoken yes/no) on or off for every game,
        including the ones already running. Pages reload to pick the change up."""
        demo_guard(request)
        try:
            make_voice("muse" if on else "stub")                   # fails now, before anything changes, with no MUSE_API_KEY
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        muse["mirror"] = muse["voice"] = on
        TemplateMirror.hears_all = on
        for room in rooms.values():
            room.adapters.mirror = make_mirror("muse" if on else "template")
            room.adapters.voice = make_voice("muse" if on else "stub")
            await room.adapters.voice.start(room.on_adapter_event)
        return {"muse": on}

    @app.post("/api/demo/step")
    async def demo_step(request: Request, game: str, player: str, squares: int):
        """Dev, laptop only: nudge one player's token `squares` forward (negative = back) on the belt and in the game."""
        demo_guard(request)
        room = get_room(game)
        if player not in [p.id for p in room.state.players] or not squares or abs(squares) > 3:
            raise HTTPException(422, "player must be in the game and squares between -3 and 3 (not 0)")
        if room.state.phase.value == "MOVING" or room.state.pending is not None:
            raise HTTPException(409, "wait for the token to stop and the pending decision to finish")
        p = room.state.player(player)
        to = (p.position + squares) % 40
        result = await room.adapters.bridge.move(p.lane, p.position, to)
        if result.get("type") == "move_failed":
            raise HTTPException(502, f"belt move failed: {result.get('reason')}")
        try:
            await room.dispatch(SYSTEM, {"action": "set_position", "player": player, "square": to})
        except EngineError as e:
            raise HTTPException(409, str(e))
        return {"player": player, "square": to}

    @app.post("/api/demo/reset")
    async def demo_reset(request: Request, game: str | None = None):
        """Dev, laptop only: start the demo over. Tokens drive back to their lined-up squares, the current game is
        retired, and a fresh demo game is made. Answers with the board to open (it plays the opening moves again)."""
        demo_guard(request)
        if not demo_on:
            raise HTTPException(404, "reset needs the belt wiring (--lane-axes or --virtual-axis)")
        room = rooms.get(game) if game else None
        if room is not None and is_demo_game(room.state):
            for p, spec in zip(room.state.players, DEMO_PLAYERS):
                if p.position != spec["position"]:
                    result = await room.adapters.bridge.move(p.lane, p.position, spec["position"])
                    if result.get("type") == "move_failed":
                        raise HTTPException(502, f"{p.name} could not drive back: {result.get('reason')}")
            store.set_status(room.game_id, "abandoned")
            if room._poll_task:
                room._poll_task.cancel()
            rooms.pop(room.game_id, None)
        fresh = await new_demo_room()
        return {"ok": True, "game_id": fresh.game_id, "board_url": f"/board/{fresh.game_id}"}

    @app.post("/api/demo/resume")
    async def demo_resume(request: Request):
        """Dev only, laptop only. Start the demo game: Player 1 and 2 are lined up by hand on their squares, the game
        starts there, and the demo moves are played on the belt in the background."""
        if not dev:
            raise HTTPException(404, "demo mode needs --dev")
        if not is_local(request):
            raise HTTPException(403, "demo resume is for the board laptop")
        room = await new_demo_room()
        room.demo_started = True
        room._spawn(room.demo_moves(DEMO_MOVES))
        return {"ok": True, "game_id": room.game_id, "board_url": f"/board/{room.game_id}"}

    @app.get("/api/games/{game_id}/invites")
    async def game_invites(game_id: str, request: Request):
        """The phone invite links of this game's remote players, as full addresses (laptop only, for the board page)."""
        if not is_local(request):
            raise HTTPException(403, "invite links are shown on the board laptop only")
        room = get_room(game_id)
        base = net["public"] or f"http://{lan_ip()}:{request.url.port or 80}"
        return {"players": [{"id": pid, "name": room.state.player(pid).name, "url": base + path}
                            for pid, path in ((p, l) for p, l in room.join_links().items())]}

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
        #   ?seat=<token>  a remote player's phone, acts as that seat's player
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
            if local and demo_on and not room.demo_started and is_fresh_demo(room.state):
                room.demo_started = True      # opening the demo game's board plays its scripted belt moves, once
                room._spawn(room.demo_moves(DEMO_MOVES))
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
    parser.add_argument("--virtual-axis", type=int, default=None, metavar="AXIS",
                        help="with --bridge step: the second player's token drives this motor axis (0-2) on the belt "
                             "tool, e.g. 0; without it that token is simulated")
    parser.add_argument("--lane-axes", default=None, metavar="LANE:AXIS,...",
                        help="with --bridge http: which motor axis each player's lane drives on the belt tool, "
                             "e.g. 1:0,2:2 (Player 1 on axis 0, Player 2 on axis 2); default URL http://127.0.0.1:8001")
    parser.add_argument("--bench-url", default=None,
                        help="with --virtual-axis: the belt tool (arduino/server.py), default http://127.0.0.1:8001")
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
                           debug_sync=args.debug_sync, speech=args.speech, dev=args.dev, camera_cmd=camera_cmd,
                           virtual_axis=args.virtual_axis, bench_url=args.bench_url,
                           lane_axes=dict(map(int, pair.split(":")) for pair in args.lane_axes.split(",")) if args.lane_axes else None),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
