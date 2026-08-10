# LevelMicro

Stripped-down sibling of LevelUP for a bare test board — MicroPython on
ESP32, one ultrasonic sensor, WiFi + MQTT, two status LEDs, no OLED /
buttons / DIP switch.

## Wiring

| HC-SR04 pin | ESP32 pin |
|---|---|
| VCC | 5V |
| TRIG | GPIO 18 |
| ECHO | GPIO 19 (use a voltage divider — ECHO is 5V, ESP32 GPIO is 3.3V) |
| GND | GND |

| LED | ESP32 pin |
|---|---|
| System LED (+, via resistor) | GPIO 26 |
| WiFi LED (+, via resistor) | GPIO 25 |
| Both LEDs (−) | GND |

Wiring assumes active-high LEDs (GPIO → resistor → LED → GND, so HIGH =
lit). If yours are wired the other way around, flip `ACTIVE_HIGH = False`
at the top of `leds.py`.

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point — WiFi/MQTT connection, publish loop, command handling |
| `config.py` | Loads/saves settings to `/config.json` on flash |
| `sensor.py` | HC-SR04 distance reading (median-of-5 filtered) |
| `wifi_portal.py` | Captive-portal setup page (WiFi + tank settings + MQTT prefix) |
| `leds.py` | System (IO26) and WiFi (IO25) status LED patterns |
| `ota_updater.py` | GitHub-based firmware updater (ported from LevelUP, see below) |
| `firmware_version.txt` | Currently-installed version string, updated by the OTA process |

Copy all six `.py` files plus `firmware_version.txt` onto the board's
root filesystem (no subfolders needed).

## Setup

1. Flash MicroPython for ESP32 (esptool / Thonny / your usual tool) if
   it isn't already on the board.
2. Install `umqtt.simple` and `urequests` (neither is bundled with generic
   MicroPython builds):
   ```
   mpremote mip install umqtt.simple
   mpremote mip install urequests
   ```
   or via Thonny's Tools → Manage Packages, or by copying
   `umqtt/simple.py` / `urequests/__init__.py` from
   [micropython-lib](https://github.com/micropython/micropython-lib) onto
   the device manually. `uhashlib`, `ubinascii`, `uos` and `gc` (used by
   the OTA updater) are already built into the ESP32 port.
3. Broker is already set in `main.py` to HiveMQ's free public broker:
   ```python
   MQTT_HOST = "broker.hivemq.com"
   MQTT_PORT = 1883
   MQTT_USER = ""   # blank - no auth
   MQTT_PASS = ""
   ```
   This is a public sandbox — no privacy, anyone can subscribe to your
   topics. Fine for bench testing; swap in a private broker before any
   real deployment.
4. Upload `main.py`, `config.py`, `sensor.py`, `wifi_portal.py`, `leds.py`,
   `ota_updater.py`, and `firmware_version.txt` to the board (Thonny's
   "Upload to /" or `mpremote cp * :`).
5. Reset the board.

## First boot / setup flow

With no saved WiFi credentials, the device opens an access point:

- **SSID:** `LevelMicro-Setup-XXXXXXXX` (XXXXXXXX = last 8 hex chars of
  the chip's unique ID)
- **Password:** `levelmicro123`

Connect to it — most phones/laptops auto-open the setup page; if not,
browse to `http://192.168.4.1`. It asks for:

1. WiFi SSID / password
2. **MQTT topic prefix** — e.g. `home/tank1`. Yours to choose per device;
   everything below hangs off it as `<prefix>/LevelMicro/...`.
3. **Tank Diameter (cm)** — used for the liters calculation (cylindrical)
4. **Tank Overflow (cm)** — height from the tank bottom (0%) up to the
   overflow line (100%). This is the usable fill height, not necessarily
   the tank's total physical height.
5. **Sensor from Overflow (cm)** — gap between the sensor and the
   overflow line (i.e. the distance the sensor reads when water is
   exactly at 100%).

There's no manual empty/full calibration override — everything's derived
from those three numbers. See "Tank geometry" below for exactly how.

Save, and the device reboots connected to WiFi and MQTT.

## Re-entering setup later

Publish `setup` to the command topic:

```
<prefix>/LevelMicro/cmd
```

This re-opens the exact same portal (WiFi field pre-filled with the
current SSID, tank fields pre-filled with current values) for up to 5
minutes. Leave the WiFi fields untouched to keep the existing network
and only change tank settings, or update them too if the network is
changing. If nothing is submitted within 5 minutes, it reboots and
resumes normal operation with the previous settings.

Right before it goes into AP mode, it publishes the AP name/password and
what to do next to `<prefix>/LevelMicro/progress` — see "MQTT topics"
below.

Other commands on the same topic:

| Payload | Effect |
|---|---|
| `setup` | Opens the WiFi + tank settings portal |
| `update` / `ota` | Checks GitHub for a newer firmware version, applies it if found |
| `status` | Immediately publishes current config + a fresh reading |
| `restart` / `reboot` | Reboots the device |

## MQTT topics

All topics are rooted at `<prefix>/LevelMicro/` (prefix is whatever you
set in the portal, e.g. `home/tank1/LevelMicro/...`).

| Topic | Direction | Payload |
|---|---|---|
| `.../data` | publish, every 5s | `{"distance_cm":32.0,"water_cm":168.0,"level_pct":85.7,"volume_l":4083.4,"tank_volume_l":4768.4,"ip":"192.168.10.188","ssid":"The_Hermitage","rssi":-58,"wifi_signal_pct":84,"version":"1.0.0"}` |
| `.../status` | publish, retained | `online` / `offline` (last will) / `setup_mode` |
| `.../config` | publish, retained, on connect + after changes | current tank + prefix settings as JSON |
| `.../progress` | publish, during setup mode or an `update` check | plain-text status messages, see below |
| `.../cmd` | subscribe | `setup`, `update`, `status`, `restart` |

**`.../progress` messages** - one shared channel narrates both setup mode
and firmware updates, so subscribing to it tells you what the device is
doing either way:

- Entering setup (right before going into AP mode): `"Entering setup
  mode. Connect to WiFi 'LevelMicro-Setup-XXXXXXXX' (password:
  levelmicro123), then browse to http://192.168.4.1 to change WiFi/tank
  settings. Device is offline until you finish or it times out in 5
  minutes."` — this is the one moment MQTT is still connected before the
  portal takes over, so it's the only live heads-up you get; the portal
  itself has no MQTT connection to report through.
- Reconnecting (after setup, after a reboot, or just a normal
  connect/reconnect): `"LevelMicro is online (v1.0.0) and publishing
  data."`
- During an `update` check: `"Checking for update (current: v1.0.0)..."`,
  then either `"Already up to date (v1.0.0)"` or `"Update found: v1.0.1
  (current: v1.0.0)"`, then `"Downloading: main.py"` (one message per
  file), then `"All 2 file(s) verified. Applying update..."`, then
  `"Update complete - now v1.0.1. Restarting..."` — or on failure,
  `"Update failed (<reason>) - keeping current version vX"`.

These are also always printed to the console, so serial and MQTT show
the same narration.

**`.../data` fields**

| Field | Meaning |
|---|---|
| `distance_cm` | Raw sensor reading (sensor to water surface) |
| `water_cm` | Water column height, clamped to `[0, tank_overflow_cm]` |
| `level_pct` | Reservoir fill percentage |
| `volume_l` | Current volume at the current water level |
| `tank_volume_l` | Total reservoir capacity (volume when 100% full) |
| `ip` | Device's current IP address on the WiFi network |
| `ssid` | WiFi network name it's connected to |
| `rssi` | WiFi signal strength in dBm |
| `wifi_signal_pct` | Same signal strength as a rough 0–100% (-100dBm=0%, -50dBm=100%) |
| `version` | Currently-installed firmware version (from `firmware_version.txt`) |

`ip`/`ssid`/`rssi`/`wifi_signal_pct`/`version` are included even when the
sensor reading itself times out (`{"error":"sensor_timeout", ...}`), so
you can still tell the device is alive, on the network, and which
firmware it's running.

## Tank geometry

Sensor mounted at the top, facing down at the water:

```
   sensor
     |
     | Sensor from Overflow (cm)   <- gap: sensor to the overflow line
     |
   ==+==  overflow line  (100% full - water above this spills out)
     |
     | Tank Overflow (cm)          <- usable fill height, bottom to overflow
     |
   __|__  tank bottom  (0% empty)
```

Only three numbers, set via the portal:

- **Tank Diameter (cm)** - for the liters calculation (assumes cylindrical)
- **Tank Overflow (cm)** - height from the tank bottom up to the overflow line
- **Sensor from Overflow (cm)** - gap between the sensor and the overflow line

Everything else is derived, no manual calibration step needed:

- `tank_roof_cm` = `sensor_from_overflow_cm + tank_overflow_cm` - the
  sensor's reading when the tank is completely empty. (Published in
  `.../config` for reference; the firmware never needs you to enter it.)
- `water_cm` = `tank_roof_cm - distance_cm`, clamped to `[0, tank_overflow_cm]`
- `level_pct` = `water_cm / tank_overflow_cm * 100`
- `volume_l` uses `water_cm`; `tank_volume_l` (total capacity) uses
  `tank_overflow_cm` - both via the cylinder formula with `tank_diameter_cm`

There's deliberately no manual empty/full override anymore. That existed
before and was an easy way to get level readings stuck at 0%: for a
top-mounted sensor, "full" is a *small* distance reading and "empty" is a
*large* one, which is the opposite of what most people expect when asked
to fill in "empty" and "full" distances - entering them the intuitive
way silently breaks the math. Three unambiguous geometry numbers instead.

The sensor itself still measures in mm internally (that's its native
resolution); it's just rounded to cm at the point data gets published.

## Publish interval

`PUBLISH_INTERVAL_S` in `main.py`, currently 5s — change and re-upload
for a different cadence. Each successful publish prints the JSON payload
to the console, so a quiet Shell for the first few seconds after boot is
normal, not a hang.

Going faster than a couple seconds isn't very useful: each reading is
already a median of 5 sensor samples (~150ms), so there's a floor before
you'd just be publishing sensor noise. Going much below ~2-3s also means
more MQTT traffic for not much practical benefit on a water tank, which
doesn't change level quickly.

## Watchdog

`main.py` starts a hardware watchdog (`machine.WDT`, `WDT_TIMEOUT_MS` =
20s) right at boot, same idea as LevelUP: if the firmware ever genuinely
locks up, the ESP32 force-reboots itself instead of sitting dead until
someone notices. Once started it can't be turned off on the ESP32 port,
so every blocking loop feeds it - the main loop, the WiFi-connect wait,
and the setup portal (fed via a reference passed into `wifi_portal.py`).
If you add new blocking code (like the OTA updater below), feed the
watchdog inside it too, or it'll reboot mid-operation.

## Firmware updates (OTA)

`ota_updater.py` is ported directly from LevelUP, unchanged, so it behaves
identically: manifest-driven, sha256-verified, all-or-nothing.

**Trigger:** publish `update` (or `ota`) to `<prefix>/LevelMicro/cmd`.
There's no automatic check on boot — it only runs when you ask.

**What it does:**

1. Fetches `MANIFEST_URL` (a `manifest.json`) from GitHub.
2. Compares its `"version"` field against the on-device
   `firmware_version.txt`. If they match, it stops here — no download.
3. If different, downloads every file listed in the manifest to a
   `<filename>.new` staging file and checks its sha256 against the
   manifest.
4. Only if **every** file downloads and verifies cleanly does it delete
   the old files, rename the staged ones into place, update
   `firmware_version.txt`, and reboot. If anything fails partway through
   (bad hash, network drop, missing file), all staged files are deleted
   and the currently-running firmware is left completely untouched — no
   partial updates, no bricking.

Progress is printed to the console at every stage and also published
(best-effort) to `<prefix>/LevelMicro/progress`, so you can watch it
either over serial or MQTT. The System LED fast-blinks during the process
(`leds.system_updating()`) while the WiFi LED stays solid, since you're
still connected — that combination is what tells you "updating" apart
from "in setup mode" (where both LEDs blink).

**Repo:** `MANIFEST_URL` in `ota_updater.py` now points at your real repo:

```python
MANIFEST_URL = "https://raw.githubusercontent.com/lgcoetzeeZA/levelMicro/main/manifest.json"
```

It's currently empty on GitHub, so there's nothing to fetch yet. Push the
firmware files to it and add a `manifest.json` at the root, e.g.:

```json
{
  "version": "1.0.1",
  "files": {
    "main.py": {
      "url": "https://raw.githubusercontent.com/lgcoetzeeZA/levelMicro/main/main.py",
      "sha256": "<sha256 hex of that exact file>"
    }
  }
}
```

List only the files you actually changed for that release — anything not
in `manifest.json` is left alone. To compute a file's sha256 for the
manifest:

```
python3 -c "import hashlib; print(hashlib.sha256(open('main.py','rb').read()).hexdigest())"
```

Bump `"version"` in the manifest for every release; that's the only
thing that decides whether a device thinks an update exists.

Note: the URL assumes your default branch will be `main` — GitHub's
current default for new repos, but worth double-checking once you push
(if it ends up `master` or something else, update the branch segment in
both `MANIFEST_URL` and every file `url` in `manifest.json`).

## Status LEDs

Two LEDs, driven by `leds.py`, non-blocking (blink patterns run alongside
everything else, no `sleep()` calls involved):

**WiFi LED (GPIO 25)**

| State | Pattern |
|---|---|
| No SSID saved / not connected | Off |
| Connecting to WiFi | Slow blink (500ms) |
| Setup portal active (broadcasting the AP) | Fast blink (120ms) |
| Connected | Solid on |

**System LED (GPIO 26)**

| State | Pattern |
|---|---|
| Booting, before WiFi/MQTT state is known | Slow blink (500ms) |
| Setup portal active | Fast blink (120ms) |
| WiFi + MQTT both up, publishing normally | Brief heartbeat blip every ~2s |
| WiFi up but MQTT down (broker unreachable, reconnecting) | Blink (300ms) |
| Firmware update in progress | Very fast blink (80ms) — don't power off |

During setup mode both LEDs fast-blink together, which is deliberately
easy to recognize: "device wants attention, connect to the AP." Once
everything is healthy the System LED drops to an occasional heartbeat
blip rather than staying lit, so a glance tells you it's alive without
being a constant light in the room.

To change what a state does, edit the pattern constants or the semantic
functions at the bottom of `leds.py` (e.g. `system_nominal()`) — nothing
else needs to change since `main.py`/`wifi_portal.py` only call those
semantic names, not raw pin values.

## One thing worth bench-testing

The captive portal (WiFi AP + tiny HTTP server + DNS redirect in
`wifi_portal.py`) is hand-written against the MicroPython `socket`/`network`
APIs rather than a pre-built library — it's the newest piece here and hasn't
run on real hardware yet. Worth confirming on your test board that the
setup page loads and the form saves correctly before relying on it; if the
DNS auto-redirect doesn't trigger on your phone/laptop, browsing directly
to `192.168.4.1` is the fallback and always works.

## Debugging a hang or crash

Both `wifi_portal.py` and `enter_setup()` in `main.py` now print a
checkpoint at every major step (AP up, sockets bound, each connection
accepted, each request's method/path, parsed form fields, when the
confirmation page is sent) and print a full traceback via
`sys.print_exception()` if anything throws, instead of failing silently.

Keep Thonny's Shell open while you connect to the setup AP and submit the
form. If it hangs or the board resets, the last line printed before that
happens tells you exactly where — copy that output back so we can pin
down the actual cause rather than guessing.
