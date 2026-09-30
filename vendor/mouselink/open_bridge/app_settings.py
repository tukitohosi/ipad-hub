"""Settings migration independent of Qt and hardware."""


def migrate_settings(value):
    settings = value.copy() if isinstance(value, dict) else {}
    old = settings.get("mode", "free")
    settings["hotkey_return_enabled"] = (
        True if old == "mixed" else False if old == "edge"
        else settings.get("hotkey_return_enabled") is True
    )
    settings["mode"] = "locked" if old == "locked" else "free"
    settings["ipad_side"] = "left" if settings.get("ipad_side") == "left" else "right"
    try:
        settings["speed"] = max(25, min(150, int(settings.get("speed", 50))))
    except (ValueError, TypeError, OverflowError):
        settings["speed"] = 50
    if not isinstance(settings.get("calibration"), dict):
        settings["calibration"] = {}
    return settings


def bridge_mode(mode, hotkey_enabled):
    return "locked" if mode == "locked" else "mixed" if hotkey_enabled else "edge"
