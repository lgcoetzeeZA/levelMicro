"""
LevelMicro persistent configuration.

Stored as JSON at /config.json on the device's flash filesystem. Holds WiFi
credentials, the MQTT topic prefix, and tank calibration - everything the
setup portal (wifi_portal.py) can change.
"""

import ujson as json

CONFIG_PATH = "/config.json"

DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "mqtt_prefix": "",
    "tank_height_cm": 100,
    "sensor_offset_cm": 5,
    "tank_diameter_cm": 100,
    "empty_dist_cm": 0,   # manual override; 0 = derive from height + offset
    "full_dist_cm": 0,    # manual override; 0 = derive from offset
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
        cfg.update(saved)
    except (OSError, ValueError):
        pass  # no config yet, or it's corrupt - fall back to defaults
    return cfg


def save(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)


def effective_empty_dist_cm(cfg):
    return cfg["empty_dist_cm"] or (cfg["sensor_offset_cm"] + cfg["tank_height_cm"])


def effective_full_dist_cm(cfg):
    return cfg["full_dist_cm"] or cfg["sensor_offset_cm"]


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
        "ota": base + "ota",
    }
