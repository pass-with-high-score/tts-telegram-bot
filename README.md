# Telegram Audio-to-Text Bot (Deepgram)

This bot lets users upload audio (voice notes, audio files) and returns a `.txt` file containing the transcription using Deepgram.

## Features
- Accepts Telegram `voice`, `audio`, `video_note`, and audio `document` uploads
- Uses Deepgram prerecorded transcription API with smart formatting and punctuation
- Replies with a downloadable `.txt` file

## Setup

1) Python 3.10+

2) Install dependencies:
```
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

3) Configure tokens (recommended via env vars or `.env`):
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
- `DEEPGRAM_API_KEY`: Your Deepgram API key
 - `DATABASE_URL` (optional): Postgres URL to persist per-user settings

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

4) Run the bot:
```
python bot.py
```

### Optional: Persist user settings with Postgres (Supabase)

If you want each user's language/model and Text Intelligence preferences to persist across restarts, provide a Postgres connection string via `DATABASE_URL` (the bot auto-creates the `user_settings` table):

```
DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<database>
```

For Supabase:
- In your project, go to Settings → Database → Connection string, pick "URI".
- Use the host `db.<project_ref>.supabase.co` and the database password you configured.
- Example:
  `postgresql://postgres:<your-db-password>@db.<project_ref>.supabase.co:5432/postgres`

Notes:
- The bot uses a small connection pool and will create the `user_settings` table if missing.
- If `DATABASE_URL` is not set, settings are stored in-memory and reset on restart.

## Usage
- Send the bot a voice note or audio file
- The bot will reply with a `.txt` document containing the transcription

### Commands
- `/language <English|Vietnamese|en|vi>` — set the bot's interface language
- `/speechlang <English|Vietnamese|en|vi|auto>` — set speech recognition language
- `/status` — show current language/model settings
- `/lang <code|auto>` — set language (e.g., `en-US`, `vi`) or enable auto-detect
- `/detect <on|off>` — toggle language detection explicitly
- `/model <name>` — set model (e.g., `nova-2`). Send without a name to reset default

Text Intelligence
- `/analyze <text>` — Analyze text (summary, topics, intents, sentiment)
- `/anstatus` — Show Text Intelligence settings
- `/summarize <off|v2>` — Summarizer
- `/topics <on|off>` — Topic detection
- `/intents <on|off>` — Intent detection
- `/sentiment <on|off>` — Sentiment analysis
- `/anlang <code>` — Analysis language (e.g., `en`, `vi`)
- You can also upload `.txt/.md/.srt/.vtt` files to analyze their contents

Admin (for user id 1578783338)
- `/admin` — list admin commands
- `/adminstatus` — show DB status and user count
- `/adminget [chat_id]` — show stored settings for a user (defaults to current chat)
- `/adminset <chat_id> <stt|ti>.<field> <value>` — update a setting
  - STT fields: `stt.language`, `stt.detect_language` (on/off), `stt.model`
  - TI fields: `ti.language`, `ti.summarize`, `ti.topics` (on/off), `ti.intents` (on/off), `ti.sentiment` (on/off)

Tip for Vietnamese (vi)
- On Deepgram v2, some language/model combos may 400. If that happens, try `/lang auto`. For best results, upgrade to Python 3.10+ and use model `nova-2`.

Enable Text Intelligence
- Create a Python 3.10+ virtualenv and install Deepgram v3:
  - `python3.10 -m venv .venv && source .venv/bin/activate`
  - `pip install -U pip`
  - `pip install -U deepgram-sdk>=3`
- Optionally update `requirements.txt` to `deepgram-sdk>=3.0.0` if you are moving the whole project to Python 3.10+.

## Notes
- Deepgram supports many audio formats (ogg/opus, mp3, m4a, wav, etc.). The bot passes the file bytes with the best-known mimetype.
- For best accuracy, ensure the audio is clear and not overly compressed.
- Do not commit your real tokens. Use env vars or a local `.env` file.

## Troubleshooting
- If you see a message about missing configuration, ensure the env vars are set or `.env` contains both keys.
- Network connectivity is required for Deepgram to transcribe.
- If transcription is empty, the audio may be silent, too noisy, or unsupported.
 - If DB persistence doesn't work, verify `DATABASE_URL` is correct and your IP/network can reach the Supabase Postgres endpoint.
