"""Stop while a move is running: the move must end at once and release the lock, not wait out its 120 s timeout.

The firmware answers `S` with `ok` but never sends `done <axis>` for the axes it stopped, and the waiting move and
the stopping call read the same serial port, so the move used to swallow Stop's `ok`."""
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "arduino"))
import control  # noqa: E402


class FakeSerial:
    """Answers like the Uno: `ok` to every command, `done` only when a move finishes (never after S)."""

    def __init__(self):
        self.lines, self.sent, self.timeout = deque(), [], 1

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        self.lines.append("ok\n")

    def readline(self):
        if threading.current_thread() is threading.main_thread():
            time.sleep(0.05)                          # the waiting move reads first and swallows Stop's `ok`, as it can on the real port
        for _ in range(20):                           # ~0.2 s of "nothing yet", like a short serial timeout
            if self.lines:
                return self.lines.popleft().encode()
            time.sleep(0.01)
        return b""

    def reset_input_buffer(self):
        self.lines.clear()


def make_board():
    b = control.Board.__new__(control.Board)
    b.ser, b.pending = FakeSerial(), set()
    return b


def test_stop_releases_a_move_that_is_waiting_for_done():
    b = make_board()
    result = {}

    def run():
        try:
            b.move(1, 5000, 3000)                     # waits for `done 1`, which never comes after a stop
            result["ended"] = "returned"
        except Exception as e:                        # noqa: BLE001
            result["ended"] = repr(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)
    assert t.is_alive() and b.pending == {1}
    b.stop()                                          # must not raise even if the move thread took its `ok`
    t.join(timeout=4)
    assert not t.is_alive(), "the move is still waiting for a done that will never come"
    assert b.pending == set()
    assert b.ser.sent[-1] == "S"


def test_nothing_stale_is_left_to_answer_the_next_command():
    b = make_board()
    b.stop()
    assert not b.ser.lines
    b.move(0, 10, 3000, wait=False)                   # the next command reads its own ok
    assert b.pending == {0}
