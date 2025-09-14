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
        "Usage:\n- Send a voice message, audio, or upload an audio file.\n- I will process and reply with a text file."
    )


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

    # Download the file to temp
    try:
        file_path, mime = await _download_audio(update, context)
    except Exception as e:
        logger.exception("Failed to download audio")
        await message.reply_text("Sorry, I couldn't download that file. Please try again.")
        return

    try:
        # Notify user
        await message.reply_text("Transcribing… this may take a moment.")

        # Transcribe
        result = await transcriber.transcribe_file(file_path, explicit_mime=mime)
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
    except Exception as e:
        logger.exception("Transcription failed")
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

    return app


def main():
    tg_token, _ = load_config()
    app = build_app(tg_token)
    logger.info("Bot is starting…")
    # Run polling in the current thread (handles setup/shutdown internally)
    app.run_polling()


if __name__ == "__main__":
    main()
