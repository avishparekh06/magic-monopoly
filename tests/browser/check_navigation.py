"""Run the history UI regression in Chrome using a disposable local game server.

From the repo root: .venv/bin/python tests/browser/check_navigation.py
Requires Node and Chrome (set CHROME_BIN for a non-default Chrome installation).
"""
from pathlib import Path
import argparse
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from server.store import Store
from tests.test_accounts import add_session, PASSWORD


def seed(path):
    store = Store(path)
    try:
        alice = store.accounts.create('career_alice', PASSWORD, 'Alice')
        bob = store.accounts.create('career_bob', PASSWORD, 'Bobby')
        add_session(store, 'career_win', {'p1': alice['id']}, purchases={'p1': [6, 8]}, elimination=['p3', 'p2'], winner='p1')
        add_session(store, 'career_second', {'p2': alice['id']}, purchases={'p2': [6]}, elimination=['p1', 'p2'], winner='p3')
        add_session(store, 'career_active', {'p1': alice['id'], 'p2': bob['id']}, purchases={'p1': [6]}, phase='WAITING_FOR_ROLL')
        add_session(store, 'career_waiting', {'p1': alice['id']}, phase='LOBBY')
        add_session(store, 'career_guest', {}, purchases={'p1': [39]}, elimination=['p3', 'p2'], winner='p1')
        add_session(store, 'career_unknown', {'p1': bob['id']})
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8001)
    port = parser.parse_args().port
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))  # Fail before doing anything if this port is in use.
    with tempfile.TemporaryDirectory(prefix='magic-monopoly-navigation-') as temp:
        path = Path(temp)
        seed(path / 'games.db')
        with (path / 'server.log').open('w+') as log:
            server = subprocess.Popen([sys.executable, '-m', 'server.main', '--sim', '--host', '127.0.0.1', '--port', str(port), '--db', str(path / 'games.db')], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                origin = f'http://127.0.0.1:{port}'
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Preview server stopped during startup')
                    try:
                        with urllib.request.urlopen(origin + '/api/board', timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Preview server did not start')
                subprocess.run(['node', str(ROOT / 'tests/browser/session-navigation.mjs'), origin], cwd=ROOT, check=True, timeout=180)
            except BaseException:
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    main()
