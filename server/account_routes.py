"""Account HTTP endpoints; no passwords or session tokens enter game events."""
from __future__ import annotations

from collections import defaultdict, deque
import hmac
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from .accounts import SESSION_SECONDS

COOKIE = 'mm_player_session'


class Credentials(BaseModel):
    username: str = Field(max_length=32)
    password: SecretStr
    display_name: str = Field(default='', max_length=40)


class SeatAccount(BaseModel):
    host: str | None = None
    seat: str | None = None
    use_current: bool = False
    expected_account: str | None = None
    create: bool = False
    username: str = Field(default='', max_length=32)
    password: SecretStr | None = None
    display_name: str = Field(default='', max_length=40)


def same_origin(request: Request):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc.lower() != request.headers.get('host', '').lower():
        raise HTTPException(403, 'Use the account controls from this game’s own page.')


def install_account_routes(app, store, rooms, is_local):
    attempts = defaultdict(deque)

    def limit(request):
        same_origin(request)
        now = time.monotonic()
        for key in list(attempts):
            if not attempts[key] or attempts[key][-1] < now - 60:
                del attempts[key]
        if len(attempts) > 1000:
            del attempts[min(attempts, key=lambda key: attempts[key][-1])]
        forwarded = request.headers.get('cf-connecting-ip') or (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
        key = forwarded or (request.client.host if request.client else 'local')
        bucket = attempts[key]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 40:
            raise HTTPException(429, 'Too many attempts. Please wait a minute.', headers={'Retry-After': '60'})
        bucket.append(now)

    def current(request, required=False):
        user = store.accounts.current(request.cookies.get(COOKIE))
        if required and user is None:
            raise HTTPException(401, 'Sign in to see your saved games and overall stats.')
        return user

    def signed_in(user, request):
        store.accounts.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'account': user}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(COOKIE, store.accounts.new_session(user['id']), max_age=SESSION_SECONDS,
                            httponly=True, samesite='lax', secure=request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https')
        return response

    @app.get('/api/auth/me')
    async def me(request: Request):
        return JSONResponse({'account': current(request)}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/auth/register')
    async def register(body: Credentials, request: Request):
        limit(request)
        try:
            user = store.accounts.create(body.username, body.password.get_secret_value(), body.display_name)
        except ValueError as e:
            raise HTTPException(409 if 'already' in str(e) else 422, str(e)) from None
        return signed_in(user, request)

    @app.post('/api/auth/login')
    async def login(body: Credentials, request: Request):
        limit(request)
        user = store.accounts.authenticate(body.username, body.password.get_secret_value())
        if not user:
            raise HTTPException(401, 'Incorrect username or password.')
        return signed_in(user, request)

    @app.post('/api/auth/logout')
    async def logout(request: Request):
        same_origin(request)
        store.accounts.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'ok': True}, headers={'Cache-Control': 'no-store'})
        response.delete_cookie(COOKIE)
        return response

    @app.get('/api/account/games')
    async def saved_games(request: Request):
        user = current(request, True)
        return JSONResponse({'games': store.accounts.games(user['id'])}, headers={'Cache-Control': 'no-store'})

    @app.get('/api/account/stats')
    async def lifetime_stats(request: Request):
        user = current(request, True)
        return JSONResponse({'account': user, 'stats': store.accounts.stats(user['id'])}, headers={'Cache-Control': 'no-store'})

    def can_manage(game, user, request, host=None):
        return is_local(request) or (bool(host) and hmac.compare_digest(host, game['host_token'])) or (user is not None and store.accounts.owner(game['game_id']) == user['id'])

    @app.get('/api/games/{game_id}/accounts')
    async def roster_accounts(game_id: str, request: Request, host: str | None = None, seat: str | None = None):
        game = store.game(game_id)
        if not game:
            raise HTTPException(404, 'No such game.')
        user = current(request)
        roster = store.accounts.roster(game_id)
        if not (can_manage(game, user, request, host) or (seat and seat in game['seat_tokens']) or
                (user and any(p['id'] == user['id'] for p in roster.values()))):
            raise HTTPException(403, 'Use your game invitation or sign in to a linked account.')
        return JSONResponse({'players': roster}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/games/{game_id}/players/{player_id}/account')
    async def link_account(game_id: str, player_id: str, body: SeatAccount, request: Request):
        limit(request)
        game = store.game(game_id)
        if not game or not any(p['id'] == player_id for p in game['state']['players']):
            raise HTTPException(404, 'No such player in this game.')
        user = current(request)
        if not (can_manage(game, user, request, body.host) or (body.seat and game['seat_tokens'].get(body.seat) == player_id)):
            raise HTTPException(403, 'Only the host or this seat’s invitation can link its account.')
        if body.use_current:
            candidate = current(request, True)
            if body.expected_account and candidate['id'] != body.expected_account:
                raise HTTPException(409, 'The signed-in account changed. Reopen this account link before trying again.')
        elif body.create:
            candidate = None
        else:
            candidate = store.accounts.authenticate(body.username, body.password.get_secret_value() if body.password else '')
            if not candidate:
                raise HTTPException(401, 'Incorrect username or password.')

        def apply_link():
            latest = store.game(game_id)
            old = store.accounts.roster(game_id).get(player_id)
            if old and latest['state']['phase'] != 'LOBBY' and (candidate is None or old['id'] != candidate['id']):
                raise HTTPException(409, 'This started game is already linked to another account.')
            account = candidate
            if account is None:
                try:
                    account = store.accounts.create(body.username, body.password.get_secret_value() if body.password else '', body.display_name)
                except ValueError as e:
                    raise HTTPException(409 if 'already' in str(e) else 422, str(e)) from None
            try:
                store.accounts.link(game_id, player_id, account['id'], lobby=latest['state']['phase'] == 'LOBBY')
            except ValueError as e:
                raise HTTPException(409, str(e)) from None
            return {'account': account}

        room = rooms.get(game_id)
        if room:
            async with room.lock:
                return apply_link()
        return apply_link()
