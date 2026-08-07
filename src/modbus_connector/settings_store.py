import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path.home() / ".modbus_connector" / "settings.json"


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load settings from %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to save settings to %s: %s", path, exc)
