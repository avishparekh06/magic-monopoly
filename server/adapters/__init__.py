"""Adapter selection.

The bridge is chosen on its own (--bridge stub|arduino). `--sim` picks the
stubs for vision, voice and the Mirror; otherwise their real implementations.
"""
from __future__ import annotations

from dataclasses import dataclass

from .bridge import ArduinoBridge, MoveBridge, StubBridge
from .mirror import Mirror, MuseMirror, TemplateMirror
from .vision import ArucoVision, StubVision, Vision
from .voice import MuseVoice, StubVoice, Voice

BRIDGES = ("stub", "arduino")
VOICES = ("stub", "muse")
MIRRORS = ("template", "muse")


@dataclass
class Adapters:
    bridge: MoveBridge
    vision: Vision
    voice: Voice
    mirror: Mirror


def make_bridge(kind: str = "stub", move_delay: float = 1.0) -> MoveBridge:
    if kind == "stub":
        return StubBridge(move_delay)
    if kind == "arduino":
        return ArduinoBridge(fallback=StubBridge(move_delay))
    raise ValueError(f"bridge must be one of {BRIDGES}")


def make_voice(kind: str = "stub") -> Voice:
    if kind == "stub":
        return StubVoice()
    if kind == "muse":
        return MuseVoice()
    raise ValueError(f"voice must be one of {VOICES}")


def make_mirror(kind: str = "template") -> Mirror:
    if kind == "template":
        return TemplateMirror()
    if kind == "muse":
        return MuseMirror()
    raise ValueError(f"mirror must be one of {MIRRORS}")


def make_adapters(sim: bool, move_delay: float = 1.0, bridge: str = "stub", voice: str = "stub",
                  mirror: str = "template") -> Adapters:
    """`sim` picks the stub for vision (the camera comes through /vision/events instead). The bridge, the voice
    and the Mirror are chosen on their own so each real piece can be switched on as soon as it works."""
    vision = StubVision() if sim else ArucoVision()
    return Adapters(make_bridge(bridge, move_delay), vision, make_voice(voice), make_mirror(mirror))
