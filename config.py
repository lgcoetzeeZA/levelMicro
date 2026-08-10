"""
LevelMicro persistent configuration.

Stored as JSON at /config.json on the device's flash filesystem. Holds WiFi
credentials, the MQTT topic prefix, and tank geometry - everything the
setup portal (wifi_portal.py) can change.

Tank geometry model (sensor mounted above the tank, facing down):

    sensor
      |
      | sensor_from_overflow_cm   <- gap between sensor and the overflow line
      |
    ==+== overflow line (100% full - water above this just spills out)
      |
      | tank_overflow_cm          <- usable fill height, bottom to overflow
      |
    __|__ tank bottom (0%)

  tank_roof_cm = sensor_from_overflow_cm + tank_overflow_cm
               = distance the sensor reads when the tank is completely
                 empty (never actually needed directly, but it's the
                 natural "far end" of the measurable range)

No manual empty/full calibration override anymore - those existed
before and were a common source of mistakes (it's easy to enter them
backwards: a top-mounted sensor reads a SMALL distance at 100% full and
a LARGE distance at 0% empty, which is the opposite of what most people
expect). Everything is now derived purely from the three geometry
numbers above, which are unambiguous.
"""

import ujson as json

CONFIG_PATH = "/config.json"

DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "mqtt_prefix": "",
    "tank_diameter_cm": 100,
    "tank_overflow_cm": 100,        # usable fill height: tank bottom -> overflow line
    "sensor_from_overflow_cm": 5,   # gap between the sensor and the overflow line
}

# Old field names, from before the tank-overflow model. Mapped onto the
# new names on load so an existing device doesn't lose its saved geometry.
_LEGACY_KEY_MAP = {
    "tank_height_cm": "tank_overflow_cm",
    "sensor_offset_cm": "sensor_from_overflow_cm",
}
# These no longer exist - manual calibration override is gone, always derive.
_REMOVED_KEYS = ("empty_dist_cm", "full_dist_cm")


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)

        for old_key, new_key in _LEGACY_KEY_MAP.items():
            if old_key in saved and new_key not in saved:
                saved[new_key] = saved.pop(old_key)
        for removed_key in _REMOVED_KEYS:
            saved.pop(removed_key, None)

        cfg.update(saved)
    except (OSError, ValueError):
        pass  # no config yet, or it's corrupt - fall back to defaults
    return cfg


def save(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


def tank_roof_cm(cfg):
    """Sensor reading when the tank is completely empty (0%)."""
    return cfg["sensor_from_overflow_cm"] + cfg["tank_overflow_cm"]


def full_dist_cm(cfg):
    """Sensor reading when water is exactly at the overflow line (100%)."""
    return cfg["sensor_from_overflow_cm"]


def device_id():
    import ubinascii
    import machine
    return ubinascii.hexlify(machine.unique_id()).decode()


def mqtt_topics(cfg):
    prefix = (cfg.get("mqtt_prefix") or "").strip()
    if prefix.endswith("/"):
        prefix = prefix[:-1]
    if not prefix:
        prefix = "levelmicro/" + device_id()
    base = prefix + "/LevelMicro/"
    return {
        "data": base + "data",
        "status": base + "status",
        "cmd": base + "cmd",
        "config": base + "config",
        # Shared live-progress channel: OTA update steps AND setup-mode
        # guidance both publish here, so watching one topic tells you
        # what the device is doing during either operation.
        "progress": base + "progress",
    }
