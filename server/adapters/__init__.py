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


def make_adapters(sim: bool, move_delay: float = 1.0, bridge: str = "stub") -> Adapters:
    if sim:
        return Adapters(make_bridge(bridge, move_delay), StubVision(), StubVoice(), TemplateMirror())
    return Adapters(make_bridge(bridge, move_delay), ArucoVision(), MuseVoice(), MuseMirror())
