import os
import re
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv


def _parse_info_txt_for_tokens(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort parse of info.txt for tokens. Avoids logging secrets.
    Returns (telegram_bot_token, deepgram_api_key).
    """
    tg = None
    dg = None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return tg, dg

    # Expected lines like:
    # Bot token: <TOKEN>
    # Deepgram token: <TOKEN>
    bot_match = re.search(r"(?im)^\s*Bot token:\s*([^\s]+)\s*$", text)
    dg_match = re.search(r"(?im)^\s*Deepgram token:\s*([^\s]+)\s*$", text)
    if bot_match:
        tg = bot_match.group(1).strip()
    if dg_match:
        dg = dg_match.group(1).strip()
    return tg, dg


def load_config() -> Tuple[str, str]:
    """Load TELEGRAM_BOT_TOKEN and DEEPGRAM_API_KEY from environment or info.txt.

    Order of precedence:
    1) Environment variables (optionally from .env via python-dotenv)
    2) info.txt in project root (if present)
    """
    load_dotenv()  # loads from .env if present

    tg = os.getenv("TELEGRAM_BOT_TOKEN")
    dg = os.getenv("DEEPGRAM_API_KEY")

    if tg and dg:
        return tg, dg

    # Fallback to info.txt if available
    info_path = Path("info.txt")
    if info_path.exists():
        tg2, dg2 = _parse_info_txt_for_tokens(info_path)
        tg = tg or tg2
        dg = dg or dg2

    missing = []
    if not tg:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not dg:
        missing.append("DEEPGRAM_API_KEY")
    if missing:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing) +
            ". Set them as env vars or in .env (or provide info.txt)."
        )

    return tg, dg

