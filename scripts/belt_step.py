"""Step the physical belt by hand, no game involved:  .venv/bin/python scripts/belt_step.py 3
Reads BELT_URL and BELT_KEY from .env (or the environment)."""
import os
import sys
from pathlib import Path

import httpx

env = {}
for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); env[k] = v.strip().strip('"')
url = os.environ.get("BELT_URL") or env.get("BELT_URL")
key = os.environ.get("BELT_KEY") or env.get("BELT_KEY")
n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
r = httpx.post(f"{url.rstrip('/')}/step", params={"squares": n, "key": key}, timeout=120)
print(r.status_code, r.text[:300])
