import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ALLOWED_TARGET_NUMBER = "+18054398008"

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime-2.1")
REALTIME_VOICE = os.getenv("REALTIME_VOICE", "marin")
TRANSCRIPTION_MODEL = os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-transcribe-diarize")
EVAL_MODEL = os.getenv("EVAL_MODEL", "gpt-5.4-mini")

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


TRACEVOX_API_KEY = os.getenv(
    "TRACEVOX_API_KEY",
    ""
).strip()

_tracevox_base = os.getenv(
    "TRACEVOX_BASE_URL",
    "https://api.tracevox.ai"
).strip().rstrip("/")
if _tracevox_base.endswith("/v1"):
    _tracevox_base = _tracevox_base[: -len("/v1")].rstrip("/")
TRACEVOX_BASE_URL = _tracevox_base

TRACEVOX_GATEWAY_URL = f"{TRACEVOX_BASE_URL}/v1"

TRACEVOX_ENABLED = _env_bool("TRACEVOX_ENABLED", True)
TRACEVOX_CAMPAIGN_ID = os.getenv("TRACEVOX_CAMPAIGN_ID", "").strip()
TRACEVOX_ENVIRONMENT = os.getenv("TRACEVOX_ENVIRONMENT", "development").strip()
TRACEVOX_GATEWAY_FOR_EVAL = _env_bool("TRACEVOX_GATEWAY_FOR_EVAL", True)

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "180"))


def require_settings() -> None:
    missing = []
    for name, value in {
        "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
        "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    }.items():
        if not value:
            missing.append(name)

    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def websocket_base_url() -> str:
    if PUBLIC_BASE_URL.startswith("https://"):
        return "wss://" + PUBLIC_BASE_URL[len("https://"):]
    if PUBLIC_BASE_URL.startswith("http://"):
        return "ws://" + PUBLIC_BASE_URL[len("http://"):]
    raise RuntimeError("PUBLIC_BASE_URL must start with http:// or https://")
