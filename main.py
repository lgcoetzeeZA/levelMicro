"""
LevelMicro - minimal ESP32 tank-level sensor node (MicroPython).

Sibling of LevelUP for a bare test board: one ultrasonic sensor, WiFi,
MQTT, and two status LEDs. No OLED, no buttons, no DIP switch / sensor
selection.

Flow:
  - No saved WiFi credentials -> opens the setup portal (WiFi + tank
    settings + MQTT prefix, all in one form).
  - Normally connects WiFi + MQTT and publishes a level reading every
    PUBLISH_INTERVAL_S seconds.
  - Publishing "setup" to <prefix>/LevelMicro/cmd re-opens the same setup
    portal (WiFi fields pre-filled, so you can leave WiFi alone and just
    change tank settings, or update WiFi too).
  - Publishing "update" to <prefix>/LevelMicro/cmd checks GitHub for a
    newer firmware version and applies it if found (see ota_updater.py).

See leds.py for what the System (IO26) and WiFi (IO25) LEDs indicate.

Requires umqtt.simple and urequests (see README for install instructions).
"""

import time
import machine
import network
import ujson as json
import math
import sys

import config as cfgmod
import sensor
import wifi_portal
import leds
import ota_updater
from umqtt.simple import MQTTClient

# ---- MQTT broker connection -------------------------------------------
MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_USER = ""   # leave blank ("") if the broker needs no auth
MQTT_PASS = ""
# ------------------------------------------------------------------
# Note: broker.hivemq.com is HiveMQ's free public sandbox broker - no auth,
# no privacy. Anyone can subscribe to your topics. Fine for bench testing;
# don't use it for anything sensitive or for a real production deployment.

KEEPALIVE_S = 60
PUBLISH_INTERVAL_S = 5
WIFI_CONNECT_TIMEOUT_S = 20
WDT_TIMEOUT_MS = 20000

cfg = cfgmod.load()
topics = cfgmod.mqtt_topics(cfg)
mqtt = None
setup_requested = False
update_requested = False

# Hardware watchdog: if this firmware ever truly locks up (not just a slow
# network operation, but a genuine hang), the ESP32 force-reboots itself
# instead of sitting dead until someone notices. Once started it can't be
# turned off on this port, so every blocking loop below - main loop, the
# WiFi connect wait, and the setup portal (fed via a reference passed in) -
# must call wdt.feed() often enough to stay under WDT_TIMEOUT_MS.
wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)

leds.system_starting()
leds.wifi_idle()


def connect_wifi(timeout_s=WIFI_CONNECT_TIMEOUT_S):
    if not cfg["wifi_ssid"]:
        print("connect_wifi: no SSID saved yet")
        return False
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print("connect_wifi: connecting to '%s' (timeout %ss)..." % (cfg["wifi_ssid"], timeout_s))
        leds.wifi_connecting()
        try:
            sta.connect(cfg["wifi_ssid"], cfg["wifi_password"])
        except Exception as e:
            print("connect_wifi: sta.connect() raised:")
            sys.print_exception(e)
            leds.wifi_idle()
            return False
        start = time.ticks_ms()
        while not sta.isconnected():
            wdt.feed()
            leds.update()
            if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
                print("connect_wifi: timed out, status =", sta.status())
                leds.wifi_idle()
                return False
            time.sleep_ms(200)
    ok = sta.isconnected()
    if ok:
        print("connect_wifi: connected, ip =", sta.ifconfig()[0])
        leds.wifi_connected()
    return ok


def enter_setup():
    """Blocking. Always reboots the device when it returns (or crashes)."""
    global cfg
    print("Entering setup portal...")
    leds.system_setup_mode()
    leds.wifi_ap_mode()
    try:
        new_cfg = wifi_portal.run_setup_portal(cfg, wdt=wdt)
        if new_cfg:
            cfg = new_cfg
            cfgmod.save(cfg)
    except Exception as e:
        print("Setup portal crashed - see traceback below, rebooting anyway:")
        sys.print_exception(e)
    machine.reset()


def publish_config():
    payload = json.dumps({
        "firmware": "LevelMicro",
        "version": ota_updater.get_current_version(),
        "prefix": cfg["mqtt_prefix"],
        "tank_diameter_cm": cfg["tank_diameter_cm"],
        "tank_overflow_cm": cfg["tank_overflow_cm"],
        "sensor_from_overflow_cm": cfg["sensor_from_overflow_cm"],
        "tank_roof_cm": cfgmod.tank_roof_cm(cfg),
    })
    try:
        mqtt.publish(topics["config"], payload, retain=True)
    except Exception as e:
        print("publish_config failed:", e)


def _rssi_to_pct(rssi):
    """Rough dBm -> 0-100% signal quality, same convention Windows/most
    router UIs use: -100dBm or worse = 0%, -50dBm or better = 100%,
    linear in between."""
    if rssi <= -100:
        return 0
    if rssi >= -50:
        return 100
    return int(2 * (rssi + 100))


def _network_info():
    sta = network.WLAN(network.STA_IF)
    try:
        ip = sta.ifconfig()[0]
    except Exception:
        ip = ""
    try:
        rssi = sta.status("rssi")
    except Exception:
        rssi = 0
    return ip, rssi


def publish_data():
    ip, rssi = _network_info()
    wifi_signal_pct = _rssi_to_pct(rssi)
    network_fields = {
        "ip": ip,
        "ssid": cfg["wifi_ssid"],
        "rssi": rssi,
        "wifi_signal_pct": wifi_signal_pct,
        "version": ota_updater.get_current_version(),
    }

    dist_mm = sensor.read_distance_mm()
    if dist_mm < 0:
        payload = json.dumps(dict({"error": "sensor_timeout"}, **network_fields))
    else:
        dist_cm = round(dist_mm / 10.0, 1)
        roof_cm = cfgmod.tank_roof_cm(cfg)
        height_cm = cfg["tank_overflow_cm"]

        water_cm = roof_cm - dist_cm
        water_cm = max(0.0, min(water_cm, float(height_cm)))
        level_pct = round(100.0 * water_cm / height_cm, 1) if height_cm > 0 else 0.0

        radius_m = (cfg["tank_diameter_cm"] / 2.0) / 100.0
        volume_l = round(math.pi * radius_m * radius_m * (water_cm / 100.0) * 1000.0, 1)
        # Total reservoir capacity (at 100% full) - distinct from volume_l,
        # which is the current volume at the current water level.
        tank_volume_l = round(math.pi * radius_m * radius_m * (height_cm / 100.0) * 1000.0, 1)

        payload = json.dumps(dict({
            "distance_cm": dist_cm,
            "water_cm": round(water_cm, 1),
            "level_pct": level_pct,
            "volume_l": volume_l,
            "tank_volume_l": tank_volume_l,
        }, **network_fields))
    print("publishing", topics["data"], "->", payload)
    try:
        mqtt.publish(topics["data"], payload)
    except Exception as e:
        print("publish_data failed:")
        sys.print_exception(e)


def mqtt_callback(topic, msg):
    global setup_requested, update_requested
    topic = topic.decode() if isinstance(topic, bytes) else topic
    msg = msg.decode() if isinstance(msg, bytes) else msg
    if topic != topics["cmd"]:
        return

    m = msg.strip().lower()
    if m == "setup":
        setup_requested = True
    elif m in ("update", "ota"):
        update_requested = True
    elif m in ("restart", "reboot"):
        machine.reset()
    elif m == "status":
        publish_config()
        publish_data()


def _check_broker_reachable(host, port, timeout_s=8):
    """Bounded reachability probe. umqtt.simple's own connect() doesn't set
    a socket timeout, so without this check a blocked/unreachable broker
    (firewalled port, bad DNS, no internet) hangs main.py indefinitely
    instead of failing with a clear error."""
    import usocket as socket
    print("mqtt: checking reachability of %s:%s (timeout %ss)..." % (host, port, timeout_s))
    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout_s)
        s.connect(addr)
        print("mqtt: broker reachable")
        return True
    except Exception as e:
        print("mqtt: broker NOT reachable:")
        sys.print_exception(e)
        return False
    finally:
        if s is not None:
            s.close()


def mqtt_connect():
    global mqtt

    if not _check_broker_reachable(MQTT_HOST, MQTT_PORT):
        raise OSError("MQTT broker unreachable: %s:%s" % (MQTT_HOST, MQTT_PORT))

    client_id = "LevelMicro-" + cfgmod.device_id()
    print("mqtt: connecting as", client_id, "...")
    mqtt = MQTTClient(
        client_id, MQTT_HOST, port=MQTT_PORT,
        user=(MQTT_USER or None), password=(MQTT_PASS or None),
        keepalive=KEEPALIVE_S,
    )
    mqtt.set_last_will(topics["status"], "offline", retain=True, qos=0)
    mqtt.set_callback(mqtt_callback)
    mqtt.connect()
    print("mqtt: connected, subscribing to", topics["cmd"])
    mqtt.subscribe(topics["cmd"])
    mqtt.publish(topics["status"], "online", retain=True)
    publish_config()
    _publish_progress("LevelMicro is online (v{}) and publishing data.".format(
        ota_updater.get_current_version()
    ))
    print("mqtt: ready")


class _WdtWithLeds:
    """Wraps the real WDT so ota_updater's existing wdt.feed() calls also
    animate the status LEDs during a download, without needing to touch
    the vendored ota_updater.py itself."""
    def __init__(self, real_wdt):
        self._wdt = real_wdt

    def feed(self):
        self._wdt.feed()
        leds.update()


def _publish_progress(msg):
    """Shared live-progress channel for both OTA updates and setup mode.
    Best-effort - a flaky MQTT link publishing this shouldn't be allowed
    to abort an otherwise-good update (ota_updater catches OSError as a
    download failure, so an uncaught publish error here would do that)."""
    print("progress:", msg)
    try:
        mqtt.publish(topics["progress"], msg)
    except Exception:
        pass


def run_update_check():
    """Blocking. Reboots the device if an update was applied; otherwise
    returns normally so the main loop continues."""
    print("Checking for firmware update...")
    leds.system_updating()
    applied = ota_updater.check_for_update(
        wdt=_WdtWithLeds(wdt),
        on_progress=_publish_progress,
    )
    if not applied:
        print("No update applied, resuming normal operation.")
    # If applied, check_for_update() already called machine.reset() and
    # this line is never reached.


def main():
    global setup_requested, update_requested

    print("LevelMicro starting, saved SSID:", cfg["wifi_ssid"] or "(none)")

    if not connect_wifi():
        enter_setup()  # does not return - reboots

    mqtt_ok = False
    try:
        mqtt_connect()
        mqtt_ok = True
    except Exception as e:
        print("MQTT connect failed:")
        sys.print_exception(e)
    leds.system_nominal() if mqtt_ok else leds.system_degraded()

    print("Entering main loop (mqtt_ok =", mqtt_ok, ")")
    last_publish = time.ticks_ms()
    last_ping = time.ticks_ms()

    while True:
        wdt.feed()
        leds.update()

        sta = network.WLAN(network.STA_IF)
        if not sta.isconnected():
            mqtt_ok = False
            leds.wifi_idle()
            if not connect_wifi(timeout_s=10):
                enter_setup()  # does not return - reboots

        if not mqtt_ok:
            try:
                mqtt_connect()
                mqtt_ok = True
            except Exception as e:
                print("MQTT reconnect failed:")
                sys.print_exception(e)
                time.sleep_ms(3000)

        if mqtt_ok:
            try:
                mqtt.check_msg()
            except Exception as e:
                print("MQTT check_msg failed:", e)
                mqtt_ok = False

            if mqtt_ok and time.ticks_diff(time.ticks_ms(), last_ping) > KEEPALIVE_S * 500:
                try:
                    mqtt.ping()
                except Exception:
                    mqtt_ok = False
                last_ping = time.ticks_ms()

        leds.system_nominal() if mqtt_ok else leds.system_degraded()

        if setup_requested:
            setup_requested = False
            # This is the one moment we're both connected to MQTT AND know
            # setup is about to happen - the portal itself runs with MQTT
            # disconnected, so this message is the only live heads-up the
            # user gets before the device goes into AP mode.
            ap_ssid = wifi_portal.AP_SSID_PREFIX + cfgmod.device_id()[-8:]
            _publish_progress(
                "Entering setup mode. Connect to WiFi '{}' (password: {}), "
                "then browse to http://192.168.4.1 to change WiFi/tank "
                "settings. Device is offline until you finish or it times "
                "out in 5 minutes.".format(ap_ssid, wifi_portal.AP_PASSWORD)
            )
            try:
                mqtt.publish(topics["status"], "setup_mode", retain=True)
                mqtt.disconnect()
            except Exception:
                pass
            enter_setup()  # does not return - reboots

        if update_requested:
            update_requested = False
            run_update_check()  # reboots if an update was applied, else returns
            leds.system_nominal() if mqtt_ok else leds.system_degraded()

        if mqtt_ok and time.ticks_diff(time.ticks_ms(), last_publish) > PUBLISH_INTERVAL_S * 1000:
            last_publish = time.ticks_ms()
            publish_data()

        time.sleep_ms(100)


# Small window to Ctrl-C out of the auto-run during development, before the
# main loop (or the setup portal, which can block for minutes) takes over
# the REPL.
time.sleep(2)
try:
    main()
except KeyboardInterrupt:
    print("LevelMicro stopped.")
