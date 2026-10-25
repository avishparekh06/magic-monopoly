import time
import serial
from serial.tools import list_ports

STEPS_PER_REV = 200
MICROSTEP = 1            # 1 with no jumper caps, 16 with all three
MM_PER_REV = 40.0        # 20-tooth GT2 pulley
STEPS_PER_MM = STEPS_PER_REV * MICROSTEP / MM_PER_REV

def find_port():
    for p in list_ports.comports():
        d = p.device
        if any(k in d for k in ("usbmodem", "usbserial", "ttyACM", "ttyUSB")) or d.startswith("COM"):
            return d
    raise RuntimeError("No Arduino port found. Check the USB cable.")

class Board:
    def __init__(self, port=None, baud=115200):
        self.ser = serial.Serial(port or find_port(), baud, timeout=1)
        time.sleep(2)                       # the Uno resets when the port opens
        self.ser.reset_input_buffer()
        self.pending = set()

    def _send(self, line):
        self.ser.write((line + "\n").encode())

    def _read(self):
        return self.ser.readline().decode(errors="ignore").strip()

    def _wait_ok(self):
        while True:
            line = self._read()
            if line == "ok":
                return
            if line == "err":
                raise RuntimeError("Arduino rejected the command")
            if line.startswith("done"):
                self.pending.discard(int(line.split()[1]))
            if line == "":
                raise TimeoutError("No reply from the Arduino")

    def move(self, axis, steps, period_us=None, wait=True):
        steps = int(steps)
        if steps == 0:
            return
        self._send(f"M {axis} {steps}" + (f" {int(period_us)}" if period_us else ""))
        self._wait_ok()
        self.pending.add(axis)
        if wait:
            self.wait()

    def move_mm(self, axis, mm, speed_mm_s=20, wait=True):
        period = max(200, int(1e6 / (speed_mm_s * STEPS_PER_MM)))
        self.move(axis, round(mm * STEPS_PER_MM), period, wait)

    def wait(self, timeout=120):
        end = time.time() + timeout
        while self.pending and time.time() < end:
            line = self._read()
            if line.startswith("done"):
                self.pending.discard(int(line.split()[1]))
        if self.pending:
            raise TimeoutError(f"Axes still moving: {self.pending}")

    def stop(self):
        """Stop every motor. The Uno answers `ok` but never sends `done` for the axes it stopped, and a move that is
        waiting on another thread reads the same port (and discards the `ok`), so don't wait for the `ok`: drop the
        pending axes so that move's wait() ends, then throw away whatever the Uno sent so it can't answer the next command."""
        self._send("S")
        self.pending.clear()
        time.sleep(0.15)
        self.ser.reset_input_buffer()

    def motors(self, on=True):
        self._send(f"E {1 if on else 0}"); self._wait_ok()

    def zero(self):
        self._send("Z"); self._wait_ok()

    def pos(self):
        self._send("P")
        for _ in range(5):
            line = self._read()
            if line.startswith("pos"):
                return [int(x) for x in line.split()[1:]]
        raise TimeoutError("No position reply")

HELP = """
Commands (axis 0 = X, 1 = Y, 2 = Z):
  <axis> <steps>          move by steps, e.g.  0 200   or  0 -200
  <axis> <steps> <us>     same, with step period in microseconds (smaller = faster)
  mm <axis> <mm> [mm/s]   move belt by mm, e.g.  mm 0 40   or  mm 1 -100 30
  multi 0 100 1 50 ...    move several axes at once in mm: axis mm axis mm ...
  pos                     show step counts
  zero                    reset step counts to 0
  stop                    stop all motors
  on / off                energize / release motors
  help, quit
"""

def main():
    b = Board()
    print("Connected.", HELP)
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        p = line.split()
        cmd = p[0].lower()
        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "pos":
                print(b.pos())
            elif cmd == "zero":
                b.zero()
            elif cmd == "stop":
                b.stop()
            elif cmd == "on":
                b.motors(True)
            elif cmd == "off":
                b.motors(False)
            elif cmd == "mm":
                axis, mm = int(p[1]), float(p[2])
                speed = float(p[3]) if len(p) > 3 else 20
                b.move_mm(axis, mm, speed)
            elif cmd == "multi":
                pairs = list(zip(p[1::2], p[2::2]))
                for a, mm in pairs:
                    b.move_mm(int(a), float(mm), wait=False)
                b.wait()
            else:
                axis, steps = int(p[0]), int(p[1])
                period = int(p[2]) if len(p) > 2 else None
                b.move(axis, steps, period)
        except (ValueError, IndexError):
            print("Couldn't read that. Type help.")
        except Exception as e:
            print("Error:", e)
    try:
        b.stop()
    except Exception:
        pass

if __name__ == "__main__":
    main()