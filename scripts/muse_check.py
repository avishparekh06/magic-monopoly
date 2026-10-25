"""One-call check that the Muse API key and endpoints work. Run as soon as you have the key:

    MUSE_API_KEY=... .venv/bin/python scripts/muse_check.py            # chat (Muse Spark) + transcribe a synthetic tone
    MUSE_API_KEY=... .venv/bin/python scripts/muse_check.py clip.wav   # transcribe your own 16 kHz mono WAV
"""
import asyncio
import io
import math
import os
import struct
import sys
import wave

import httpx

KEY = os.environ.get("MUSE_API_KEY") or os.environ.get("MODEL_API_KEY")
BASE = os.environ.get("MUSE_BASE_URL", "https://api.meta.ai/v1")


def tone_wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


async def main():
    if not KEY:
        sys.exit("set MUSE_API_KEY first")
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {KEY}"}) as c:
        print("1. chat completions (muse-spark-1.3) ...")
        r = await c.post(f"{BASE}/chat/completions", json={
            "model": "muse-spark-1.3", "max_tokens": 30,
            "messages": [{"role": "user", "content": "Reply with the single word READY."}]})
        print("   ", r.status_code, r.text[:200])

        wav = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else tone_wav()
        print("2. speech to text (muse-voice-transcribe-1.0) ...")
        r = await c.post(f"{BASE}/asr/transcribe", files={
            "request": (None, '{"mode":"PUSH_TO_TALK","model":"muse-voice-transcribe-1.0","audioEncoding":"WAV"}', "application/json"),
            "audio": ("clip.wav", wav, "audio/wav")})
        print("   ", r.status_code, r.text[:300])

asyncio.run(main())
