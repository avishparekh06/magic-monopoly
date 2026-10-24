"""FastAPI app: pages, the create-game API and the WebSocket endpoint.

Run:  python -m server.main --sim
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .adapters import BRIDGES, make_adapters
from .engine import SYSTEM, EngineError
from .rooms import GameRoom
from .rules.monopoly import board, cards

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


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


class PlayerSpec(BaseModel):
    name: str
    kind: str  # "in_person" | "remote"


class CreateGame(BaseModel):
    players: list[PlayerSpec]


def create_app(sim: bool = True, move_delay: float = 1.0, seed: int | None = None,
               bridge: str = "stub") -> FastAPI:
    app = FastAPI(title="Magic Mirror Monopoly")
    if bridge == "arduino":
        # Avish's motor endpoints (/board/*, /move, /stop, /pos, /vision/events), unchanged.
        from .hardware import motor_routes
        app.router.routes.extend(motor_routes())
    rooms: dict[str, GameRoom] = {}
    app.state.rooms = rooms

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
                                         make_adapters(sim, move_delay, bridge), seed)
        except EngineError as e:
            raise HTTPException(400, str(e))
        rooms[room.game_id] = room
        return links(room)

    @app.get("/api/games/{game_id}/links")
    async def game_links(game_id: str, host: str):
        room = get_room(game_id)
        if host != room.host_token:
            raise HTTPException(403, "wrong host token")
        return links(room)

    @app.get("/api/lan")
    async def lan(request: Request):
        return {"base": f"http://{lan_ip()}:{request.url.port or 80}"}

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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    uvicorn.run(create_app(sim=args.sim, seed=args.seed, bridge=args.bridge), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
