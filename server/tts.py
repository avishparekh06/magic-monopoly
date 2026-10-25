"""The Mirror's speaking voice through ElevenLabs text-to-speech (the `elevenlabs` SDK).

Every Mirror line the server broadcasts is `allow`ed here: synthesis starts at once in the
background, so the audio is usually ready by the time a page asks for it at GET /tts?text=.
Only allowed lines are served, so a visitor through the public tunnel cannot spend the
ElevenLabs credits on text of their own. Pages fall back to the browser's own voice when
/tts fails or no key is set.

Off unless the server runs with --speech elevenlabs.
Env: ELEVENLABS_API_KEY (required to enable), ELEVENLABS_VOICE (voice id, default George,
a warm storyteller), ELEVENLABS_MODEL (default eleven_v4).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict

log = logging.getLogger("tts")

BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"     # George
DEFAULT_MODEL = "eleven_v4"
MAX_LINES = 200
# Lines the pages speak on their own, not broadcast by the server.
FIXED = ("The Mirror is awake.", "The Mirror is listening.")

_clips: OrderedDict[str, asyncio.Task] = OrderedDict()
_allowed: OrderedDict[str, None] = OrderedDict((t, None) for t in FIXED)


_on = False   # set by the server's --speech flag; off = pages use the browser voice and no credits are spent


def use(on: bool) -> None:
    global _on
    _on = on
    if on and not os.environ.get("ELEVENLABS_API_KEY"):
        raise RuntimeError("--speech elevenlabs needs ELEVENLABS_API_KEY in .env "
                           "(npx -y @elevenlabs/cli@latest onboard init)")


def enabled() -> bool:
    return _on and bool(os.environ.get("ELEVENLABS_API_KEY"))


def allow(text: str) -> None:
    """A Mirror line went out: let pages fetch its audio, and start making it now."""
    if not enabled() or not text:
        return
    _allowed[text] = None
    _allowed.move_to_end(text)
    while len(_allowed) > MAX_LINES + len(FIXED):
        _allowed.popitem(last=False)
    _start(text)


def _start(text: str) -> asyncio.Task:
    task = _clips.get(text)
    if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
        task = asyncio.create_task(_synthesize(text))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())   # a failed prefetch is not an error
        _clips[text] = task
    _clips.move_to_end(text)
    while len(_clips) > MAX_LINES:
        _clips.popitem(last=False)
    return task


async def audio(text: str) -> bytes:
    """MP3 bytes for an allowed line. Raises KeyError if the line was never broadcast."""
    if text not in _allowed:
        raise KeyError(text)
    return await asyncio.shield(_start(text))


_client = None
_slots: asyncio.Semaphore | None = None   # ElevenLabs' free tier rejects more than ~2 requests at once


def client():
    """The SDK client; it reads ELEVENLABS_API_KEY from the environment (loaded from .env by main)."""
    global _client
    if _client is None:
        from elevenlabs import AsyncElevenLabs
        _client = AsyncElevenLabs()
    return _client


async def _synthesize(text: str) -> bytes:
    """One line's MP3. Requests wait for a free slot (FIFO, so lines finish in the order they were said)
    and a rejected one (429, too many at once) is retried once after a moment."""
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(int(os.environ.get("ELEVENLABS_CONCURRENCY") or 2))
    async with _slots:
        try:
            return await _convert(text)
        except Exception as e:
            if getattr(e, "status_code", None) != 429:
                log.warning("elevenlabs failed: %s", e)
                raise
        await asyncio.sleep(1.0)
        try:
            return await _convert(text)
        except Exception as e:
            log.warning("elevenlabs failed after retry: %s", e)
            raise


async def _convert(text: str) -> bytes:
    from elevenlabs import VoiceSettings
    chunks = client().text_to_speech.convert(
        voice_id=os.environ.get("ELEVENLABS_VOICE") or DEFAULT_VOICE,
        text=text,
        model_id=os.environ.get("ELEVENLABS_MODEL") or DEFAULT_MODEL,
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(stability=0.4, similarity_boost=0.75),
    )
    return b"".join([c async for c in chunks])
