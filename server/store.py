"""SQLite persistence: every action a room applies is appended with a snapshot of
the resulting state, so a game survives a server restart and its history is
queryable for stats ("Chronicles").

    games    one row per game: roster, tokens, status
    actions  one row per applied action: who, what, the events it produced,
             and the full state afterwards (small: a few KB)

Resume = load the last snapshot. Stats = fold over the events.
"""
from __future__ import annotations

import json
from collections import Counter
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id     TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    specs       TEXT NOT NULL,
    seed        INTEGER,
    host_token  TEXT NOT NULL,
    seat_tokens TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS actions (
    game_id  TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    ts       REAL NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    events   TEXT NOT NULL,
    state    TEXT NOT NULL,
    PRIMARY KEY (game_id, seq)
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        from .accounts import Accounts
        self.accounts = Accounts(self)

    def close(self) -> None:
        self.db.close()

    # --- games -------------------------------------------------------------

    def create_game(self, game_id: str, specs: list[dict], seed: int | None,
                    host_token: str, seat_tokens: dict[str, str], state: dict, *, owner_id: str | None = None) -> None:
        now = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO games (game_id, created_at, updated_at, specs, seed, host_token, seat_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_id, now, now, json.dumps(specs), seed, host_token, json.dumps(seat_tokens)))
            self.db.execute(
                "INSERT INTO actions (game_id, seq, ts, actor, action, events, state) VALUES (?, 0, ?, 'system', ?, '[]', ?)",
                (game_id, now, json.dumps({"action": "new_game"}), json.dumps(state)))
            if owner_id:
                self.db.execute('INSERT INTO game_owners VALUES (?, ?)', (game_id, owner_id))
                self.db.execute('INSERT INTO game_accounts VALUES (?, ?, ?)', (game_id, state['players'][0]['id'], owner_id))

    def append(self, game_id: str, seq: int, actor: str, action: dict, events: list[dict], state: dict,
               *, seat_tokens: dict[str, str] | None = None) -> None:
        now = time.time()
        status = "over" if state.get("phase") == "GAME_OVER" else "active"
        with self.db:
            self.db.execute(
                "INSERT INTO actions (game_id, seq, ts, actor, action, events, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_id, seq, now, actor, json.dumps(action), json.dumps(events), json.dumps(state)))
            self.db.execute("UPDATE games SET updated_at = ?, status = ? WHERE game_id = ?", (now, status, game_id))
            if seat_tokens is not None:
                specs = [{"name": p["name"], "kind": p["kind"]} for p in state["players"]]
                self.db.execute("UPDATE games SET specs = ?, seat_tokens = ? WHERE game_id = ?",
                                (json.dumps(specs), json.dumps(seat_tokens), game_id))

    def set_status(self, game_id: str, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE games SET status = ?, updated_at = ? WHERE game_id = ?", (status, time.time(), game_id))

    def games(self, status: str | None = None) -> list[dict]:
        """Newest first. Each: game_id, created_at, updated_at, specs, seed, host_token, seat_tokens, status, seq, state."""
        q = ("SELECT g.*, a.seq, a.state FROM games g JOIN actions a ON a.game_id = g.game_id "
             "WHERE a.seq = (SELECT MAX(seq) FROM actions WHERE game_id = g.game_id)")
        args: tuple = ()
        if status:
            q += " AND g.status = ?"
            args = (status,)
        q += " ORDER BY g.updated_at DESC"
        out = []
        for r in self.db.execute(q, args):
            d = dict(r)
            d["specs"] = json.loads(d["specs"])
            d["seat_tokens"] = json.loads(d["seat_tokens"])
            d["state"] = json.loads(d["state"])
            out.append(d)
        return out

    def game(self, game_id: str) -> dict | None:
        row = self.db.execute("SELECT g.*, a.seq, a.state FROM games g JOIN actions a ON a.game_id=g.game_id "
                              "WHERE g.game_id=? AND a.seq=(SELECT MAX(seq) FROM actions WHERE game_id=g.game_id)", (game_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ('specs', 'seat_tokens', 'state'):
            result[key] = json.loads(result[key])
        return result

    @staticmethod
    def top_properties(counts) -> list[dict]:
        if not counts:
            return []
        from .rules.monopoly import square
        top = max(counts.values())
        return [{'square': int(index), 'name': square(int(index))['name'], 'count': count}
                for index, count in sorted(counts.items(), key=lambda item: int(item[0])) if count == top]

    # --- history -----------------------------------------------------------

    def history(self, game_id: str) -> list[dict]:
        """Every applied action in order: {seq, ts, actor, action, events}. Seq 0 is creation."""
        rows = self.db.execute(
            "SELECT seq, ts, actor, action, events FROM actions WHERE game_id = ? ORDER BY seq", (game_id,))
        return [{"seq": r["seq"], "ts": r["ts"], "actor": r["actor"],
                 "action": json.loads(r["action"]), "events": json.loads(r["events"])} for r in rows]

    def events(self, game_id: str) -> list[dict]:
        return [ev for row in self.history(game_id) for ev in row["events"]]

    def stats(self, game_id: str, state: dict | None = None) -> dict:
        """Per-player totals folded from the event log, plus a few game-wide numbers."""
        state = state or (self.game(game_id) or {}).get("state") or {}
        players = {p["id"]: {"id": p["id"], "name": p["name"], "balance": p["balance"],
                             "properties": len(p["properties"]), "bankrupt": p["bankrupt"],
                             "turns": 0, "rolls": [], "rent_paid": 0, "rent_received": 0,
                             "bought": 0, "spent_on_property": 0, "salary": 0, "cards_drawn": 0,
                             "taxes": 0, "jailed": 0, "property_purchases": {}, "placement": None}
                   for p in state.get("players", [])}
        squares_landed: dict[str, int] = {}
        n_turns = 0
        eliminated = []
        for ev in self.events(game_id):
            t, p = ev.get("type"), players.get(ev.get("player") or "")
            if t == "turn_started" and p:
                p["turns"] += 1
                n_turns += 1
            elif t == "rolled" and p:
                p["rolls"].append(ev["value"])
            elif t == "landed":
                squares_landed[str(ev["square"])] = squares_landed.get(str(ev["square"]), 0) + 1
            elif t == "paid":
                payer, payee, amt, why = players.get(ev["from"]), players.get(ev["to"]), ev["amount"], ev.get("reason")
                if why == "rent":
                    if payer:
                        payer["rent_paid"] += amt
                    if payee:
                        payee["rent_received"] += amt
                elif why == "buy" and payer:
                    payer["spent_on_property"] += amt
                elif why == "tax" and payer:
                    payer["taxes"] += amt
            elif t == "bought" and p:
                p["bought"] += 1
                key = str(ev['square'])
                p['property_purchases'][key] = p['property_purchases'].get(key, 0) + 1
            elif t == "bankrupt" and p and p['id'] not in eliminated:
                eliminated.append(p['id'])
            elif t == "salary" and p:
                p["salary"] += ev["amount"]
            elif t == "card_drawn" and p:
                p["cards_drawn"] += 1
            elif t == "jailed" and p:
                p["jailed"] += 1
        for index, pid in enumerate(eliminated):
            players[pid]['placement'] = len(players) - index
        if state.get('phase') == 'GAME_OVER' and state.get('winner') in players:
            players[state['winner']]['placement'] = 1
        all_purchases = Counter()
        for p in players.values():
            all_purchases.update(p['property_purchases'])
            p['most_bought_properties'] = self.top_properties(p['property_purchases'])
            rolls = p.pop("rolls")
            p["roll_count"] = len(rolls)
            p["roll_total"] = sum(rolls)
            p["avg_roll"] = round(sum(rolls) / len(rolls), 2) if rolls else None
            p["best_roll"] = max(rolls) if rolls else None
        richest = max(players.values(), key=lambda p: p["balance"], default=None)
        top_square = max(squares_landed.items(), key=lambda kv: kv[1], default=None)
        return {
            "game_id": game_id,
            "turns": n_turns,
            "phase": state.get("phase"),
            "winner": state.get("winner"),
            "richest": richest["id"] if richest else None,
            "most_landed_square": int(top_square[0]) if top_square else None,
            "players": list(players.values()),
            "most_bought_properties": self.top_properties(all_purchases),
        }
