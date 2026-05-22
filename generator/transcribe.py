"""
transcribe.py - Sprint 3 placeholder
Whisper API wrapper for audio -> text transcription.
If OPENAI_API_KEY is not set, raises TranscribeUnavailable so the UI
can show a friendly message instead of crashing.
"""
import os
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


class TranscribeUnavailable(RuntimeError):
    """Raised when transcription is requested but key is missing."""


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.m4a") -> str:
    if not OPENAI_API_KEY:
        raise TranscribeUnavailable(
            "Audio transcription is not configured yet. "
            "Set OPENAI_API_KEY in .env to enable."
        )
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {"file": (filename, audio_bytes)}
    data = {"model": "whisper-1"}
    r = requests.post(url, headers=headers, files=files, data=data, timeout=120)
    r.raise_for_status()
    return r.json().get("text", "").strip()
