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
import math
import random
import secrets
import time
from typing import Any, Protocol

from . import tts
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
        self.belt_positions: dict[int, int] = {}  # lane -> square the physical side last reported
        self.debug_sync = False                   # adopt the belt's square automatically (testing)
        self.dev = False                          # server --dev: the board's on-screen dice may roll
        self._poll_task: asyncio.Task | None = None
        self.seats: dict[str, Socket] = {}       # player id -> its one live socket
        self.viewers: dict[Socket, str] = {}     # host / board sockets -> role
        self._tasks: set[asyncio.Task] = set()
        self._held_lines: list[dict] = []   # events of a move in progress, narrated when it arrives
        # Ephemeral courtyard presence: never part of rules, saves, or turn history.
        self.lobby_positions = {p.id: {"x": .25 + i * .25, "y": .62, "jump": 0}
                                for i, p in enumerate(state.players)}
        self._lobby_sent: dict[str, float] = {}
        self._lobby_jumped: dict[str, float] = {}

    @classmethod
    async def create(cls, specs: list[dict], adapters: Adapters,
                     seed: int | None = None, store: Store | None = None, owner_id: str | None = None) -> "GameRoom":
        game_id = secrets.token_urlsafe(6)
        room = cls(new_game(game_id, specs), adapters, random.Random(seed), store)
        if store:
            store.create_game(game_id, specs, seed, room.host_token, room.seat_tokens, to_public(room.state), owner_id=owner_id)
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
        return {pid: f"/game/{self.game_id}/join/{tok}" for tok, pid in self.seat_tokens.items()
                if self.state.player(pid).kind == "remote"}

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

        await self._send_lobby(ws)

    async def _send_lobby(self, ws: Socket) -> None:
        if self.state.phase == Phase.LOBBY:
            await self._safe_send(ws, {"type": "lobby_presence", "players": self.lobby_positions})

    camera = None      # server.camera.CameraStatus, set by the app: what the camera process is looking at

    async def connect_viewer(self, role: str, ws: Socket) -> None:
        self.viewers[ws] = role
        await self._safe_send(ws, {"type": "hello", "you": None, "role": role})
        await self._safe_send(ws, {"type": "state", "state": to_public(self.state)})
        await self._safe_send(ws, {"type": "debug_sync", "on": self.debug_sync})
        if self.camera is not None:
            await self._safe_send(ws, self.camera.snapshot())
        for lane, sq in self.belt_positions.items():
            player = next((p for p in self.state.players if p.lane == lane), None)
            await self._safe_send(ws, {"type": "belt_position", "lane": lane, "square": sq,
                                       "game_square": player.position if player else None, "source": "belt"})
        await self._send_lobby(ws)
        if role == "host":
            await self._safe_send(ws, {"type": "join_links", "join_links": self.join_links()})

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
            attendance = action.get("action") == "set_attendance"
            if attendance:
                pid = action.get("target")
                action = {"action": "set_attendance", "target": pid, "kind": action.get("kind"),
                          "connected": isinstance(pid, str) and pid in self.seats}
            next_state, events = apply_action(self.state, actor, action, self.rng)
            next_tokens = dict(self.seat_tokens)
            if attendance and next_state.player(action["target"]).kind == "remote":
                if action["target"] not in next_tokens.values():
                    next_tokens[secrets.token_urlsafe(9)] = action["target"]
            seq = self.seq + 1
            if self.store and action.get("action") != "set_connected":   # presence is not game history
                if attendance:
                    # Persist the roster, access link and state in the same transaction.
                    self.store.append(self.game_id, seq, actor, action, events, to_public(next_state), seat_tokens=next_tokens)
                else:
                    self.store.append(self.game_id, seq, actor, action, events, to_public(next_state))
            self.state, self.seat_tokens, self.seq = next_state, next_tokens, seq
            self.updated_at = time.time()
            if attendance and self.state.player(action["target"]).kind == "in_person":
                old = self.seats.pop(action["target"], None)
                if old:
                    await self._safe_send(old, {"type": "error", "message": "Your seat is now attending in person.", "fatal": True})
                    try:
                        await old.close(code=4405)
                    except Exception:
                        pass
            if events or attendance:
                await self.broadcast({"type": "state", "state": to_public(self.state)})
                for ev in events:
                    await self.broadcast(ev)
            if attendance:
                # Join credentials belong only to the authenticated lobby host.
                for ws, role in list(self.viewers.items()):
                    if role == "host":
                        await self._safe_send(ws, {"type": "join_links", "join_links": self.join_links()})
        await self._react(events)
        return events

    async def _react(self, events: list[dict]) -> None:
        # Hardware first: the belt starts moving (and game-start resets happen) before any narration is awaited.
        for ev in events:
            if ev.get("type") == "game_started":   # tokens were placed on the start square by hand
                self._spawn(self.adapters.bridge.reset([p.lane for p in self.state.players]))
                if hasattr(self.adapters.bridge, "position"):   # the game starts wherever the belt really is
                    self._spawn(self._adopt_belt_at_start())
            if ev.get("type") == "move":
                self._spawn(self._run_move(ev))
        # One Mirror line per action rather than per event: a roll is told together with where it led
        # (while a token is moving its events are held and told with the arrival), and a turn that ends
        # is told together with who goes next.
        group = self._held_lines + events
        self._held_lines = []
        if any(ev.get("type") == "move" for ev in events):
            self._held_lines = group
            return
        try:
            line = await self.adapters.mirror.narrate(group, self.state) if group else None
        except Exception as e:
            log.info("mirror failed for %s: %s", [ev.get("type") for ev in group], e)
            line = None
        if line:
            await self.broadcast({"type": "mirror", "text": line})

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
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

    def has_board(self) -> bool:
        return "board" in self.viewers.values()

    async def handle_message(self, ws: Socket, actor: str, role: str, msg: dict) -> None:
        try:
            if role == "viewer":
                raise EngineError("this board view is read-only; use the host link or the board laptop")
            if msg.get("type") == "lobby_move":
                await self._move_lobby(ws, actor, role, msg)
                return
            if msg.get("action") == "set_attendance" and role != "host":
                raise EngineError("only the lobby host can change attendance")
            source = msg.get("source")
            if source is not None:
                if role != "board":
                    raise EngineError("only the board's dev panel can do that")
                adapter = {"vision": self.adapters.vision, "voice": self.adapters.voice}.get(source)
                if source == "voice" and not hasattr(adapter, "dev_input"):
                    # Muse voice is live, but the host may still answer by button or typed text.
                    from .adapters.voice import parse_answer
                    answer = msg.get("answer") or parse_answer(str(msg.get("text", "")))
                    if answer not in ("yes", "no"):
                        raise EngineError(f"couldn't hear a yes or no in {msg.get('text')!r}")
                    await self.dispatch(SYSTEM, {"action": "decision", "answer": answer})
                    return
                if adapter is None or not hasattr(adapter, "dev_input"):
                    raise EngineError(f"no dev input for {source} (are you running --sim?)")
                await adapter.dev_input(msg)
                return
            claimed = msg.get("player")
            if actor != SYSTEM and claimed is not None and claimed != actor:
                raise EngineError(f"you can only act as {actor}")
            if msg.get("action") == "board_roll" and not self.dev:
                raise EngineError("the on-screen dice are for dev mode; roll the real dice into the tray")
            if msg.get("action") == "say":
                return await self.say(actor, str(msg.get("text", "")))
            if msg.get("action") == "sync_belt":
                if role not in ("board", "host"):
                    raise EngineError("only the board or host page can sync from the belt")
                return await self.sync_belt()
            if msg.get("action") == "debug_sync":
                if role not in ("board", "host"):
                    raise EngineError("only the board or host page can change debug sync")
                return await self.set_debug_sync(bool(msg.get("on")))
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

    async def belt_reported(self, lane: int, square: int, source: str = "belt") -> None:
        """The physical side says where a token is. Everyone sees it; in debug mode the game adopts it
        (never while that token is mid-move, when the belt is the one still catching up)."""
        prev = self.belt_positions.get(lane)
        self.belt_positions[lane] = square
        player = next((p for p in self.state.players if p.lane == lane), None)
        game_square = player.position if player else None
        if prev != square or game_square != square:
            await self.broadcast({"type": "belt_position", "lane": lane, "square": square,
                                  "game_square": game_square, "source": source})
        if not (self.debug_sync and player) or game_square == square:
            return
        # Only between turns: while a landing is being resolved (offer, card, payment) or the belt is
        # still moving, the square is part of the pending action and must not be changed under it.
        if self.state.phase.value not in ("LOBBY", "WAITING_FOR_ROLL"):
            return
        try:
            await self.dispatch(SYSTEM, {"action": "set_position", "player": player.id, "square": square})
        except EngineError as e:
            log.info("debug sync: could not adopt belt square %s for lane %s: %s", square, lane, e)

    async def set_debug_sync(self, on: bool) -> None:
        self.debug_sync = on
        await self.broadcast({"type": "debug_sync", "on": on})
        if on:
            self.start_belt_poll()
            for lane, sq in list(self.belt_positions.items()):
                await self.belt_reported(lane, sq, source="poll")

    def start_belt_poll(self, interval: float = 3.0) -> None:
        """Ask the bridge where the physical token is every few seconds (for gateways that cannot push)."""
        bridge = self.adapters.bridge
        if self._poll_task or not hasattr(bridge, "position"):
            return

        async def poll():
            while True:
                try:
                    pos = await bridge.position()
                    if pos is not None:
                        await self.belt_reported(1, pos, source="poll")
                except Exception as e:                      # keep polling through tunnel hiccups
                    log.info("belt poll: %s", e)
                await asyncio.sleep(interval)
        self._poll_task = asyncio.create_task(poll())

    async def sync_belt(self) -> None:
        """Debug: ask the belt where its token really is and put the game's token there."""
        bridge = self.adapters.bridge
        if not hasattr(bridge, "position"):
            raise EngineError("this bridge cannot report a position (run with --bridge step)")
        pos = await bridge.position()
        if pos is None:
            raise EngineError("the belt gateway did not answer with a position")
        for p in self.state.players:            # the gateway reports track 1 only
            if p.lane == 1:
                await self.dispatch(SYSTEM, {"action": "set_position", "player": p.id, "square": pos})

    async def _adopt_belt_at_start(self) -> None:
        try:
            await self.sync_belt()
        except EngineError as e:
            log.info("game start: could not adopt the belt's square: %s", e)

    async def scene(self, text: str) -> None:
        """What the Mirror sees at the table, for the players who are not there."""
        await self.broadcast({"type": "mirror", "kind": "scene", "text": text})
    async def _move_lobby(self, ws: Socket, actor: str, role: str, msg: dict) -> None:
        async with self.lock:
            if self.state.phase != Phase.LOBBY:
                raise EngineError("courtyard movement is only available while waiting in the lobby")
            pid = self.state.players[0].id if actor == SYSTEM else actor
            if (role == "seat" and self.seats.get(pid) is not ws) or (role != "seat" and ws not in self.viewers):
                raise EngineError("this lobby connection is no longer active")
            if msg.get("player", pid) != pid:
                raise EngineError(f"you can only move {pid}'s lobby character")
            x, y, jump = msg.get("x"), msg.get("y"), msg.get("jump", False)
            if (type(x) not in (int, float) or type(y) not in (int, float)
                    or not math.isfinite(x) or not math.isfinite(y)
                    or not 0 <= x <= 1 or not 0 <= y <= 1 or type(jump) is not bool):
                raise EngineError("lobby coordinates must be finite numbers from 0 to 1; jump must be true or false")
            now = time.monotonic()
            do_jump = jump and now - self._lobby_jumped.get(pid, -math.inf) >= .4
            if not do_jump and now - self._lobby_sent.get(pid, -math.inf) < .05:
                return
            self._lobby_sent[pid] = now
            previous = self.lobby_positions[pid]
            position = {"x": x, "y": y, "jump": previous["jump"] + int(do_jump)}
            self.lobby_positions[pid] = position
            if do_jump:
                self._lobby_jumped[pid] = now
            await self.broadcast({"type": "lobby_motion", "player": pid, **position})

    # --- sending -----------------------------------------------------------

    async def broadcast(self, msg: dict) -> None:
        if msg.get("type") == "mirror":
            tts.allow(msg.get("text", ""))
        for ws in self.all_sockets():
            await self._safe_send(ws, msg)

    @staticmethod
    async def _safe_send(ws: Socket, msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            pass  # socket is closing; its disconnect handler cleans up
