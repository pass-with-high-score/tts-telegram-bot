# Telegram Audio-to-Text Bot (Deepgram)

This bot lets users upload audio (voice notes, audio files) and returns a `.txt` file containing the transcription using Deepgram.

## Features
- Accepts Telegram `voice`, `audio`, `video_note`, and audio `document` uploads
- Uses Deepgram prerecorded transcription API with smart formatting and punctuation
- Replies with a downloadable `.txt` file

## Setup

1) Python 3.9+

2) Install dependencies:
```
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

3) Configure tokens (recommended via env vars or `.env`):
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
- `DEEPGRAM_API_KEY`: Your Deepgram API key

Create `.env` from the example:
```
cp .env.example .env
# edit .env and fill values
```

Alternatively, the bot will also attempt to read `info.txt` if present with lines:
```
Bot token: <telegram_token>
Deepgram token: <deepgram_api_key>
```

4) Install/refresh dependencies (pin uses Deepgram v2 for Python 3.9):
```
pip install -U -r requirements.txt
```

5) Run the bot:
```
python bot.py
```

## Usage
- Send the bot a voice note or audio file
- The bot will reply with a `.txt` document containing the transcription

## Notes
- Deepgram supports many audio formats (ogg/opus, mp3, m4a, wav, etc.). The bot passes the file bytes with the best-known mimetype.
- For best accuracy, ensure the audio is clear and not overly compressed.
- Do not commit your real tokens. Use env vars or a local `.env` file.

## Troubleshooting
- If you see a message about missing configuration, ensure the env vars are set or `.env` contains both keys.
- Network connectivity is required for Deepgram to transcribe.
- If transcription is empty, the audio may be silent, too noisy, or unsupported.
