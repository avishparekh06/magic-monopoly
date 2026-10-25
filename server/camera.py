"""The camera process as the game sees it.

vision/live.py reports what it is looking at a few times a second (POST /vision/sync) and gets back what the
game wants it to look for. This module keeps that last report, turns it into one message for the board page
(`{"type": "camera", ...}`) and notices when the camera stops reporting. It can also start the camera process
itself (`--camera <index>`) so the whole table is one command.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("camera")

OFFLINE_AFTER = 3.0        # seconds without a report before the board says the camera is gone
REPORT_KEYS = ("mode", "dice_state", "seeing", "card", "fps", "forced")


class CameraStatus:
    def __init__(self) -> None:
        self.report: dict = {}
        self.seen_at: float | None = None
        self._last_sent: dict | None = None

    def update(self, report: dict, now: float | None = None) -> None:
        self.report = {k: report.get(k) for k in REPORT_KEYS if k in report}
        self.seen_at = now if now is not None else time.time()

    @property
    def online(self) -> bool:
        return self.seen_at is not None and time.time() - self.seen_at < OFFLINE_AFTER

    def snapshot(self) -> dict:
        """The board page's view: nothing if no camera ever reported."""
        if self.seen_at is None:
            return {"type": "camera", "online": False, "ever": False}
        return {"type": "camera", "online": self.online, "ever": True, **self.report}

    def changed(self) -> dict | None:
        """The snapshot if it differs from the last one handed out, else None."""
        snap = self.snapshot()
        if snap == self._last_sent:
            return None
        self._last_sent = snap
        return snap

    async def watch(self, publish: Callable[[dict], Awaitable[None]]) -> None:
        """Push the offline transition when reports stop (reports themselves are pushed on arrival)."""
        while True:
            await asyncio.sleep(1.0)
            snap = self.changed()
            if snap is not None:
                await publish(snap)


def camera_command(index: int, port: int, extra: list[str] | None = None) -> list[str]:
    live = Path(__file__).resolve().parent.parent / "vision" / "live.py"
    return [sys.executable, "-u", str(live), str(index), "--post", "--server", f"http://127.0.0.1:{port}", *(extra or [])]


class CameraProcess:
    """vision/live.py as a child of the server: started after the server listens, stopped with it."""

    def __init__(self, cmd: list[str]) -> None:
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        log.info("starting the camera: %s", " ".join(self.cmd))
        self.proc = subprocess.Popen(self.cmd)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
