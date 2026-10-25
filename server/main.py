"""FastAPI app: pages, the create-game API and the WebSocket endpoint.

Run:  python -m server.main --sim
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .adapters import BRIDGES, make_adapters
from .engine import SYSTEM, EngineError
from .rooms import GameRoom
from .rules.monopoly import board, cards
from .store import Store
from .tunnel import Tunnel

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DEFAULT_DB = ROOT / "games.db"
log = logging.getLogger("server")


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


class CreateGame(BaseModel):
    players: list[PlayerSpec]


def create_app(sim: bool = True, move_delay: float = 1.0, seed: int | None = None,
               db_path: str | Path | None = ":memory:", bridge: str = "stub",
               public: str | None = None, tunnel_port: int | None = None) -> FastAPI:
    """db_path: SQLite file that games are saved to and resumed from on startup.
    ":memory:" (the default, used by tests) keeps nothing across restarts.
    bridge: "stub" fakes moves; "arduino" drives lane 1 through arduino/server.py.
    public: a base URL phones can reach (e.g. an ngrok address), used for join links.
    tunnel_port: start a cloudflared tunnel to this port and use its URL as `public`."""
    rooms: dict[str, GameRoom] = {}
    store = Store(db_path or ":memory:")
    net = {"public": public.rstrip("/") if public else None}
    tunnel = Tunnel(tunnel_port) if tunnel_port else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if tunnel and not net["public"]:
            net["public"] = await tunnel.start()
        # Startup: bring every unfinished saved game back to life.
        for saved in store.games(status="active"):
            try:
                rooms[saved["game_id"]] = await GameRoom.restore(saved, make_adapters(sim, move_delay, bridge), store)
                log.info("resumed game %s (turn %s, %s)", saved["game_id"],
                         saved["state"].get("turn"), saved["state"].get("phase"))
            except Exception as e:                       # one bad row must not block the server
                log.warning("could not resume game %s: %s", saved["game_id"], e)
        yield
        if tunnel:
            await tunnel.stop()
        store.close()

    app = FastAPI(title="Magic Mirror Monopoly", lifespan=lifespan)
    app.state.rooms = rooms
    app.state.store = store
    if bridge == "arduino":
        # Avish's motor endpoints (/board/*, /move, /stop, /pos), unchanged. His /vision/events
        # only logged; ours below feeds the engine, so his is left out.
        from .hardware import motor_routes
        app.router.routes.extend(r for r in motor_routes() if getattr(r, "path", None) != "/vision/events")

    if not sim:
        make_adapters(False, move_delay, bridge)  # fail at startup, not at first game, if real adapters are missing

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
        return page("kid.html")

    @app.get("/board/{game_id}")
    async def board_page(game_id: str):
        return page("board.html")

    # --- API ---
    @app.post("/api/games")
    async def create_game(body: CreateGame):
        try:
            room = await GameRoom.create([p.model_dump() for p in body.players],
                                         make_adapters(sim, move_delay, bridge), seed, store)
        except EngineError as e:
            raise HTTPException(400, str(e))
        rooms[room.game_id] = room
        return links(room)

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
    # vision/watch.py POSTs its events here. It knows nothing about the game, so
    # its vocabulary is mapped onto the engine's and the engine decides whether a
    # read is wanted (a roll nobody is waiting for is rejected, not applied).
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
        if game is not None:
            room = rooms.get(game)
            if room is None:
                raise HTTPException(404, "no such game")
        else:
            # The game being played right now: the most recently active room that is past the lobby.
            live = [r for r in rooms.values() if r.state.phase.value not in ("LOBBY", "GAME_OVER")]
            if not live:
                raise HTTPException(409, "no running game to send this to")
            room = max(live, key=lambda r: r.updated_at)
        await room.on_adapter_event(mapped)
        return {"ok": True, "game": room.game_id, "sent": mapped}

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
            actor, kind = pid, "seat"
            await room.connect_seat(pid, ws)
        elif host is not None:
            if host != room.host_token:
                return await reject("wrong host link", 4403)
            actor, kind = SYSTEM, "host"
            await room.connect_viewer(kind, ws)
        elif role == "board":
            actor, kind = SYSTEM, "board"
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
                        help="stub: fake moves; arduino: Grandma's lane through arduino/server.py "
                             "(set DRY_RUN=1 without an Arduino), other lanes stubbed")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file games are saved to and resumed from")
    parser.add_argument("--tunnel", action="store_true", help="start a cloudflared tunnel and use its public URL in links")
    parser.add_argument("--public", default=None, help="public base URL to use in links instead (e.g. an ngrok address)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    uvicorn.run(create_app(sim=args.sim, seed=args.seed, db_path=args.db, bridge=args.bridge, public=args.public,
                           tunnel_port=args.port if args.tunnel else None), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
