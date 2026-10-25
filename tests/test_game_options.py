"""Host-only reset/end/delete, persistence and aggregate-stat cleanup."""
import pytest
from fastapi.testclient import TestClient
from server.main import create_app
from server.engine import SYSTEM, EngineError, Phase
from tests.test_accounts import add_session, PASSWORD
from tests.test_store import PLAYERS


@pytest.fixture
def client():
    with TestClient(create_app(sim=True, move_delay=0)) as client:
        yield client


@pytest.mark.parametrize('operation', ['reset', 'abandon', 'delete'])
def test_options_require_host_and_same_origin(client, operation):
    game = client.post('/api/games', json={'players': PLAYERS}).json()
    url = f'/api/games/{game["game_id"]}/{operation}'
    for invalid in ('wrong', 'invalid-🔑'):
        assert client.post(url, params={'host': invalid}).status_code == 403
    assert client.post(url, params={'host': game['host_token']}, headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.app.state.store.game(game['game_id'])['status'] == 'active'


def test_reset_clears_progress_keeps_roster_accounts_and_invitations(tmp_path):
    db = tmp_path / 'games.db'
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as client:
        store = client.app.state.store
        account = store.accounts.create('player', PASSWORD)
        add_session(store, 'reset_fixture', {'p1': account['id']}, purchases={'p1': [6]}, phase='WAITING_FOR_ROLL')
        old = store.game('reset_fixture')
        assert store.accounts.stats(account['id'])['bought'] == 1
        result = client.post('/api/games/reset_fixture/reset', params={'host': old['host_token']})
        assert result.status_code == 200
        saved = store.game('reset_fixture')
        assert saved['status'] == 'active' and saved['seq'] == 0
        assert saved['state']['phase'] == 'LOBBY' and saved['state']['turn'] == 0
        assert all(p['balance'] == 1500 and p['position'] == 0 and not p['properties'] for p in saved['state']['players'])
        assert saved['host_token'] == old['host_token'] and saved['seat_tokens'] == old['seat_tokens']
        assert [p['name'] for p in saved['state']['players']] == [p['name'] for p in old['state']['players']]
        assert store.accounts.roster('reset_fixture')['p1']['id'] == account['id']
        assert store.accounts.stats(account['id'])['bought'] == 0
        assert len(store.history('reset_fixture')) == 1
        assert 'host=' in result.json()['host_url']
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as client:
        assert client.app.state.rooms['reset_fixture'].state.phase == Phase.LOBBY
        assert client.app.state.store.game('reset_fixture')['seat_tokens'] == old['seat_tokens']


def test_reset_updates_connected_room_without_changing_links(client):
    game = client.post('/api/games', json={'players': PLAYERS}).json()
    gid = game['game_id']
    room = client.app.state.rooms[gid]
    room.state.players[0].balance = 99
    with client.websocket_connect(f'/ws/{gid}?role=board') as ws:
        ws.receive_json()  # hello
        ws.receive_json()  # state
        assert client.post(f'/api/games/{gid}/reset', params={'host': game['host_token']}).status_code == 200
        for _ in range(10):
            message = ws.receive_json()
            if message['type'] == 'state':
                assert message['state']['players'][0]['balance'] == 1500
                break
        else:
            pytest.fail('reset state was not broadcast')
        assert client.app.state.rooms[gid] is room


@pytest.mark.parametrize('operation', ['reset', 'delete'])
def test_reset_and_delete_wait_for_pending_move(client, operation):
    game = client.post('/api/games', json={'players': PLAYERS}).json()
    room = client.app.state.rooms[game['game_id']]
    room.state.phase = Phase.MOVING
    room.state.pending = {'kind': 'move', 'player': 'p1', 'from': 0, 'to': 6}
    response = client.post(f'/api/games/{game["game_id"]}/{operation}', params={'host': game['host_token']})
    assert response.status_code == 409
    assert client.app.state.store.game(game['game_id']) is not None


def test_delete_removes_game_history_and_stats_but_keeps_accounts_and_other_games(tmp_path):
    db = tmp_path / 'games.db'
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as client:
        store = client.app.state.store
        account = store.accounts.create('player', PASSWORD)
        add_session(store, 'delete_fixture', {'p1': account['id']}, purchases={'p1': [6]}, phase='WAITING_FOR_ROLL')
        other = client.post('/api/games', json={'players': PLAYERS}).json()
        result = client.post('/api/games/delete_fixture/delete', params={'host': 'host-delete_fixture'})
        assert result.status_code == 200
        assert store.game('delete_fixture') is None and store.history('delete_fixture') == []
        assert store.accounts.authenticate('player', PASSWORD)['id'] == account['id']
        assert store.accounts.games(account['id']) == []
        assert store.accounts.stats(account['id'])['bought'] == 0
        assert store.game(other['game_id']) is not None
        assert client.get('/api/games/delete_fixture/stats').status_code == 404
    with TestClient(create_app(sim=True, move_delay=0, db_path=db)) as client:
        assert 'delete_fixture' not in client.app.state.rooms
        assert client.app.state.store.game('delete_fixture') is None


def test_ended_games_have_no_resume_url_and_reject_late_events(client):
    store = client.app.state.store
    account = store.accounts.create('player', PASSWORD)
    game = client.post('/api/games', json={'players': PLAYERS}).json()
    gid = game['game_id']
    store.accounts.link(gid, 'p1', account['id'], lobby=True)
    room = client.app.state.rooms[gid]
    assert client.post(f'/api/games/{gid}/abandon', params={'host': game['host_token']}).status_code == 200
    assert room.closed and store.game(gid)['status'] == 'abandoned'
    assert store.accounts.games(account['id'])[0]['resume_url'] is None
    import asyncio
    with pytest.raises(EngineError, match='ended'):
        asyncio.run(room.dispatch(SYSTEM, {'action': 'start', 'force': True}))
    assert client.post(f'/api/games/{gid}/reset', params={'host': game['host_token']}).status_code == 200
    assert client.app.state.rooms[gid].state.phase == Phase.LOBBY


def test_delete_closes_live_guests_without_recreating_game(client):
    game = client.post('/api/games', json={'players': PLAYERS}).json()
    gid = game['game_id']
    token = game['join_links']['p2'].split('/')[-1]
    with client.websocket_connect(f'/ws/{gid}?seat={token}') as ws:
        ws.receive_json()
        ws.receive_json()
        assert client.post(f'/api/games/{gid}/delete', params={'host': game['host_token']}).status_code == 200
        for _ in range(10):
            message = ws.receive_json()
            if message['type'] == 'error':
                assert message['fatal'] and 'deleted' in message['message']
                break
        assert client.app.state.store.game(gid) is None
    assert client.app.state.store.history(gid) == []
