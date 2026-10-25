"""Local player accounts and seat identities, kept separate from game rules."""
from __future__ import annotations

from collections import Counter
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

ACCOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_sessions (
 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS game_accounts (
 game_id TEXT NOT NULL REFERENCES games(game_id), player_id TEXT NOT NULL,
 account_id TEXT NOT NULL REFERENCES accounts(id), PRIMARY KEY(game_id, player_id), UNIQUE(game_id, account_id)
);
CREATE INDEX IF NOT EXISTS game_accounts_by_account ON game_accounts(account_id, game_id);
CREATE TABLE IF NOT EXISTS game_owners (
 game_id TEXT PRIMARY KEY REFERENCES games(game_id), account_id TEXT NOT NULL REFERENCES accounts(id)
);
"""
SESSION_SECONDS = 30 * 24 * 60 * 60


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64)
    return f'scrypt${salt}${digest.hex()}'


def password_matches(password: str, encoded: str) -> bool:
    try:
        _, salt, _ = encoded.split('$')
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = password_hash('not-a-user-password', '00' * 16)


class Accounts:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript(ACCOUNT_SCHEMA)

    @staticmethod
    def public(row):
        return {k: row[k] for k in ('id', 'username', 'display_name', 'created_at')} if row else None

    def create(self, username: str, password: str, display_name: str = '') -> dict:
        username = username.strip()
        if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username):
            raise ValueError('Use 3–32 letters, numbers or underscores for your username.')
        if not 8 <= len(password) <= 128:
            raise ValueError('Use a password with 8–128 characters.')
        account_id, now = secrets.token_urlsafe(15), time.time()
        try:
            with self.db:
                self.db.execute('INSERT INTO accounts VALUES (?, ?, ?, ?, ?)',
                                (account_id, username, (display_name.strip() or username)[:40], password_hash(password), now))
        except sqlite3.IntegrityError:
            raise ValueError('That username is already in use.') from None
        return self.public(self.db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone())

    def authenticate(self, username: str, password: str) -> dict | None:
        row = self.db.execute('SELECT * FROM accounts WHERE username=? COLLATE NOCASE', (username.strip(),)).fetchone()
        valid = password_matches(password[:128], row['password_hash'] if row else DUMMY_HASH)
        return self.public(row) if row and valid and len(password) <= 128 else None

    def new_session(self, account_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute('DELETE FROM account_sessions WHERE expires_at<?', (time.time(),))
            self.db.execute('INSERT INTO account_sessions VALUES (?, ?, ?)',
                            (hashlib.sha256(token.encode()).hexdigest(), account_id, time.time() + SESSION_SECONDS))
        return token

    def current(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.db.execute('SELECT a.* FROM accounts a JOIN account_sessions s ON s.account_id=a.id '
                              'WHERE s.token_hash=? AND s.expires_at>?',
                              (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return self.public(row)

    def logout(self, token: str | None):
        if token:
            with self.db:
                self.db.execute('DELETE FROM account_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def owner(self, game_id: str) -> str | None:
        row = self.db.execute('SELECT account_id FROM game_owners WHERE game_id=?', (game_id,)).fetchone()
        return row[0] if row else None

    def roster(self, game_id: str) -> dict:
        rows = self.db.execute('SELECT g.player_id, a.* FROM game_accounts g JOIN accounts a ON a.id=g.account_id WHERE g.game_id=?', (game_id,))
        return {r['player_id']: self.public(r) for r in rows}

    def link(self, game_id: str, player_id: str, account_id: str, *, lobby: bool):
        old = self.roster(game_id).get(player_id)
        if old and old['id'] != account_id and not lobby:
            raise ValueError('This seat already belongs to another account. Started games keep their original accounts.')
        try:
            with self.db:
                self.db.execute('INSERT INTO game_accounts VALUES (?, ?, ?) ON CONFLICT(game_id, player_id) '
                                'DO UPDATE SET account_id=excluded.account_id', (game_id, player_id, account_id))
                if player_id == 'p1':
                    self.db.execute('INSERT OR IGNORE INTO game_owners VALUES (?, ?)', (game_id, account_id))
        except sqlite3.IntegrityError:
            raise ValueError('This account is already linked to another player in this game.') from None

    def games(self, account_id: str) -> list[dict]:
        rows = self.db.execute('SELECT g.game_id, a.player_id FROM games g '
                               'LEFT JOIN game_accounts a ON a.game_id=g.game_id AND a.account_id=? '
                               'LEFT JOIN game_owners o ON o.game_id=g.game_id '
                               'WHERE a.account_id=? OR o.account_id=? ORDER BY g.updated_at DESC', (account_id, account_id, account_id)).fetchall()
        result = []
        for row in rows:
            game = self.store.game(row['game_id'])
            if not game:
                continue
            state, pid = game['state'], row['player_id']
            p = next((p for p in state['players'] if p['id'] == pid), None)
            if pid is not None and not p:
                continue
            host = self.owner(game['game_id']) == account_id
            resume = None
            host_url = f'/?game={game["game_id"]}&host={game["host_token"]}' if host else None
            if game['status'] not in ('over', 'abandoned'):
                if host:
                    resume = host_url if state['phase'] == 'LOBBY' else f'/board/{game["game_id"]}'
                elif p and p['kind'] == 'remote':
                    token = next((token for token, player in game['seat_tokens'].items() if player == pid), None)
                    if token:
                        resume = f'/game/{game["game_id"]}/join/{token}'
                else:
                    resume = f'/board/{game["game_id"]}'
            stats = self.store.stats(game['game_id'])
            personal = next((p for p in stats['players'] if p['id'] == pid), None)
            result.append({'game_id': game['game_id'], 'status': game['status'], 'phase': state['phase'],
                           'turn': state.get('turn', 0), 'winner': state.get('winner'), 'players': state['players'],
                           'created_at': game['created_at'], 'updated_at': game['updated_at'], 'player_id': pid,
                           'board_url': f'/board/{game["game_id"]}', 'host_url': host_url, 'resume_url': resume,
                           'stats': personal})
        return result

    def stats(self, account_id: str) -> dict:
        games = self.games(account_id)
        participating = [g for g in games if g['stats'] is not None]
        started = [g for g in participating if g['phase'] != 'LOBBY']
        finished = [g for g in started if g['status'] == 'over']
        placements = [g['stats']['placement'] for g in finished if g['stats'] and g['stats']['placement'] is not None]
        decided = [g for g in finished if g['winner'] is not None]
        wins = sum(g['winner'] == g['player_id'] for g in decided)
        purchases = Counter()
        totals = {key: 0 for key in ('bought', 'rent_paid', 'rent_received', 'spent_on_property', 'salary', 'taxes', 'cards_drawn', 'turns', 'roll_count')}
        roll_sum, best_roll = 0, None
        for game in games:
            stats = game['stats']
            if not stats:
                continue
            for key in totals:
                totals[key] += stats[key]
            roll_sum += stats['roll_total']
            best_roll = max(best_roll or 0, stats['best_roll'] or 0) or None
            purchases.update({int(k): v for k, v in stats['property_purchases'].items()})
        return {**totals, 'games_played': len(started), 'completed_games': len(finished), 'active_games': sum(g['status'] == 'active' for g in started),
                'waiting_games': len(participating)-len(started), 'wins': wins,
                'win_rate': round(100*wins/len(decided), 1) if decided else None, 'result_games': len(decided),
                'average_placement': round(sum(placements)/len(placements), 2) if placements else None,
                'ranked_games': len(placements), 'avg_roll': round(roll_sum/totals['roll_count'], 2) if totals['roll_count'] else None,
                'best_roll': best_roll, 'most_bought_properties': self.store.top_properties(purchases), 'games': games}
