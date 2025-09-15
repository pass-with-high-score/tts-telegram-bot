import os
from typing import Optional, Dict, Any

from dotenv import load_dotenv

try:
    from psycopg_pool import ConnectionPool  # type: ignore
    import psycopg  # type: ignore
except Exception:  # pragma: no cover - optional dependency until configured
    ConnectionPool = None  # type: ignore
    psycopg = None  # type: ignore


_POOL: Optional["ConnectionPool"] = None


def _get_db_url() -> Optional[str]:
    # Load env from .env if present
    load_dotenv()
    return os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")


def _ensure_pool() -> Optional["ConnectionPool"]:
    global _POOL
    if _POOL is not None:
        return _POOL
    db_url = _get_db_url()
    if not db_url or ConnectionPool is None:
        return None
    # Initialize a small pool suitable for a bot
    _POOL = ConnectionPool(db_url, min_size=1, max_size=4, kwargs={"autocommit": True})
    return _POOL


def is_enabled() -> bool:
    return _ensure_pool() is not None


def ensure_schema() -> None:
    pool = _ensure_pool()
    if not pool:
        return
    ddl = """
    create table if not exists user_settings (
        chat_id        bigint primary key,
        -- speech-to-text settings
        language       text not null default 'en-US',
        detect_language boolean not null default false,
        model          text not null default '',
        -- text intelligence settings
        ti_language    text not null default 'en',
        summarize      text not null default 'v2',
        topics         boolean not null default true,
        intents        boolean not null default true,
        sentiment      boolean not null default true,
        -- bot UI language (en|vi)
        ui_language    text not null default 'en',
        created_at     timestamptz not null default now(),
        updated_at     timestamptz not null default now()
    );

    create or replace function set_updated_at()
    returns trigger as $$
    begin
      new.updated_at = now();
      return new;
    end;
    $$ language plpgsql;

    do $$ begin
      if not exists (
        select 1 from pg_trigger
        where tgrelid = 'user_settings'::regclass and tgname = 'user_settings_set_updated_at'
      ) then
        create trigger user_settings_set_updated_at
        before update on user_settings
        for each row execute function set_updated_at();
      end if;
    end $$;
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            # Ensure ui_language exists for older tables
            cur.execute("alter table if exists user_settings add column if not exists ui_language text not null default 'en'")


# Defaults used when no row exists yet
_LANG_DEFAULT = {"detect_language": False, "language": "en-US", "model": ""}
_TI_DEFAULT = {
    "language": "en",  # TI language
    "summarize": "v2",
    "topics": True,
    "intents": True,
    "sentiment": True,
}


def get_lang_settings(chat_id: int) -> Dict[str, Any]:
    pool = _ensure_pool()
    if not pool:
        return dict(_LANG_DEFAULT)
    sql = "select language, detect_language, model from user_settings where chat_id = %s"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (chat_id,))
            row = cur.fetchone()
            if not row:
                return dict(_LANG_DEFAULT)
            language, detect_language, model = row
            return {
                "language": language or _LANG_DEFAULT["language"],
                "detect_language": bool(detect_language) if detect_language is not None else _LANG_DEFAULT["detect_language"],
                "model": model or _LANG_DEFAULT["model"],
            }


def save_lang_settings(chat_id: int, cfg: Dict[str, Any]) -> None:
    pool = _ensure_pool()
    if not pool:
        return
    # On first insert, also set TI defaults; on conflict, update only lang fields
    sql = (
        "insert into user_settings (chat_id, language, detect_language, model, ti_language, summarize, topics, intents, sentiment) "
        "values (%s, %s, %s, %s, 'en', 'v2', true, true, true) "
        "on conflict (chat_id) do update set language = excluded.language, detect_language = excluded.detect_language, model = excluded.model"
    )
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    chat_id,
                    cfg.get("language", _LANG_DEFAULT["language"]),
                    cfg.get("detect_language", _LANG_DEFAULT["detect_language"]),
                    cfg.get("model", _LANG_DEFAULT["model"]),
                ),
            )


def get_ti_settings(chat_id: int) -> Dict[str, Any]:
    pool = _ensure_pool()
    if not pool:
        return dict(_TI_DEFAULT)
    sql = "select ti_language, summarize, topics, intents, sentiment from user_settings where chat_id = %s"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (chat_id,))
            row = cur.fetchone()
            if not row:
                return dict(_TI_DEFAULT)
            ti_language, summarize, topics, intents, sentiment = row
            return {
                "language": ti_language or _TI_DEFAULT["language"],
                "summarize": summarize or _TI_DEFAULT["summarize"],
                "topics": bool(topics) if topics is not None else _TI_DEFAULT["topics"],
                "intents": bool(intents) if intents is not None else _TI_DEFAULT["intents"],
                "sentiment": bool(sentiment) if sentiment is not None else _TI_DEFAULT["sentiment"],
            }


def save_ti_settings(chat_id: int, cfg: Dict[str, Any]) -> None:
    pool = _ensure_pool()
    if not pool:
        return
    # On first insert, also set STT defaults; on conflict, update only TI fields
    sql = (
        "insert into user_settings (chat_id, ti_language, summarize, topics, intents, sentiment, language, detect_language, model) "
        "values (%s, %s, %s, %s, %s, %s, 'en-US', false, '') "
        "on conflict (chat_id) do update set ti_language = excluded.ti_language, summarize = excluded.summarize, topics = excluded.topics, intents = excluded.intents, sentiment = excluded.sentiment"
    )
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    chat_id,
                    cfg.get("language", _TI_DEFAULT["language"]),
                    cfg.get("summarize", _TI_DEFAULT["summarize"]),
                    cfg.get("topics", _TI_DEFAULT["topics"]),
                    cfg.get("intents", _TI_DEFAULT["intents"]),
                    cfg.get("sentiment", _TI_DEFAULT["sentiment"]),
                ),
            )


def get_ui_language(chat_id: int) -> str:
    pool = _ensure_pool()
    if not pool:
        return "en"
    sql = "select ui_language from user_settings where chat_id = %s"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (chat_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return "en"
            v = str(row[0]).lower()
            return "vi" if v.startswith("vi") else "en"


def save_ui_language(chat_id: int, lang: str) -> None:
    pool = _ensure_pool()
    if not pool:
        return
    lang = "vi" if str(lang).lower().startswith("vi") else "en"
    sql = (
        "insert into user_settings (chat_id, ui_language, language, detect_language, model, ti_language, summarize, topics, intents, sentiment) "
        "values (%s, %s, 'en-US', false, '', 'en', 'v2', true, true, true) "
        "on conflict (chat_id) do update set ui_language = excluded.ui_language"
    )
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (chat_id, lang))


def get_user_count() -> Optional[int]:
    pool = _ensure_pool()
    if not pool:
        return None
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from user_settings")
            row = cur.fetchone()
            return int(row[0]) if row else 0
