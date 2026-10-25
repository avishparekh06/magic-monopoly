"""End-to-end: HTTP create + WebSocket join, takeover, broadcast, spoofing."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.main import create_app
from server.rooms import TAKEN_OVER

PLAYERS = [
    {"name": "Grandma", "kind": "in_person"},
    {"name": "Maya", "kind": "remote"},
    {"name": "Leo", "kind": "remote"},
]


@pytest.fixture
def client():
    with TestClient(create_app(sim=True, move_delay=0)) as c:
        yield c


@pytest.fixture
def game(client):
    r = client.post("/api/games", json={"players": PLAYERS})
    assert r.status_code == 200
    return r.json()


def seat_token(game, pid):
    return game["join_links"][pid].rsplit("/", 1)[1]


def recv_until(ws, pred, limit=30):
    """Read messages until one matches pred; return it."""
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message never arrived")


def is_state(msg):
    return msg["type"] == "state"


def connected(msg, pid):
    return next(p for p in msg["state"]["players"] if p["id"] == pid)["connected"]


def test_create_returns_links(client, game):
    assert set(game["join_links"]) == {"p2", "p3"}
    assert client.get(game["join_links"]["p2"]).status_code == 200      # kid page served
    assert client.get(game["board_url"]).status_code == 200
    r = client.get(f"/api/games/{game['game_id']}/links", params={"host": game["host_token"]})
    assert r.json() == game
    r = client.get(f"/api/games/{game['game_id']}/links", params={"host": "nope"})
    assert r.status_code == 403


def test_bad_roster_is_400(client):
    r = client.post("/api/games", json={"players": PLAYERS[1:]})
    assert r.status_code == 400


def test_bad_token_and_unknown_game_rejected(client, game):
    with client.websocket_connect(f"/ws/{game['game_id']}?seat=wrong") as ws:
        assert ws.receive_json()["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect("/ws/nosuchgame?role=board") as ws:
        assert "doesn't exist" in ws.receive_json()["message"]


def test_join_broadcasts_to_everyone_and_start(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?host={game['host_token']}") as host, \
         client.websocket_connect(f"/ws/{gid}?role=board") as board:
        for v in (host, board):
            assert v.receive_json()["type"] == "hello"
            assert not connected(v.receive_json(), "p2")

        with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p2')}") as maya:
            assert maya.receive_json() == {"type": "hello", "you": "p2", "role": "seat"}
            for ws in (maya, host, board):
                assert connected(recv_until(ws, is_state), "p2")

            # Leo hasn't joined: start refused, error goes only to the host.
            host.send_json({"action": "start"})
            assert "Leo" in recv_until(host, lambda m: m["type"] == "error")["message"]

            with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p3')}") as leo:
                recv_until(leo, is_state)
                host.send_json({"action": "start"})
                for ws in (maya, leo, host, board):
                    st = recv_until(ws, lambda m: is_state(m) and m["state"]["phase"] != "LOBBY")
                    assert st["state"]["phase"] == "WAITING_FOR_ROLL"
                greeting = recv_until(board, lambda m: m["type"] == "mirror" and "Grandma" in m["text"])
                assert "welcome" in greeting["text"].lower()


def test_kid_disconnect_and_rejoin(client, game):
    gid, tok = game["game_id"], seat_token(game, "p2")
    with client.websocket_connect(f"/ws/{gid}?role=board") as board:
        recv_until(board, is_state)
        with client.websocket_connect(f"/ws/{gid}?seat={tok}"):
            assert connected(recv_until(board, is_state), "p2")
        assert not connected(recv_until(board, is_state), "p2")
        with client.websocket_connect(f"/ws/{gid}?seat={tok}") as again:
            assert connected(recv_until(again, is_state), "p2")


def test_second_device_takes_over_seat(client, game):
    gid, tok = game["game_id"], seat_token(game, "p2")
    with client.websocket_connect(f"/ws/{gid}?seat={tok}") as phone:
        recv_until(phone, is_state)
        with client.websocket_connect(f"/ws/{gid}?seat={tok}") as tablet:
            err = recv_until(phone, lambda m: m["type"] == "error")
            assert "taken over" in err["message"]
            with pytest.raises(WebSocketDisconnect) as exc:
                phone.receive_json()
            assert exc.value.code == TAKEN_OVER
            st = recv_until(tablet, is_state)   # new device gets state even with no change
            assert connected(st, "p2")
            # Old socket closing must not mark the seat disconnected.
            tablet.send_json({"action": "start"})
            assert recv_until(tablet, lambda m: m["type"] == "error")
            assert connected(game_state(client, gid), "p2")


def game_state(client, gid):
    with client.websocket_connect(f"/ws/{gid}?role=board") as b:
        return recv_until(b, is_state)


def test_kid_cannot_act_as_someone_else_or_start(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p2')}") as maya:
        recv_until(maya, is_state)
        maya.send_json({"player": "p1", "action": "roll"})
        assert "only act as p2" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_json({"player": "p2", "action": "start", "force": True})
        assert "host" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_json({"source": "vision", "type": "dice", "value": 7})
        assert "dev panel" in recv_until(maya, lambda m: m["type"] == "error")["message"]
        maya.send_text("not json")
        assert "JSON" in recv_until(maya, lambda m: m["type"] == "error")["message"]


def test_dev_panel_reaches_engine_via_adapter(client, game):
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?role=board") as board:
        recv_until(board, is_state)
        board.send_json({"source": "vision", "type": "dice", "value": 13})
        assert "2-12" in recv_until(board, lambda m: m["type"] == "error")["message"]
        # Valid read is routed to the engine, which rejects it for now (milestone 3).
        board.send_json({"source": "vision", "type": "dice", "value": 7})
        assert "dice" in recv_until(board, lambda m: m["type"] == "error")["message"]
        board.send_json({"source": "voice", "text": "hmm"})
        assert "yes or no" in recv_until(board, lambda m: m["type"] == "error")["message"]


def test_lan_base(client):
    base = client.get("/api/lan").json()["base"]
    assert base.startswith("http://") and "localhost" not in base


def test_kid_turn_end_to_end_through_stub_bridge(client, game):
    """Roll -> stub bridge reports move_done -> offer -> Buy -> paid; Grandma's tap via dev panel."""
    gid = game["game_id"]
    with client.websocket_connect(f"/ws/{gid}?role=board") as board, \
         client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p2')}") as maya, \
         client.websocket_connect(f"/ws/{gid}?seat={seat_token(game, 'p3')}") as leo:
        recv_until(leo, is_state)
        board.send_json({"action": "start"})
        recv_until(maya, lambda m: m["type"] == "turn_started")

        # Grandma's turn: camera reads 6 -> she lands on Royal Ballroom.
        board.send_json({"source": "vision", "type": "dice", "value": 6})
        offer = recv_until(board, lambda m: m["type"] == "offer")
        assert offer["square"] == 6
        board.send_json({"source": "voice", "text": "yes please"})
        recv_until(board, lambda m: m["type"] == "payment_requested")
        board.send_json({"source": "vision", "type": "player_card", "player": "p2"})
        assert "Maya's card" in recv_until(board, lambda m: m["type"] == "error")["message"]
        board.send_json({"source": "vision", "type": "player_card", "player": "p1"})
        line = recv_until(board, lambda m: m["type"] == "mirror" and "bought" in m["text"])
        assert "you now have 1400" in line["text"]

        # Maya's turn: Roll button, engine rolls, stub bridge finishes the move.
        recv_until(maya, lambda m: m["type"] == "turn_started" and m["player"] == "p2")
        maya.send_json({"player": "p2", "action": "roll"})
        rolled = recv_until(maya, lambda m: m["type"] == "rolled" and m["player"] == "p2")
        recv_until(maya, lambda m: m["type"] == "move" and m["player"] == "p2")
        # The stub bridge reports move_done by itself; wait for its result
        # (any landing square works, so this doesn't depend on the roll).
        moved = recv_until(maya, lambda m: m["type"] == "moved" and m["player"] == "p2")
        assert moved == {"type": "moved", "player": "p2", "from": 0, "to": rolled["value"]}


@pytest.mark.parametrize('answer', ['yes', 'no'])
def test_board_on_screen_buy_choice_uses_pending_player(client, game, answer):
    """Board buttons decide for the correct player; buying still waits for payment."""
    with client.websocket_connect(f"/ws/{game['game_id']}?role=board") as board:
        recv_until(board, is_state)
        board.send_json({'action': 'start', 'force': True})
        recv_until(board, lambda m: m['type'] == 'turn_started')
        board.send_json({'source': 'vision', 'type': 'dice', 'value': 6})
        recv_until(board, lambda m: m['type'] == 'offer')
        board.send_json({'action': 'decision', 'player': 'p2', 'answer': answer})
        assert "Grandma's decision" in recv_until(board, lambda m: m['type'] == 'error')['message']
        board.send_json({'action': 'decision', 'player': 'p1', 'answer': answer})
        state = recv_until(board, is_state)['state']
        if answer == 'yes':
            assert state['phase'] == 'WAITING_FOR_PAYMENT'
            assert state['players'][0]['balance'] == 1500
            assert '6' not in state['owners']
            board.send_json({'action': 'confirm_payment'})
            state = recv_until(board, is_state)['state']
            assert state['owners']['6'] == 'p1'
            assert state['players'][0]['balance'] == 1400
        else:
            assert state['phase'] == 'WAITING_FOR_ROLL'
            assert state['current_player'] == 'p2'
            assert '6' not in state['owners']
        # A second/stale click cannot buy something or charge again.
        board.send_json({'action': 'decision', 'player': 'p1', 'answer': 'yes'})
        recv_until(board, lambda m: m['type'] == 'error')
        # The shared board can also present a remote player's pending choice.
        board.send_json({'source': 'vision', 'type': 'dice', 'value': 8})
        recv_until(board, lambda m: m['type'] == 'offer' and m['player'] == 'p2')
        board.send_json({'action': 'decision', 'player': 'p2', 'answer': 'yes'})
        state = recv_until(board, is_state)['state']
        assert state['owners']['8'] == 'p2'
        assert state['players'][1]['balance'] == 1400


def test_lobby_movement_is_shared_temporary_and_seat_bound(client, game):
    gid = game['game_id']
    room = client.app.state.rooms.get(gid)
    with client.websocket_connect(f'/ws/{gid}?role=board') as board, \
         client.websocket_connect(f'/ws/{gid}?seat={seat_token(game, "p2")}') as maya:
        recv_until(board, is_state)
        snapshot = recv_until(maya, lambda m: m['type'] == 'lobby_presence')
        assert set(snapshot['players']) == {'p1', 'p2', 'p3'}
        seq, before = room.seq, room.state
        maya.send_json({'type': 'lobby_move', 'x': .7, 'y': .4, 'jump': True})
        motion = recv_until(board, lambda m: m['type'] == 'lobby_motion')
        assert motion == {'type': 'lobby_motion', 'player': 'p2', 'x': .7, 'y': .4, 'jump': 1}
        assert recv_until(maya, lambda m: m['type'] == 'lobby_motion') == motion
        assert room.seq == seq and room.state is before
        assert all(p.position == 0 and p.balance == 1500 for p in room.state.players)
        maya.send_json({'type': 'lobby_move', 'player': 'p1', 'x': .1, 'y': .2})
        assert 'only move p2' in recv_until(maya, lambda m: m['type'] == 'error')['message']
        board.send_json({'type': 'lobby_move', 'x': .2, 'y': .3, 'jump': True})
        assert recv_until(board, lambda m: m['type'] == 'lobby_motion')['player'] == 'p1'
        # Late viewers get courtyard positions, without replaying jumps or engine actions.
        with client.websocket_connect(f'/ws/{gid}?host={game["host_token"]}') as host:
            snapshot = recv_until(host, lambda m: m['type'] == 'lobby_presence')
            assert snapshot['players']['p2'] == {'x': .7, 'y': .4, 'jump': 1}
        board.send_json({'action': 'start', 'force': True})
        recv_until(board, lambda m: is_state(m) and m['state']['phase'] == 'WAITING_FOR_ROLL')
        maya.send_json({'type': 'lobby_move', 'x': .8, 'y': .8, 'jump': True})
        assert 'only available while waiting' in recv_until(maya, lambda m: m['type'] == 'error')['message']
        assert all(p.position == 0 for p in room.state.players)


@pytest.mark.parametrize('position', [
    {'x': -1, 'y': .5}, {'x': .5, 'y': 1.1}, {'x': '0.2', 'y': .5},
    {'x': True, 'y': .5}, {'x': None, 'y': .5}, {'x': .5, 'y': .5, 'jump': 'yes'},
])
def test_lobby_movement_rejects_invalid_coordinates(client, game, position):
    with client.websocket_connect(f'/ws/{game["game_id"]}?role=board') as board:
        recv_until(board, is_state)
        board.send_json({'type': 'lobby_move', **position})
        assert 'finite numbers' in recv_until(board, lambda m: m['type'] == 'error')['message']
        room = client.app.state.rooms.get(game['game_id'])
        assert room.lobby_positions['p1'] == {'x': .25, 'y': .62, 'jump': 0}


def test_host_can_switch_attendance_and_links_without_recreating_game(client):
    game = client.post('/api/games', json={'players': [
        {'name': 'Grandma', 'kind': 'in_person'}, {'name': 'Maya', 'kind': 'in_person'}]}).json()
    gid = game['game_id']
    assert game['join_links'] == {}
    with client.websocket_connect(f'/ws/{gid}?host={game["host_token"]}') as host, \
         client.websocket_connect(f'/ws/{gid}?role=board') as board:
        recv_until(host, lambda m: m['type'] == 'join_links')
        recv_until(board, is_state)
        host.send_json({'action': 'set_attendance', 'target': 'p2', 'kind': 'remote', 'connected': True})
        state = recv_until(host, is_state)['state']
        assert state['players'][1]['kind'] == 'remote'
        assert not state['players'][1]['connected']  # a browser cannot fake readiness
        link = recv_until(host, lambda m: m['type'] == 'join_links')['join_links']['p2']
        token = link.rsplit('/', 1)[1]
        # No join credentials are broadcast to the shared board.
        board.send_json({'action': 'unknown'})
        while True:
            msg = board.receive_json()
            assert msg['type'] != 'join_links'
            if msg['type'] == 'error':
                break
        with client.websocket_connect(f'/ws/{gid}?seat={token}') as phone:
            recv_until(phone, is_state)
            recv_until(host, lambda m: is_state(m) and m['state']['players'][1]['connected'])
            phone.send_json({'action': 'set_attendance', 'target': 'p2', 'kind': 'in_person'})
            assert 'only the lobby host' in recv_until(phone, lambda m: m['type'] == 'error')['message']
            host.send_json({'action': 'set_attendance', 'target': 'p2', 'kind': 'in_person'})
            assert 'in person' in recv_until(phone, lambda m: m['type'] == 'error')['message']
            with pytest.raises(WebSocketDisconnect) as closed:
                phone.receive_json()
            assert closed.value.code == 4405
        state = recv_until(host, lambda m: is_state(m) and m['state']['players'][1]['kind'] == 'in_person')['state']
        assert state['players'][1]['connected']
        assert recv_until(host, lambda m: m['type'] == 'join_links')['join_links'] == {}
        with client.websocket_connect(f'/ws/{gid}?seat={token}') as old_link:
            assert old_link.receive_json()['fatal']
            with pytest.raises(WebSocketDisconnect) as closed:
                old_link.receive_json()
            assert closed.value.code == 4405
        host.send_json({'action': 'set_attendance', 'target': 'p2', 'kind': 'remote'})
        recv_until(host, is_state)
        assert recv_until(host, lambda m: m['type'] == 'join_links')['join_links']['p2'] == link
        board.send_json({'action': 'set_attendance', 'target': 'p2', 'kind': 'in_person'})
        assert 'only the lobby host' in recv_until(board, lambda m: m['type'] == 'error')['message']


def test_waiting_guests_use_home_and_running_players_use_game(client, game):
    gid = game['game_id']
    path = game['join_links']['p2']
    waiting = client.get(path, follow_redirects=False)
    assert waiting.status_code == 302
    assert waiting.headers['cache-control'] == 'no-store'
    assert waiting.headers['location'] == f'/?game={gid}&seat={seat_token(game, "p2")}'
    assert game['host_token'] not in waiting.headers['location']
    home = client.get(waiting.headers['location']).text
    assert 'home-lobby-scene' in home and 'guest-waiting' in home
    board = client.get(game['board_url']).text
    assert 'mountLobbyPlayground' not in board and 'lobby-courtyard' not in board
    with client.websocket_connect(f'/ws/{gid}?role=board') as board:
        recv_until(board, is_state)
        board.send_json({'action': 'start', 'force': True})
        recv_until(board, lambda m: is_state(m) and m['state']['phase'] == 'WAITING_FOR_ROLL')
    playing = client.get(path, follow_redirects=False)
    assert playing.status_code == 200
    assert 'id="roll"' in playing.text
    assert 'mountLobbyPlayground' not in playing.text


def test_shared_screen_roll_is_broadcast_once_and_uses_authoritative_result(client, game):
    gid = game['game_id']
    with client.websocket_connect(f'/ws/{gid}?role=board') as board:
        recv_until(board, is_state)
        board.send_json({'action': 'start', 'force': True})
        state = recv_until(board, lambda m: is_state(m) and m['state']['phase'] == 'WAITING_FOR_ROLL')['state']
        action = {'action': 'board_roll', 'target': state['current_player'], 'turn': state['turn']}
        board.send_json(action)
        roll = recv_until(board, lambda m: m['type'] == 'rolled')
        assert len(roll['dice']) == 2 and all(1 <= d <= 6 for d in roll['dice'])
        assert sum(roll['dice']) == roll['value']
        board.send_json(action)  # a queued duplicate cannot roll the next player's turn
        recv_until(board, lambda m: m['type'] == 'error')
        room = client.app.state.rooms.get(gid)
        assert room.state.last_dice == roll['dice'] and room.state.last_roll == roll['value']
        assert sum(e['type'] == 'rolled' for e in client.app.state.store.events(gid)) == 1
