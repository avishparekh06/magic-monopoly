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
import time
from typing import Any, Protocol

from .adapters import Adapters
from .engine import SYSTEM, EngineError, GameState, Phase, apply_action, from_public, new_game, to_public
from .store import Store

log = logging.getLogger("room")

TAKEN_OVER = 4001  # WebSocket close code: another device took this seat


class Socket(Protocol):
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


class GameRoom:
    def __init__(self, state: GameState, adapters: Adapters, rng: random.Random,
                 store: Store | None = None, host_token: str | None = None,
                 seat_tokens: dict[str, str] | None = None, seq: int = 0):
        self.state = state
        self.adapters = adapters
        self.rng = rng
        self.store = store
        self.seq = seq                      # number of actions applied so far (0 = just created)
        self.updated_at = time.time()       # last action; picks "the game being played" among several
        self.host_token = host_token or secrets.token_urlsafe(9)
        # token -> player id, for remote seats only
        self.seat_tokens = seat_tokens or {secrets.token_urlsafe(9): p.id
                                           for p in state.players if p.kind == "remote"}
        self.lock = asyncio.Lock()
        self.seats: dict[str, Socket] = {}       # player id -> its one live socket
        self.viewers: dict[Socket, str] = {}     # host / board sockets -> role
        self._tasks: set[asyncio.Task] = set()

    @classmethod
    async def create(cls, specs: list[dict], adapters: Adapters,
                     seed: int | None = None, store: Store | None = None) -> "GameRoom":
        game_id = secrets.token_urlsafe(6)
        room = cls(new_game(game_id, specs), adapters, random.Random(seed), store)
        if store:
            store.create_game(game_id, specs, seed, room.host_token, room.seat_tokens, to_public(room.state))
        await room._start_adapters()
        return room

    @classmethod
    async def restore(cls, saved: dict, adapters: Adapters, store: Store) -> "GameRoom":
        """Rebuild a room from a Store.games() row and pick up where it left off.

        Remote seats come back as disconnected (their phones will reconnect by
        link). A move that was in flight is re-issued. The bridge is told where
        every token should be so real hardware can drive them back."""
        state = from_public(saved["state"])
        for p in state.players:
            if p.kind == "remote":
                p.connected = False
        room = cls(state, adapters, random.Random(saved.get("seed")), store,
                   host_token=saved["host_token"], seat_tokens=saved["seat_tokens"], seq=saved["seq"])
        room.updated_at = saved.get("updated_at") or room.updated_at
        await room._start_adapters()
        await adapters.bridge.restore({p.lane: p.position for p in state.players if not p.bankrupt})
        if state.phase == Phase.MOVING:
            await room.dispatch(SYSTEM, {"action": "retry_move"})
        return room

    async def _start_adapters(self) -> None:
        await self.adapters.vision.start(self.on_adapter_event)
        await self.adapters.voice.start(self.on_adapter_event)

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
            self.seq += 1
            self.updated_at = time.time()
            if self.store and action.get("action") != "set_connected":   # presence is not game history
                self.store.append(self.game_id, self.seq, actor, action, events, to_public(self.state))
            if events:
                await self.broadcast({"type": "state", "state": to_public(self.state)})
                for ev in events:
                    await self.broadcast(ev)
        await self._react(events)
        return events

    async def _react(self, events: list[dict]) -> None:
        # Mirror lines are written concurrently when the Mirror is slow (a Muse call is ~1-2 s each) but
        # spoken in event order. The template Mirror is instant, so it is simply awaited in turn.
        slow = len(events) > 1 and hasattr(self.adapters.mirror, "ask")
        pending = [asyncio.create_task(self.adapters.mirror.line(ev, self.state)) for ev in events] if slow else None
        for i, ev in enumerate(events):
            try:
                line = await pending[i] if slow else await self.adapters.mirror.line(ev, self.state)
            except Exception as e:
                log.info("mirror failed for %s: %s", ev.get("type"), e)
                line = None
            if line:
                await self.broadcast({"type": "mirror", "text": line})
            if ev.get("type") == "game_started":   # tokens were placed on the start square by hand
                task = asyncio.create_task(self.adapters.bridge.reset([p.lane for p in self.state.players]))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
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
            if msg.get("action") == "say":
                return await self.say(actor, str(msg.get("text", "")))
            if msg.get("action") == "sync_belt":
                if role not in ("board", "host"):
                    raise EngineError("only the board or host page can sync from the belt")
                return await self.sync_belt()
            # A seat may only act as itself, so its "player" claim is dropped; the board/host act as the
            # system and may name a player (set_position, decision).
            action = dict(msg) if actor == SYSTEM else {k: v for k, v in msg.items() if k != "player"}
            await self.dispatch(actor, action)
        except (EngineError, ValueError) as e:
            await self._safe_send(ws, {"type": "error", "message": str(e)})

    async def say(self, actor: str, text: str) -> None:
        """A remote player speaks to the table: the Mirror repeats it aloud on the board laptop and
        every phone sees it. This is the phones' voice in the room."""
        text = " ".join(text.split())[:160]
        if not text:
            raise EngineError("say something first")
        name = next((p.name for p in self.state.players if p.id == actor), "The host")
        await self.broadcast({"type": "mirror", "kind": "say", "from": actor, "name": name,
                              "text": f"{name} says: {text}"})

    async def sync_belt(self) -> None:
        """Debug: ask the belt where its token really is and put the game's token there."""
        bridge = self.adapters.bridge
        if not hasattr(bridge, "position"):
            raise EngineError("this bridge cannot report a position (run with --bridge step)")
        pos = await bridge.position()
        if pos is None:
            raise EngineError("the belt gateway did not answer with a position")
        lanes = getattr(bridge, "lanes", 1)
        for p in self.state.players:
            if p.lane <= lanes:
                await self.dispatch(SYSTEM, {"action": "set_position", "player": p.id, "square": pos})

    async def scene(self, text: str) -> None:
        """What the Mirror sees at the table, for the players who are not there."""
        await self.broadcast({"type": "mirror", "kind": "scene", "text": text})

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
