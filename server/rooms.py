"""GameRoom: one running game plus everyone connected to it.

The room is the only place that calls engine.apply_action. After every
change it pushes the full state to every socket, then the events, then
any Mirror lines. Adapter outputs (move_done, camera reads, voice) come
back in through `on_adapter_event` and go through the same path.

Seat tokens and the host token live here, never in the engine state, so
they can't leak into the state broadcast.
"""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
from typing import Any, Protocol

from .adapters import Adapters
from .engine import SYSTEM, EngineError, GameState, apply_action, new_game, to_public

log = logging.getLogger("room")

TAKEN_OVER = 4001  # WebSocket close code: another device took this seat


class Socket(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class GameRoom:
    def __init__(self, state: GameState, adapters: Adapters, rng: random.Random):
        self.state = state
        self.adapters = adapters
        self.rng = rng
        self.host_token = secrets.token_urlsafe(9)
        # token -> player id, for remote seats only
        self.seat_tokens = {secrets.token_urlsafe(9): p.id
                            for p in state.players if p.kind == "remote"}
        self.lock = asyncio.Lock()
        self.seats: dict[str, Socket] = {}       # player id -> its one live socket
        self.viewers: dict[Socket, str] = {}     # host / board sockets -> role
        self._tasks: set[asyncio.Task] = set()

    @classmethod
    async def create(cls, specs: list[dict], adapters: Adapters,
                     seed: int | None = None) -> "GameRoom":
        game_id = secrets.token_urlsafe(6)
        room = cls(new_game(game_id, specs), adapters, random.Random(seed))
        await adapters.vision.start(room.on_adapter_event)
        await adapters.voice.start(room.on_adapter_event)
        return room

    @property
    def game_id(self) -> str:
        return self.state.game_id

    def join_links(self) -> dict[str, str]:
        return {pid: f"/game/{self.game_id}/join/{tok}" for tok, pid in self.seat_tokens.items()}

    # --- connections -----------------------------------------------------

    def all_sockets(self) -> list[Socket]:
        return [*self.seats.values(), *self.viewers]

    async def connect_seat(self, pid: str, ws: Socket) -> None:
        old = self.seats.get(pid)
        self.seats[pid] = ws
        if old is not None and old is not ws:
            await self._safe_send(old, {"type": "error", "message": "seat taken over on another device"})
            try:
                await old.close(code=TAKEN_OVER)
            except Exception:
                pass
        await self._safe_send(ws, {"type": "hello", "you": pid, "role": "seat"})
        # Marking connected broadcasts state to everyone, including this socket.
        events = await self.dispatch(SYSTEM, {"action": "set_connected", "player": pid, "connected": True})
        if not events:  # seat was already marked connected (takeover), so no broadcast happened
            await self._safe_send(ws, {"type": "state", "state": to_public(self.state)})

    async def connect_viewer(self, role: str, ws: Socket) -> None:
        self.viewers[ws] = role
        await self._safe_send(ws, {"type": "hello", "you": None, "role": role})
        await self._safe_send(ws, {"type": "state", "state": to_public(self.state)})

    async def disconnect(self, ws: Socket) -> None:
        self.viewers.pop(ws, None)
        for pid, sock in list(self.seats.items()):
            # Only the seat's *current* socket counts; a taken-over socket closing is ignored.
            if sock is ws:
                del self.seats[pid]
                await self.dispatch(SYSTEM, {"action": "set_connected", "player": pid, "connected": False})

    # --- the one path that changes state ---------------------------------

    async def dispatch(self, actor: str, action: dict) -> list[dict]:
        """Apply an action and broadcast the result. Raises EngineError if illegal."""
        async with self.lock:
            self.state, events = apply_action(self.state, actor, action, self.rng)
            if events:
                await self.broadcast({"type": "state", "state": to_public(self.state)})
                for ev in events:
                    await self.broadcast(ev)
        await self._react(events)
        return events

    async def _react(self, events: list[dict]) -> None:
        for ev in events:
            line = await self.adapters.mirror.line(ev, self.state)
            if line:
                await self.broadcast({"type": "mirror", "text": line})
            if ev.get("type") == "move":   # emitted by the engine from milestone 3
                task = asyncio.create_task(self._run_move(ev))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _run_move(self, ev: dict) -> None:
        result = await self.adapters.bridge.move(ev["lane"], ev["from"], ev["to"])
        await self.on_adapter_event(result)

    async def on_adapter_event(self, ev: dict) -> None:
        """Bridge/vision/voice output -> engine action from the system actor.

        {"type": "dice", "value": 7} becomes {"action": "dice", "value": 7}.
        Rejections (e.g. a read nobody is waiting for) go to the board page.
        """
        action = {"action": ev["type"], **{k: v for k, v in ev.items() if k != "type"}}
        try:
            await self.dispatch(SYSTEM, action)
        except EngineError as e:
            log.info("adapter event %s rejected: %s", ev, e)
            for ws, role in list(self.viewers.items()):
                if role == "board":
                    await self._safe_send(ws, {"type": "error", "message": f"{ev['type']}: {e}"})

    # --- messages from a client ------------------------------------------

    async def handle_message(self, ws: Socket, actor: str, role: str, msg: dict) -> None:
        try:
            source = msg.get("source")
            if source is not None:
                if role != "board":
                    raise EngineError("only the board's dev panel can do that")
                adapter = {"vision": self.adapters.vision, "voice": self.adapters.voice}.get(source)
                if adapter is None or not hasattr(adapter, "dev_input"):
                    raise EngineError(f"no dev input for {source} (are you running --sim?)")
                await adapter.dev_input(msg)
                return
            claimed = msg.get("player")
            if actor != SYSTEM and claimed is not None and claimed != actor:
                raise EngineError(f"you can only act as {actor}")
            action = {k: v for k, v in msg.items() if k != "player"}
            await self.dispatch(actor, action)
        except (EngineError, ValueError) as e:
            await self._safe_send(ws, {"type": "error", "message": str(e)})

    # --- sending -----------------------------------------------------------

    async def broadcast(self, msg: dict) -> None:
        for ws in self.all_sockets():
            await self._safe_send(ws, msg)

    @staticmethod
    async def _safe_send(ws: Socket, msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            pass  # socket is closing; its disconnect handler cleans up
