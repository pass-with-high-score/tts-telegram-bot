import asyncio
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from deepgram import Deepgram


@dataclass
class TranscriptionResult:
    text: str
    raw: dict


class DeepgramTranscriber:
    def __init__(self, api_key: str):
        self._client = Deepgram(api_key)

    async def transcribe_file(self, file_path: Path, explicit_mime: Optional[str] = None) -> TranscriptionResult:
        """Transcribe a local audio file with Deepgram prerecorded API.

        - file_path: path to the audio file to transcribe.
        - explicit_mime: optionally pass a mime type (e.g., from Telegram message metadata).
        """
        # Determine mimetype: prefer explicit one, fall back to guess by extension, then default.
        mimetype = explicit_mime or mimetypes.guess_type(str(file_path))[0] or "audio/ogg"

        # Read file bytes
        audio_bytes = file_path.read_bytes()

        # Request options: enable smart formatting and punctuation for readable text.
        options = {
            "smart_format": True,
            "punctuate": True,
        }

        source = {"buffer": audio_bytes, "mimetype": mimetype}

        response = await self._client.transcription.prerecorded(source, options)

        # Extract transcript text: Deepgram returns a structured JSON.
        # Use the combined text (utterances or the alternative transcripts).
        text = _extract_text_from_deepgram_response(response)
        return TranscriptionResult(text=text, raw=response)


def _extract_text_from_deepgram_response(response: dict) -> str:
    # Attempt to extract the transcript robustly.
    # Priority: channel.alternatives.transcript → utterances combined → fallback empty.
    try:
        channels = response.get("results", {}).get("channels", [])
        if channels:
            alts = channels[0].get("alternatives", [])
            if alts and "transcript" in alts[0]:
                return alts[0].get("transcript", "").strip()
    except Exception:
        pass

    # Fallback: assemble from utterances if present
    try:
        utterances = response.get("results", {}).get("utterances", [])
        if utterances:
            return "\n".join(u.get("transcript", "").strip() for u in utterances if u.get("transcript")).strip()
    except Exception:
        pass

    return ""

