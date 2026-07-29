"""Load/save the last-seen snapshot so restarts don't re-alert."""

import json
import logging
import os

STATE_FILE = os.path.join("data", "state.json")

logger = logging.getLogger(__name__)


def load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load state file, starting fresh")
        return {}


def save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp_path, STATE_FILE)
