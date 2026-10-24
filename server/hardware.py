"""Use Avish's motor server (arduino/server.py) in-process, unchanged.

His file stays the one source of truth for motor logic (plan_move, the
tracked square, the /board/* endpoints). We load it here and:
  - attach its routes to our app (except GET /, which served his old
    ui.html and would clash with our create page),
  - call its board_goto() for game moves on lane 1 (see adapters/bridge.py).

His file is named server.py, the same as our `server` package, so it can't
simply be imported with arduino/ on sys.path: that would shadow our package.
We load it under another name and register his control.py as `control`,
which is what his `from control import Board` expects.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ARDUINO_DIR = Path(__file__).resolve().parents[1] / "arduino"

# Routes we don't take from his app: his UI page and FastAPI's own docs routes
# (our app has its own).
SKIP_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

_module: ModuleType | None = None


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def motor_server(fresh: bool = False) -> ModuleType:
    """His arduino/server.py module, loaded once. `fresh=True` reloads it (tests)."""
    global _module
    if _module is None or fresh:
        sys.modules["control"] = _load("control", ARDUINO_DIR / "control.py")
        _module = _load("arduino_server", ARDUINO_DIR / "server.py")
    return _module


def motor_routes() -> list:
    """His endpoints (/board, /board/goto, /board/move, /board/set, /move, /roll,
    /stop, /pos, /vision/events), ready to add to our app."""
    return [r for r in motor_server().app.routes if getattr(r, "path", None) not in SKIP_PATHS]
