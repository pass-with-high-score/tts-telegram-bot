from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Guarded import: Deepgram v3 uses Python 3.10+ syntax (match/case).
# On Python 3.9 this import will raise SyntaxError; catch it and disable the feature.
try:
    from deepgram import DeepgramClient, AnalyzeOptions  # type: ignore
    _DG_V3_AVAILABLE = True
except Exception:
    DeepgramClient = None  # type: ignore
    AnalyzeOptions = None  # type: ignore
    _DG_V3_AVAILABLE = False


@dataclass
class AnalyzeResult:
    ok: bool
    message: str
    raw_json: Optional[str] = None


class TextAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key

    @staticmethod
    def is_available() -> bool:
        return _DG_V3_AVAILABLE

    def analyze_text(self, text: str, options: Dict[str, Any]) -> AnalyzeResult:
        if not _DG_V3_AVAILABLE:
            return AnalyzeResult(
                ok=False,
                message=(
                    "Text Intelligence requires Python 3.10+ and deepgram-sdk>=3. "
                    "Upgrade your environment to enable it."
                ),
            )
        try:
            dg = DeepgramClient(self.api_key)  # type: ignore
            # Only pass known keys to the options model
            keys = ("language", "summarize", "topics", "intents", "sentiment")
            kwargs = {k: v for k, v in options.items() if k in keys and v not in (None, "")}
            ao = AnalyzeOptions(**kwargs)  # type: ignore
            src = {"buffer": text}
            resp = dg.read.analyze.v("1").analyze_text(src, ao)
            try:
                return AnalyzeResult(ok=True, message="OK", raw_json=resp.to_json(indent=2))
            except Exception:
                return AnalyzeResult(ok=True, message="OK", raw_json=str(resp))
        except Exception as e:
            return AnalyzeResult(ok=False, message=f"Deepgram analyze error: {e}")

