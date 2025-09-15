import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import load_config
from db import (
    ensure_schema as db_ensure_schema,
    is_enabled as db_is_enabled,
    get_lang_settings as db_get_lang_settings,
    save_lang_settings as db_save_lang_settings,
    get_ti_settings as db_get_ti_settings,
    save_ti_settings as db_save_ti_settings,
    get_user_count as db_get_user_count,
    get_ui_language as db_get_ui_language,
    save_ui_language as db_save_ui_language,
)
from transcribe import DeepgramTranscriber
from text_intelligence import TextAnalyzer


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tl-bot-stt")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = t(update, context, "start_message")
    await update.message.reply_text(msg)


ADMIN_USER_ID = 1578783338


def _is_admin(update: Update) -> bool:
    try:
        uid = update.effective_user.id if update.effective_user else None
        return uid == ADMIN_USER_ID
    except Exception:
        return False


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Not allowed.")
        return
    await update.message.reply_text(
        "Admin commands:\n"
        "/adminstatus — DB status and user count\n"
        "/adminget [chat_id] — show stored settings\n"
        "/adminset <chat_id> <stt|ti>.<field> <value> — update a setting"
    )


async def admin_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Not allowed.")
        return
    enabled = db_is_enabled()
    if not enabled:
        await update.message.reply_text("Database: not configured")
        return
    try:
        import asyncio as _asyncio
        count = await _asyncio.to_thread(db_get_user_count)
    except Exception:
        count = None
    await update.message.reply_text(
        f"Database: enabled\nuser_settings rows: {count if count is not None else '(unknown)'}"
    )


async def admin_get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Not allowed.")
        return
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        try:
            chat_id = int(parts[1].strip().split()[0])
        except Exception:
            await update.message.reply_text("Usage: /adminget [chat_id]")
            return
    else:
        chat_id = update.effective_chat.id
    lang = db_get_lang_settings(chat_id)
    ti = db_get_ti_settings(chat_id)
    msg = (
        f"chat_id: {chat_id}\n"
        f"stt.language: {lang.get('language')}\n"
        f"stt.detect_language: {lang.get('detect_language')}\n"
        f"stt.model: {lang.get('model') or '(default)'}\n"
        f"ti.language: {ti.get('language')}\n"
        f"ti.summarize: {ti.get('summarize')}\n"
        f"ti.topics: {ti.get('topics')}\n"
        f"ti.intents: {ti.get('intents')}\n"
        f"ti.sentiment: {ti.get('sentiment')}\n"
    )
    await update.message.reply_text(msg)


def _parse_bool(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if v in {"on", "true", "yes", "1"}:
        return True
    if v in {"off", "false", "no", "0"}:
        return False
    return None


async def admin_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Not allowed.")
        return
    text = (update.message.text or "")
    parts = text.split(maxsplit=3)
    if len(parts) < 4:
        await update.message.reply_text(
            "Usage: /adminset <chat_id> <stt|ti>.<field> <value>"
        )
        return
    try:
        chat_id = int(parts[1])
    except Exception:
        await update.message.reply_text("Invalid chat_id")
        return
    field = parts[2].strip()
    value = parts[3].strip()

    try:
        if field.startswith("stt."):
            key = field[4:]
            if key not in {"language", "detect_language", "model"}:
                await update.message.reply_text("Unknown STT field")
                return
            current = db_get_lang_settings(chat_id)
            if key == "detect_language":
                b = _parse_bool(value)
                if b is None:
                    await update.message.reply_text("detect_language expects on/off|true/false|1/0")
                    return
                current[key] = b
            else:
                current[key] = value
            import asyncio as _asyncio
            await _asyncio.to_thread(db_save_lang_settings, chat_id, current)
            await update.message.reply_text("Updated STT setting.")
            return
        elif field.startswith("ti."):
            key = field[3:]
            if key not in {"language", "summarize", "topics", "intents", "sentiment"}:
                await update.message.reply_text("Unknown TI field")
                return
            current = db_get_ti_settings(chat_id)
            if key in {"topics", "intents", "sentiment"}:
                b = _parse_bool(value)
                if b is None:
                    await update.message.reply_text("Boolean expected: on/off|true/false|1/0")
                    return
                current[key] = b
            else:
                current[key] = value
            import asyncio as _asyncio
            await _asyncio.to_thread(db_save_ti_settings, chat_id, current)
            await update.message.reply_text("Updated TI setting.")
            return
        else:
            await update.message.reply_text("Field must start with stt. or ti.")
    except Exception:
        logger.exception("adminset failed")
        await update.message.reply_text("Failed to update setting.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t(update, context, "help_message"))


# UI language per-user (en|vi)
def _get_ui_lang(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    store = context.application.bot_data.setdefault("_ui_lang", {})
    v = store.get(chat_id)
    if not v:
        v = db_get_ui_language(chat_id)
        store[chat_id] = v
    return v


def t(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    lang = _get_ui_lang(context, chat_id)
    en = {
        "start_message": "Send me an audio file or voice note, and I'll return a transcription as a .txt file.",
        "help_message": (
            "Usage:\n"
            "- Send a voice message, audio, or upload an audio file.\n"
            "- I will process and reply with a text file.\n\n"
            "Interface language:\n"
            "/language <English|Vietnamese|en|vi> — set bot language\n\n"
            "Speech recognition options:\n"
            "/speechlang <English|Vietnamese|en|vi|auto> — set speech language\n"
            "/status — show current language/model settings\n"
            "/lang <code|auto> — set language (e.g., en-US, vi) or auto-detect\n"
            "/detect <on|off> — toggle language detection\n"
            "/model <name> — set model (e.g., nova-2). Leave blank to reset default.\n\n"
            "Text Intelligence:\n"
            "/analyze <text> — summarize, topics, intents, sentiment\n"
            "/anstatus — show TI settings\n"
            "/summarize <off|v2>\n"
            "/topics <on|off>\n"
            "/intents <on|off>\n"
            "/sentiment <on|off>\n"
            "/anlang <code> — TI language (e.g., en, vi)\n"
            "Or upload a .txt/.md/.srt/.vtt file to analyze contents."
        ),
        "analyze_requires_upgrade": "Text Intelligence isn't available right now.",
        "couldnt_download_file": "Couldn't download that file.",
        "analyzing_text": "Analyzing text…",
        "analyzing_file_text": "Analyzing file text…",
        "transcribing": "Transcribing… this may take a moment.",
        "transcription_empty": "Transcription came back empty. The audio may be too quiet or unsupported.",
        "transcription_caption": "Here is your transcription.",
        "transcribe_failed": "Sorry, I couldn't transcribe that audio.",
        "ui_lang_set_en": "Interface language set to English.",
        "ui_lang_set_vi": "Đã chuyển ngôn ngữ hiển thị sang Tiếng Việt.",
        "language_usage": "Usage: /language <English|Vietnamese|en|vi>",
        "speechlang_usage": "Usage: /speechlang <English|Vietnamese|en|vi|auto>",
        "speechlang_set_en": "Speech recognition language set to English (en-US).",
        "speechlang_set_vi": "Speech recognition language set to Vietnamese (vi).",
        "speechlang_set_auto": "Language detection enabled for speech recognition.",
    }
    vi = {
        "start_message": "Gửi cho tôi file âm thanh hoặc voice note, tôi sẽ trả về bản ghi (.txt).",
        "help_message": (
            "Cách dùng:\n"
            "- Gửi voice, audio hoặc tải lên file âm thanh.\n"
            "- Tôi sẽ xử lý và gửi lại file văn bản.\n\n"
            "Ngôn ngữ giao diện:\n"
            "/language <English|Vietnamese|en|vi> — đổi ngôn ngữ bot\n\n"
            "Tùy chọn nhận dạng giọng nói:\n"
            "/speechlang <English|Vietnamese|en|vi|auto> — đặt ngôn ngữ nhận dạng\n"
            "/status — xem cài đặt ngôn ngữ/mô hình\n"
            "/lang <code|auto> — đặt ngôn ngữ (vd: en-US, vi) hoặc tự động\n"
            "/detect <on|off> — bật/tắt tự phát hiện ngôn ngữ\n"
            "/model <name> — đặt mô hình (vd: nova-2). Bỏ trống để về mặc định.\n\n"
            "Text Intelligence:\n"
            "/analyze <text> — tóm tắt, chủ đề, ý định, cảm xúc\n"
            "/anstatus — xem cài đặt TI\n"
            "/summarize <off|v2>\n"
            "/topics <on|off>\n"
            "/intents <on|off>\n"
            "/sentiment <on|off>\n"
            "/anlang <code> — ngôn ngữ phân tích (vd: en, vi)\n"
            "Hoặc tải lên file .txt/.md/.srt/.vtt để phân tích."
        ),
        "analyze_requires_upgrade": "Tính năng Phân tích văn bản hiện chưa khả dụng.",
        "couldnt_download_file": "Không tải được file.",
        "analyzing_text": "Đang phân tích văn bản…",
        "analyzing_file_text": "Đang phân tích nội dung file…",
        "transcribing": "Đang chuyển giọng nói thành văn bản…",
        "transcription_empty": "Kết quả trống. Âm thanh quá nhỏ hoặc không hỗ trợ.",
        "transcription_caption": "Bản ghi của bạn đây.",
        "transcribe_failed": "Xin lỗi, tôi không thể chuyển âm thanh này.",
        "ui_lang_set_en": "Đã chuyển ngôn ngữ hiển thị sang English.",
        "ui_lang_set_vi": "Đã chuyển ngôn ngữ hiển thị sang Tiếng Việt.",
        "language_usage": "Cú pháp: /language <English|Vietnamese|en|vi>",
        "speechlang_usage": "Cú pháp: /speechlang <English|Vietnamese|en|vi|auto>",
        "speechlang_set_en": "Đã đặt ngôn ngữ nhận dạng thành English (en-US).",
        "speechlang_set_vi": "Đã đặt ngôn ngữ nhận dạng thành Tiếng Việt (vi).",
        "speechlang_set_auto": "Đã bật tự phát hiện ngôn ngữ cho nhận dạng giọng nói.",
    }
    table = vi if lang == "vi" else en
    return table.get(key, en.get(key, key))


def _parse_ui_lang(arg: str) -> str:
    a = (arg or "").strip().lower()
    if a in {"vi", "vietnamese", "viet", "tiếng việt", "tieng viet", "vn"}:
        return "vi"
    if a in {"en", "english"}:
        return "en"
    return ""


async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text(t(update, context, "language_usage"))
        return
    lang = _parse_ui_lang(parts[1])
    if not lang:
        await update.message.reply_text(t(update, context, "language_usage"))
        return
    # Save to cache and DB
    context.application.bot_data.setdefault("_ui_lang", {})[chat_id] = lang
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ui_language, chat_id, lang)
    except Exception:
        pass
    # Confirm in selected language
    key = "ui_lang_set_vi" if lang == "vi" else "ui_lang_set_en"
    await update.message.reply_text(t(update, context, key))


async def speechlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text(t(update, context, "speechlang_usage"))
        return
    arg = parts[1].strip()
    a = arg.lower()
    cfg = _get_lang_cfg(context, chat_id)
    if a in {"auto", "detect"}:
        cfg["detect_language"] = True
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
        except Exception:
            pass
        await update.message.reply_text(t(update, context, "speechlang_set_auto"))
        return
    lang = _parse_ui_lang(a)
    if not lang:
        await update.message.reply_text(t(update, context, "speechlang_usage"))
        return
    if lang == "en":
        cfg["language"] = "en-US"
        cfg["detect_language"] = False
        key = "speechlang_set_en"
    else:
        cfg["language"] = "vi"
        cfg["detect_language"] = False
        key = "speechlang_set_vi"
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
    except Exception:
        pass
    await update.message.reply_text(t(update, context, key))


def _get_ti_cfg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict:
    store = context.application.bot_data.setdefault("_ti_cfg", {})
    cfg = store.get(chat_id)
    if not cfg:
        # Load from DB if configured; otherwise defaults
        cfg = db_get_ti_settings(chat_id)
        store[chat_id] = cfg
    return cfg


async def ti_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cfg = _get_ti_cfg(context, chat_id)
    await update.message.reply_text(
        "Text Intelligence settings:\n"
        f"language: {cfg.get('language')}\n"
        f"summarize: {cfg.get('summarize')}\n"
        f"topics: {cfg.get('topics')}\n"
        f"intents: {cfg.get('intents')}\n"
        f"sentiment: {cfg.get('sentiment')}\n"
    )


def _parse_bool_arg(text: str) -> Optional[bool]:
    a = (text or "").strip().lower()
    if a in {"on", "true", "yes", "1"}:
        return True
    if a in {"off", "false", "no", "0"}:
        return False
    return None


async def summarize_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip().lower() not in {"off", "v2"}:
        await update.message.reply_text("Usage: /summarize <off|v2>")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["summarize"] = parts[1].strip().lower()
    await update.message.reply_text(f"summarize set to {cfg['summarize']}")
    # Persist
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ti_settings, chat_id, cfg)
    except Exception:
        pass


async def topics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    v = _parse_bool_arg(parts[1] if len(parts) > 1 else "")
    if v is None:
        await update.message.reply_text("Usage: /topics <on|off>")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["topics"] = v
    await update.message.reply_text(f"topics set to {cfg['topics']}")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ti_settings, chat_id, cfg)
    except Exception:
        pass


async def intents_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    v = _parse_bool_arg(parts[1] if len(parts) > 1 else "")
    if v is None:
        await update.message.reply_text("Usage: /intents <on|off>")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["intents"] = v
    await update.message.reply_text(f"intents set to {cfg['intents']}")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ti_settings, chat_id, cfg)
    except Exception:
        pass


async def sentiment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    v = _parse_bool_arg(parts[1] if len(parts) > 1 else "")
    if v is None:
        await update.message.reply_text("Usage: /sentiment <on|off>")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["sentiment"] = v
    await update.message.reply_text(f"sentiment set to {cfg['sentiment']}")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ti_settings, chat_id, cfg)
    except Exception:
        pass


async def anlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /anlang <code> (e.g., en, vi, ja)")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["language"] = parts[1].strip().split()[0]
    await update.message.reply_text(f"analysis language set to {cfg['language']}")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_ti_settings, chat_id, cfg)
    except Exception:
        pass


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_token, dg_key = context.bot_data.get("_cfg", (None, None))
    if not dg_key:
        tg_token, dg_key = load_config()
        context.bot_data["_cfg"] = (tg_token, dg_key)

    analyzer = TextAnalyzer(dg_key)
    if not analyzer.is_available():
        await update.message.reply_text(t(update, context, "analyze_requires_upgrade"))
        return

    cfg = _get_ti_cfg(context, update.effective_chat.id)
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /analyze <text> or upload a .txt/.md/.srt/.vtt file")
        return
    content = parts[1]

    options = {
        "language": cfg.get("language"),
        "summarize": cfg.get("summarize"),
        "topics": cfg.get("topics"),
        "intents": cfg.get("intents"),
        "sentiment": cfg.get("sentiment"),
    }

    await update.message.reply_text(t(update, context, "analyzing_text"))
    # Run in a thread since v3 SDK call is sync
    import asyncio as _asyncio
    result = await _asyncio.to_thread(analyzer.analyze_text, content, options)
    if not result.ok:
        await update.message.reply_text(result.message)
        return

    try:
        from io import BytesIO
        bio = BytesIO(result.raw_json.encode("utf-8"))
        bio.name = "analysis.json"
        await update.message.reply_document(bio, filename="analysis.json", caption="Text Intelligence result")
    except Exception:
        await update.message.reply_text(result.raw_json or "(no content)")


async def handle_text_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.document:
        return

    tg_token, dg_key = context.bot_data.get("_cfg", (None, None))
    if not dg_key:
        tg_token, dg_key = load_config()
        context.bot_data["_cfg"] = (tg_token, dg_key)

    analyzer = TextAnalyzer(dg_key)
    if not analyzer.is_available():
        await message.reply_text(t(update, context, "analyze_requires_upgrade"))
        return

    cfg = _get_ti_cfg(context, update.effective_chat.id)
    try:
        file = await message.document.get_file()
        from io import BytesIO
        buf = BytesIO()
        await file.download_to_memory(out=buf)
        data = buf.getvalue()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    except Exception:
        await message.reply_text(t(update, context, "couldnt_download_file"))
        return

    options = {
        "language": cfg.get("language"),
        "summarize": cfg.get("summarize"),
        "topics": cfg.get("topics"),
        "intents": cfg.get("intents"),
        "sentiment": cfg.get("sentiment"),
    }

    await message.reply_text(t(update, context, "analyzing_file_text"))
    import asyncio as _asyncio
    result = await _asyncio.to_thread(analyzer.analyze_text, text, options)
    if not result.ok:
        await message.reply_text(result.message)
        return

    try:
        from io import BytesIO
        bio = BytesIO(result.raw_json.encode("utf-8"))
        bio.name = "analysis.json"
        await message.reply_document(bio, filename="analysis.json", caption="Text Intelligence result")
    except Exception:
        await message.reply_text(result.raw_json or "(no content)")


def _get_lang_cfg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict:
    store = context.application.bot_data.setdefault("_lang_cfg", {})
    cfg = store.get(chat_id)
    if not cfg:
        cfg = db_get_lang_settings(chat_id)
        store[chat_id] = cfg
    return cfg


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cfg = _get_lang_cfg(context, chat_id)
    lang = cfg.get("language")
    detect = cfg.get("detect_language")
    await update.message.reply_text(
        f"language: {lang}\n"
        f"detect_language: {detect}\n"
        f"model: {cfg.get('model') or '(default)'}"
    )


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /lang <code|auto>")
        return
    arg = parts[1].strip()
    cfg = _get_lang_cfg(context, chat_id)
    if arg.lower() == "auto":
        cfg["detect_language"] = True
        await update.message.reply_text("Language detection enabled.")
    else:
        cfg["language"] = arg
        cfg["detect_language"] = False
        await update.message.reply_text(f"Language set to {arg}.")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
    except Exception:
        pass


async def detect_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /detect <on|off>")
        return
    arg = parts[1].strip().lower()
    if arg not in {"on", "off"}:
        await update.message.reply_text("Usage: /detect <on|off>")
        return
    cfg = _get_lang_cfg(context, chat_id)
    cfg["detect_language"] = (arg == "on")
    await update.message.reply_text(f"detect_language set to {cfg['detect_language']}")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
    except Exception:
        pass


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    cfg = _get_lang_cfg(context, chat_id)
    if len(parts) < 2:
        cfg["model"] = ""
        await update.message.reply_text("Model reset to default.")
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
        except Exception:
            pass
        return
    model = parts[1].strip()
    cfg["model"] = model
    await update.message.reply_text(f"Model set to {model or '(default)'}.")
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(db_save_lang_settings, chat_id, cfg)
    except Exception:
        pass


def _build_temp_filename(base_dir: Path, suggested_name: Optional[str], default_ext: str = ".ogg") -> Path:
    safe_name = (suggested_name or "audio").replace("/", "_").replace("\\", "_")
    if not os.path.splitext(safe_name)[1]:
        safe_name += default_ext
    return base_dir / safe_name


async def _download_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Tuple[Path, Optional[str]]:
    """Download the incoming audio/voice/document to a temporary file.
    Returns (file_path, mime_type_if_known).
    """
    message = update.effective_message
    if not message:
        raise RuntimeError("No message found in update")

    temp_dir = Path(tempfile.mkdtemp(prefix="tg_audio_"))
    mime: Optional[str] = None
    file_path: Optional[Path] = None

    try:
        if message.voice:
            file = await message.voice.get_file()
            mime = message.voice.mime_type
            file_path = _build_temp_filename(temp_dir, file.file_path.split("/")[-1], ".ogg")
        elif message.audio:
            file = await message.audio.get_file()
            mime = message.audio.mime_type
            # Prefer original filename if available
            name = message.audio.file_name or file.file_path.split("/")[-1]
            file_path = _build_temp_filename(temp_dir, name, ".mp3")
        elif message.video_note:
            file = await message.video_note.get_file()
            mime = None  # Telegram does not expose a mimetype for video_note
            file_path = _build_temp_filename(temp_dir, file.file_path.split("/")[-1], ".mp4")
        elif message.document:
            # Support uploaded files that are audio (e.g., .wav, .m4a, .mp3)
            file = await message.document.get_file()
            mime = message.document.mime_type
            name = message.document.file_name or file.file_path.split("/")[-1]
            file_path = _build_temp_filename(temp_dir, name, ".bin")
        else:
            raise RuntimeError("No supported audio found in message.")

        await file.download_to_drive(custom_path=str(file_path))
        return file_path, mime
    except Exception:
        # Cleanup if we failed after creating temp dir
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    # Show chat action while processing
    try:
        chat_id = update.effective_chat.id if update.effective_chat else message.chat_id
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass

    # Prepare transcriber
    tg_token, dg_key = context.bot_data.get("_cfg", (None, None))
    if not dg_key:
        # Load once and memoize
        tg_token, dg_key = load_config()
        context.bot_data["_cfg"] = (tg_token, dg_key)

    transcriber = DeepgramTranscriber(dg_key)
    lang_cfg = _get_lang_cfg(context, update.effective_chat.id)

    # Download the file to temp
    try:
        file_path, mime = await _download_audio(update, context)
    except Exception:
        logger.exception("Failed to download audio")
        await message.reply_text(t(update, context, "couldnt_download_file"))
        return

    try:
        # Notify user
        await message.reply_text(t(update, context, "transcribing"))

        # Transcribe with language settings
        dg_opts = {"detect_language": True} if lang_cfg.get("detect_language") else {"language": lang_cfg.get("language")}
        # If a specific model is set, include it (e.g., nova-2 for Vietnamese on v3)
        if lang_cfg.get("model"):
            dg_opts["model"] = lang_cfg["model"]
        # For Vietnamese, default to nova-2 if no model set (v3). If running on v2, this may 400.
        if dg_opts.get("language") in {"vi", "vi-VN"} and not lang_cfg.get("model"):
            dg_opts["model"] = "nova-2"
        try:
            result = await transcriber.transcribe_file(file_path, explicit_mime=mime, options=dg_opts)
        except Exception:
            # Fallback: detect language with minimal options
            logger.warning("Primary transcription failed; retrying with detect_language only…", exc_info=True)
            fb_opts = {"detect_language": True}
            result = await transcriber.transcribe_file(file_path, explicit_mime=mime, options=fb_opts)
        text = result.text.strip()
        if not text:
            await message.reply_text(t(update, context, "transcription_empty"))
            return

        # Write to a temporary .txt and send back
        out_dir = file_path.parent
        out_file = out_dir / (file_path.stem + ".txt")
        out_file.write_text(text, encoding="utf-8")

        # Use upload_document action for better UX
        try:
            chat_id = update.effective_chat.id if update.effective_chat else message.chat_id
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        except Exception:
            pass

        with out_file.open("rb") as f:
            await message.reply_document(document=InputFile(f, filename=out_file.name), caption=t(update, context, "transcription_caption"))
    except Exception as ex:
        logger.exception("Transcription failed")
        msg = str(ex) if ex else ""
        if "DG: 400" in msg and ("language=vi" in msg or "language=vi-VN" in msg):
            await message.reply_text(
                "Deepgram returned 400 for Vietnamese on this model.\n"
                "If you're on Python 3.9/v2 SDK, please upgrade to Python 3.10+ and reinstall deps.\n"
                "Then set: /lang vi and /model nova-2, and resend the audio."
            )
        else:
            await message.reply_text(t(update, context, "transcribe_failed"))
    finally:
        # Cleanup temp files
        try:
            shutil.rmtree(file_path.parent, ignore_errors=True)
        except Exception:
            pass


def build_app(tg_token: str) -> Application:
    app = Application.builder().token(tg_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("language", language_cmd))
    # Text Intelligence commands (gated at runtime)
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("anstatus", ti_status_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("topics", topics_cmd))
    app.add_handler(CommandHandler("intents", intents_cmd))
    app.add_handler(CommandHandler("sentiment", sentiment_cmd))
    app.add_handler(CommandHandler("anlang", anlang_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))
    app.add_handler(CommandHandler("detect", detect_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("speechlang", speechlang_cmd))

    # Admin commands (restricted by user id)
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("adminstatus", admin_status_cmd))
    app.add_handler(CommandHandler("adminget", admin_get_cmd))
    app.add_handler(CommandHandler("adminset", admin_set_cmd))

    audio_filter = (
        filters.VOICE
        | filters.AUDIO
        | filters.VIDEO_NOTE
        | filters.Document.FileExtension("wav")
        | filters.Document.FileExtension("mp3")
        | filters.Document.FileExtension("m4a")
        | filters.Document.FileExtension("ogg")
        | filters.Document.FileExtension("oga")
        | filters.Document.FileExtension("webm")
        | filters.Document.FileExtension("flac")
        | filters.Document.FileExtension("aac")
    )
    app.add_handler(MessageHandler(audio_filter, handle_audio))
    # Text documents to analyze
    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt")
            | filters.Document.FileExtension("md")
            | filters.Document.FileExtension("srt")
            | filters.Document.FileExtension("vtt"),
            handle_text_document,
        )
    )

    return app


def main():
    tg_token, _ = load_config()
    # Initialize database schema if configured
    try:
        db_ensure_schema()
        if db_is_enabled():
            logger.info("Database: enabled (settings will be persisted)")
        else:
            logger.info("Database: not configured; settings will be in-memory only")
    except Exception:
        logger.exception("Database initialization failed; continuing without persistence")
    app = build_app(tg_token)
    logger.info("Bot is starting…")
    # Run polling in the current thread (handles setup/shutdown internally)
    app.run_polling()


if __name__ == "__main__":
    main()
