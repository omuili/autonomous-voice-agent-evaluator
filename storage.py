import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ARTIFACTS_DIR


def safe_part(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)


def call_dir(scenario_id: str, call_sid: str) -> Path:
    path = ARTIFACTS_DIR / safe_part(scenario_id) / safe_part(call_sid)
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_event(
    scenario_id: str,
    call_sid: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    path = call_dir(scenario_id, call_sid) / "events.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
