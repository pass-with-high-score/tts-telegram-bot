import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

# Prefer Deepgram v3; gracefully fall back to v2 if only that is installed.
_DG_V3 = False
try:
    from deepgram import DeepgramClient, PrerecordedOptions  # type: ignore
    _DG_V3 = True
except Exception:
    DeepgramClient = None  # type: ignore
    PrerecordedOptions = None  # type: ignore

try:
    from deepgram import Deepgram  # v2
except Exception:
    Deepgram = None  # type: ignore


@dataclass
class TranscriptionResult:
    text: str
    raw: dict


class DeepgramTranscriber:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._v3 = DeepgramClient(api_key) if _DG_V3 and DeepgramClient else None  # type: ignore
        self._v2 = Deepgram(api_key) if (not self._v3 and Deepgram) else None  # type: ignore

    async def transcribe_file(
        self,
        file_path: Path,
        explicit_mime: Optional[str] = None,
        options: Optional[Dict] = None,
    ) -> TranscriptionResult:
        """Transcribe a local audio file with Deepgram prerecorded API.

        - file_path: path to the audio file to transcribe.
        - explicit_mime: optionally pass a mime type (e.g., from Telegram message metadata).
        """
        # Determine mimetype: prefer explicit one, fall back to guess by extension, then default.
        mimetype = explicit_mime or mimetypes.guess_type(str(file_path))[0] or "audio/ogg"

        # Read file bytes
        audio_bytes = file_path.read_bytes()

        # Request options: enable smart formatting and punctuation for readable text.
        base_options = {
            "smart_format": True,
            "punctuate": True,
        }
        if options:
            base_options.update(options)

        source = {"buffer": audio_bytes, "mimetype": mimetype}

        if self._v3:
            # v3 SDK is synchronous; call it in a thread
            import asyncio
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                self._transcribe_v3_sync,
                audio_bytes,
                mimetype,
                base_options,
            )
        elif self._v2:
            source = {"buffer": audio_bytes, "mimetype": mimetype}
            response = await self._v2.transcription.prerecorded(source, base_options)  # type: ignore
        else:
            raise RuntimeError("Deepgram SDK not available")

        # Extract transcript text: Deepgram returns a structured JSON.
        # Use the combined text (utterances or the alternative transcripts).
        text = _extract_text_from_deepgram_response(response)
        return TranscriptionResult(text=text, raw=response)

    def _transcribe_v3_sync(self, audio_bytes: bytes, mimetype: str, options: Dict[str, Any]):
        assert self._v3 is not None
        # Only pass supported keys to PrerecordedOptions
        allowed = {
            "model",
            "language",
            "detect_language",
            "smart_format",
            "punctuate",
            "diarize",
            "utterances",
            "paragraphs",
            "numerals",
            "profanity_filter",
            "multichannel",
            "keywords",
        }
        kwargs = {k: v for k, v in (options or {}).items() if k in allowed}
        po = PrerecordedOptions(**kwargs)  # type: ignore
        src = {"buffer": audio_bytes, "mimetype": mimetype}
        resp = self._v3.listen.prerecorded.v("1").transcribe_file(src, po)  # type: ignore
        try:
            return json.loads(resp.to_json())  # normalize to dict
        except Exception:
            return resp


def _extract_text_from_deepgram_response(response: Any) -> str:
    # Attempt to extract the transcript robustly.
    # Priority: channel.alternatives.transcript → utterances combined → fallback empty.
    try:
        if hasattr(response, "to_dict"):
            response = response.to_dict()
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
