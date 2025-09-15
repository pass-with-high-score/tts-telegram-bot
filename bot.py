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
from transcribe import DeepgramTranscriber
from text_intelligence import TextAnalyzer


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tl-bot-stt")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me an audio file or voice note, and I'll return a transcription as a .txt file."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Usage:\n"
        "- Send a voice message, audio, or upload an audio file.\n"
        "- I will process and reply with a text file.\n\n"
        "Language options:\n"
        "/status — show current language/model settings\n"
        "/lang <code|auto> — set language (e.g., en-US, vi) or auto-detect\n"
        "/detect <on|off> — toggle language detection\n"
        "/model <name> — set model (e.g., nova-2). Leave blank to reset default.\n\n"
        "Text Intelligence (Python 3.10+ only):\n"
        "/analyze <text> — summarize, topics, intents, sentiment\n"
        "/anstatus — show TI settings\n"
        "/summarize <off|v2>\n"
        "/topics <on|off>\n"
        "/intents <on|off>\n"
        "/sentiment <on|off>\n"
        "/anlang <code> — TI language (e.g., en, vi)\n"
        "Or upload a .txt/.md/.srt/.vtt file to analyze contents."
    )


def _get_ti_cfg(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict:
    store = context.application.bot_data.setdefault("_ti_cfg", {})
    cfg = store.get(chat_id)
    if not cfg:
        cfg = {
            "language": "en",
            "summarize": "v2",
            "topics": True,
            "intents": True,
            "sentiment": True,
        }
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


async def anlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /anlang <code> (e.g., en, vi, ja)")
        return
    cfg = _get_ti_cfg(context, chat_id)
    cfg["language"] = parts[1].strip().split()[0]
    await update.message.reply_text(f"analysis language set to {cfg['language']}")


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_token, dg_key = context.bot_data.get("_cfg", (None, None))
    if not dg_key:
        tg_token, dg_key = load_config()
        context.bot_data["_cfg"] = (tg_token, dg_key)

    analyzer = TextAnalyzer(dg_key)
    if not analyzer.is_available():
        await update.message.reply_text(
            "Text Intelligence requires Python 3.10+ and deepgram-sdk>=3. Upgrade to enable it."
        )
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

    await update.message.reply_text("Analyzing text…")
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
        await message.reply_text("Text Intelligence requires Python 3.10+ and deepgram-sdk>=3.")
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
        await message.reply_text("Couldn't download that file.")
        return

    options = {
        "language": cfg.get("language"),
        "summarize": cfg.get("summarize"),
        "topics": cfg.get("topics"),
        "intents": cfg.get("intents"),
        "sentiment": cfg.get("sentiment"),
    }

    await message.reply_text("Analyzing file text…")
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
        cfg = {"detect_language": False, "language": "en-US", "model": ""}
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


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "")
    parts = text.split(maxsplit=1)
    cfg = _get_lang_cfg(context, chat_id)
    if len(parts) < 2:
        cfg["model"] = ""
        await update.message.reply_text("Model reset to default.")
        return
    model = parts[1].strip()
    cfg["model"] = model
    await update.message.reply_text(f"Model set to {model or '(default)'}.")


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
        await message.reply_text("Sorry, I couldn't download that file. Please try again.")
        return

    try:
        # Notify user
        await message.reply_text("Transcribing… this may take a moment.")

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
            await message.reply_text("Transcription came back empty. The audio may be too quiet or unsupported.")
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
            await message.reply_document(document=InputFile(f, filename=out_file.name), caption="Here is your transcription.")
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
            await message.reply_text("Sorry, I couldn't transcribe that audio.")
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
    app = build_app(tg_token)
    logger.info("Bot is starting…")
    # Run polling in the current thread (handles setup/shutdown internally)
    app.run_polling()


if __name__ == "__main__":
    main()
