"""Identity, saved-session access and statistics drawn from actual event schemas."""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from server.engine import new_game, to_public
from server.main import create_app
from server.store import SCHEMA, Store

PASSWORD = 'storybook-test-password'
SPECS = [{'name': 'Grandma', 'kind': 'in_person'}, {'name': 'Maya', 'kind': 'remote'}, {'name': 'Leo', 'kind': 'remote'}]


def register(client, username='wanderer'):
    response = client.post('/api/auth/register', json={'username': username, 'password': PASSWORD, 'display_name': username.title()})
    assert response.status_code == 200
    return response.json()['account']


@pytest.fixture
def client():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        yield c


def test_registration_password_storage_sessions_and_logout(client):
    user = register(client)
    response = client.get('/api/auth/me')
    assert response.json()['account']['id'] == user['id']
    row = client.app.state.store.db.execute('SELECT password_hash FROM accounts').fetchone()
    assert row[0].startswith('scrypt$') and PASSWORD not in row[0]
    token = client.cookies.get('mm_player_session')
    saved = client.app.state.store.db.execute('SELECT token_hash FROM account_sessions').fetchone()[0]
    assert saved != token and token not in saved
    assert 'password' not in json.dumps(response.json())
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/account/stats').status_code == 401
    client.cookies.set('mm_player_session', token)
    assert client.get('/api/auth/me').json()['account'] is None
    bad = client.post('/api/auth/login', json={'username': 'wanderer', 'password': 'wrong'})
    missing = client.post('/api/auth/login', json={'username': 'unknown', 'password': 'wrong'})
    assert bad.status_code == missing.status_code == 401
    assert bad.json() == missing.json()
    login = client.post('/api/auth/login', json={'username': 'WANDERER', 'password': PASSWORD})
    assert login.status_code == 200
    assert 'httponly' in login.headers['set-cookie'].lower()
    assert 'samesite=lax' in login.headers['set-cookie'].lower()
    assert client.post('/api/auth/register', json={'username': 'Wanderer', 'password': PASSWORD}).status_code == 409


def test_auth_validation_expiration_and_cross_origin(client):
    assert client.post('/api/auth/register', json={'username': 'bad name!', 'password': PASSWORD}).status_code == 422
    assert client.post('/api/auth/register', json={'username': 'good_name', 'password': 'short'}).status_code == 422
    register(client)
    assert client.post('/api/auth/logout', headers={'Origin': 'https://different.example'}).status_code == 403
    assert client.get('/api/auth/me').json()['account'] is not None
    with client.app.state.store.db:
        client.app.state.store.db.execute('UPDATE account_sessions SET expires_at=?', (time.time()-1,))
    assert client.get('/api/auth/me').json()['account'] is None
    assert client.get('/api/account/games').status_code == 401


def test_sign_in_attempts_are_limited(client):
    for _ in range(40):
        assert client.post('/api/auth/login', json={'username': 'unknown', 'password': 'wrong'}).status_code == 401
    limited = client.post('/api/auth/login', json={'username': 'unknown', 'password': 'wrong'})
    assert limited.status_code == 429 and limited.headers['retry-after'] == '60'


def test_owner_assignment_and_linking_other_players_do_not_switch_login(client):
    owner = register(client)
    game = client.post('/api/games', json={'players': SPECS}).json()
    gid = game['game_id']
    accounts = client.app.state.store.accounts
    assert accounts.roster(gid)['p1']['id'] == owner['id']
    friend = accounts.create('friend', PASSWORD)
    response = client.post(f'/api/games/{gid}/players/p2/account', json={'host': game['host_token'], 'username': 'friend', 'password': PASSWORD})
    assert response.status_code == 200 and response.json()['account']['id'] == friend['id']
    assert client.get('/api/auth/me').json()['account']['id'] == owner['id']
    duplicate = client.post(f'/api/games/{gid}/players/p2/account', json={'use_current': True})
    assert duplicate.status_code == 409
    assert accounts.roster(gid)['p2']['id'] == friend['id']
    stats = client.get('/api/account/stats').json()['stats']
    assert stats['games_played'] == 0 and stats['waiting_games'] == 1
    saved = client.get('/api/account/games').json()['games'][0]
    assert saved['game_id'] == gid and game['host_token'] in saved['resume_url']
    before = len(client.get('/api/games').json())
    client.get(saved['resume_url'])
    assert len(client.get('/api/games').json()) == before  # continuing never creates a new game
    assert PASSWORD not in json.dumps(client.get(f'/api/games/{gid}/history').json())


def test_remote_users_cannot_claim_others_or_read_their_accounts(client):
    owner = register(client, 'owner')
    game = client.post('/api/games', json={'players': SPECS}).json()
    gid = game['game_id']
    client.post('/api/auth/logout')
    other = register(client, 'other')
    remote = {'x-forwarded-for': '192.0.2.7'}
    assert client.get(f'/api/games/{gid}/accounts', headers=remote).status_code == 403
    assert client.post(f'/api/games/{gid}/players/p1/account', json={'use_current': True}, headers=remote).status_code == 403
    token = game['join_links']['p2'].rsplit('/', 1)[1]
    assert client.post(f'/api/games/{gid}/players/p1/account', json={'seat': token, 'use_current': True}, headers=remote).status_code == 403
    stale = client.post(f'/api/games/{gid}/players/p2/account', json={'seat': token, 'use_current': True, 'expected_account': owner['id']}, headers=remote)
    assert stale.status_code == 409
    linked = client.post(f'/api/games/{gid}/players/p2/account', json={'seat': token, 'use_current': True}, headers=remote)
    assert linked.status_code == 200
    private = client.get('/api/account/games', headers=remote).json()['games']
    assert len(private) == 1 and private[0]['player_id'] == 'p2'
    assert private[0]['host_url'] is None and game['host_token'] not in json.dumps(private)
    assert private[0]['resume_url'] == game['join_links']['p2']
    assert client.get('/api/account/stats', params={'account_id': owner['id']}).json()['account']['id'] == other['id']


def add_session(store, gid, mapping, *, purchases=None, elimination=None, winner=None, phase='GAME_OVER', alias=None):
    specs = [dict(p) for p in SPECS]
    if alias:
        specs[1]['name'] = alias
    state = to_public(new_game(gid, specs))
    store.create_game(gid, specs, None, 'host-'+gid, {'seat2-'+gid: 'p2', 'seat3-'+gid: 'p3'}, state)
    for pid, account_id in mapping.items():
        store.accounts.link(gid, pid, account_id, lobby=True)
    events = []
    if phase != 'LOBBY':
        events = [{'type': 'game_started'}, {'type': 'turn_started', 'player': 'p1'},
                  {'type': 'rolled', 'player': next(iter(mapping), 'p1'), 'value': 6, 'dice': [2, 4]}]
    for pid, ids in (purchases or {}).items():
        for square in ids:
            events.extend([{'type': 'paid', 'from': pid, 'to': 'bank', 'amount': 100, 'reason': 'buy', 'square': square},
                           {'type': 'bought', 'player': pid, 'square': square}])
    for pid in elimination or []:
        events.append({'type': 'bankrupt', 'player': pid, 'owed': 200})
        next(p for p in state['players'] if p['id'] == pid)['bankrupt'] = True
    state['phase'], state['winner'], state['turn'] = phase, winner, int(phase != 'LOBBY')
    if winner:
        events.append({'type': 'game_over', 'winner': winner})
    store.append(gid, 1, 'system', {'action': 'recorded_history'}, events, state)


def test_lifetime_stats_join_by_identity_rank_by_elimination_and_exclude_unfinished_results():
    store = Store()
    a = store.accounts.create('player_a', PASSWORD)
    b = store.accounts.create('player_b', PASSWORD)
    add_session(store, 'one', {'p1': a['id']}, purchases={'p1': [6]}, elimination=['p3', 'p2'], winner='p1')
    add_session(store, 'two', {'p2': a['id']}, purchases={'p2': [6, 8]}, elimination=['p1', 'p2'], winner='p3', alias='Different name')
    add_session(store, 'active', {'p1': a['id']}, purchases={'p1': [8]}, phase='WAITING_FOR_ROLL')
    add_session(store, 'waiting', {'p1': a['id']}, phase='LOBBY')
    add_session(store, 'same_name', {'p1': b['id']}, purchases={'p1': [39]}, elimination=['p2', 'p3'], winner='p1')
    stats = store.accounts.stats(a['id'])
    assert stats['games_played'] == 3 and stats['completed_games'] == 2 and stats['waiting_games'] == 1
    assert stats['average_placement'] == 1.5 and stats['ranked_games'] == 2
    assert stats['wins'] == 1 and stats['win_rate'] == 50
    assert stats['bought'] == 4 and stats['spent_on_property'] == 400
    assert [(p['square'], p['count']) for p in stats['most_bought_properties']] == [(6, 2), (8, 2)]
    assert store.stats('one')['players'][2]['placement'] == 3
    assert store.stats('two')['players'][1]['placement'] == 2
    assert store.accounts.stats(a['id']) == stats  # reading stats never increments them
    # An incomplete old result does not invent a rank from money or roster order.
    add_session(store, 'missing', {'p2': a['id']}, winner='p1')
    stats = store.accounts.stats(a['id'])
    assert stats['completed_games'] == 3 and stats['ranked_games'] == 2 and stats['average_placement'] == 1.5
    assert stats['win_rate'] == 33.3
    add_session(store, 'unknown_winner', {'p1': a['id']})
    stats = store.accounts.stats(a['id'])
    assert stats['completed_games'] == 4 and stats['result_games'] == 3
    assert stats['win_rate'] == 33.3 and stats['average_placement'] == 1.5
    store.close()


def test_existing_databases_and_account_sessions_survive_restart(tmp_path):
    db = tmp_path/'saved.db'
    connection = sqlite3.connect(db)
    connection.executescript(SCHEMA)  # pre-account database
    connection.close()
    with TestClient(create_app(sim=True, db_path=db)) as c:
        account = register(c)
        cookie = c.cookies.get('mm_player_session')
        game = c.post('/api/games', json={'players': SPECS}).json()
        gid = game['game_id']
    with TestClient(create_app(sim=True, db_path=db)) as c:
        c.cookies.set('mm_player_session', cookie)
        assert c.get('/api/auth/me').json()['account']['id'] == account['id']
        games = c.get('/api/account/games').json()['games']
        assert [g['game_id'] for g in games] == [gid]
        assert games[0]['resume_url'].endswith(game['host_token'])
        assert c.get(f'/api/games/{gid}/stats').json()['players'][0]['name'] == 'Grandma'


def test_finished_sessions_can_be_linked_once_but_not_reassigned(client):
    a = register(client, 'first')
    b = client.app.state.store.accounts.create('second', PASSWORD)
    add_session(client.app.state.store, 'legacy', {}, purchases={'p2': [6]}, elimination=['p1', 'p2'], winner='p3')
    link = client.post('/api/games/legacy/players/p2/account', json={'use_current': True})
    assert link.status_code == 200
    assert client.get('/api/account/stats').json()['stats']['bought'] == 1
    changed = client.post('/api/games/legacy/players/p2/account', json={'username': b['username'], 'password': PASSWORD})
    assert changed.status_code == 409
    assert client.app.state.store.accounts.roster('legacy')['p2']['id'] == a['id']


def test_host_can_continue_after_assigning_their_seat_to_someone_else(client):
    owner = register(client)
    game = client.post('/api/games', json={'players': SPECS}).json()
    other = client.app.state.store.accounts.create('other_player', PASSWORD)
    linked = client.post(f'/api/games/{game["game_id"]}/players/p1/account', json={'username': other['username'], 'password': PASSWORD})
    assert linked.status_code == 200
    games = client.get('/api/account/games').json()['games']
    assert games[0]['game_id'] == game['game_id'] and games[0]['stats'] is None
    assert games[0]['resume_url'].endswith(game['host_token'])
    stats = client.get('/api/account/stats').json()['stats']
    assert stats['games_played'] == stats['waiting_games'] == 0
    assert client.app.state.store.accounts.owner(game['game_id']) == owner['id']


def test_signing_in_rotates_and_revokes_the_previous_device_session(client):
    register(client, 'first_account')
    old = client.cookies.get('mm_player_session')
    register(client, 'second_account')
    assert client.cookies.get('mm_player_session') != old
    assert client.app.state.store.accounts.current(old) is None

@pytest.mark.parametrize('password', ['Abcd1234', 'two words 123', 'Héllo世界123', '😀' * 8])
def test_create_from_player_link_accepts_password_and_can_sign_in(client, password):
    game = client.post('/api/games', json={'players': SPECS}).json()
    path = f'/api/games/{game["game_id"]}/players/p2/account'
    credentials = {'username': 'new_linked_player', 'password': password}
    # Existing-account mode must not silently create an account.
    assert client.post(path, json={**credentials, 'create': False}).status_code == 401
    created = client.post(path, json={**credentials, 'create': True})
    assert created.status_code == 200
    account_id = created.json()['account']['id']
    assert client.app.state.store.accounts.roster(game['game_id'])['p2']['id'] == account_id
    logged_in = client.post('/api/auth/login', json=credentials)
    assert logged_in.status_code == 200
    assert logged_in.json()['account']['id'] == account_id


def test_player_link_creation_rejects_short_password_without_creating_account(client):
    game = client.post('/api/games', json={'players': SPECS}).json()
    response = client.post(f'/api/games/{game["game_id"]}/players/p2/account',
                           json={'username': 'new_linked_player', 'password': 'short', 'create': True})
    assert response.status_code == 422
    assert '8–128' in response.json()['detail']
    assert client.app.state.store.accounts.authenticate('new_linked_player', 'short') is None
    assert 'p2' not in client.app.state.store.accounts.roster(game['game_id'])
