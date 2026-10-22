"""Non-destructive desktop-only preferences. No guild settings are touched."""
import json
import os
from pathlib import Path

DEFAULTS = {"refresh_seconds": 1, "theme": "Dark", "startup_windows": "Main only"}
THEMES = ("Dark", "White")
REFRESH_CHOICES = (1, 2, 3, 5, 10)
STARTUP_CHOICES = ("Main only", "Open guild windows")
FILENAME = ".dashboard_preferences.json"


def validate(value):
    value = value if isinstance(value, dict) else {}
    refresh = value.get("refresh_seconds", DEFAULTS["refresh_seconds"])
    try:
        refresh = int(refresh)
    except (ValueError, TypeError):
        refresh = DEFAULTS["refresh_seconds"]
    return {
        "refresh_seconds": refresh if refresh in REFRESH_CHOICES else DEFAULTS["refresh_seconds"],
        "theme": value.get("theme") if value.get("theme") in THEMES else DEFAULTS["theme"],
        "startup_windows": value.get("startup_windows") if value.get("startup_windows") in STARTUP_CHOICES else DEFAULTS["startup_windows"],
    }


def load(root):
    try:
        return validate(json.loads((Path(root) / FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def save(root, value):
    path = Path(root) / FILENAME
    data = validate(value)
    # Do not create or rewrite guild data, reward records, or credentials.
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
            out.write("\n")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return data
