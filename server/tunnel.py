"""Public URL for phones that are not on the laptop's Wi-Fi.

`cloudflared tunnel --url http://localhost:<port>` gives a random
https://<words>.trycloudflare.com address with no account, and it passes
WebSockets. We spawn it, read the address off its log, and hand it to the
pages so join links and QR codes point at it. If it never comes up, the
server keeps running and links fall back to the LAN address.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil

log = logging.getLogger("tunnel")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, port: int):
        self.port = port
        self.url: str | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self._drainer: asyncio.Task | None = None

    async def start(self, timeout: float = 25.0) -> str | None:
        exe = shutil.which("cloudflared")
        if exe is None:
            log.warning("cloudflared not installed (brew install cloudflared); no public URL")
            return None
        self.proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--url", f"http://localhost:{self.port}", "--no-autoupdate",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        assert self.proc.stderr is not None
        try:
            async with asyncio.timeout(timeout):
                while self.url is None:
                    line = await self.proc.stderr.readline()
                    if not line:
                        break
                    m = URL_RE.search(line.decode(errors="ignore"))
                    if m:
                        self.url = m.group(0)
        except TimeoutError:
            log.warning("cloudflared gave no URL within %.0fs; links use the LAN address", timeout)
        if self.url:
            log.info("public URL: %s", self.url)
            self._drainer = asyncio.create_task(self._drain())
        return self.url

    async def _drain(self) -> None:          # keep the pipe from filling up
        assert self.proc and self.proc.stderr
        while await self.proc.stderr.readline():
            pass

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except TimeoutError:
                self.proc.kill()
