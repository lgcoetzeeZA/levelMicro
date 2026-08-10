"""
LevelMicro status LEDs.

SYSTEM_PIN (IO26): overall firmware health/heartbeat.
WIFI_PIN   (IO25): WiFi/network connectivity state.

Both are driven by a small non-blocking blink-pattern state machine, same
"blink without delay" idea as Arduino. Call update() often - every main
loop tick, every WiFi-connect wait tick, every setup-portal loop tick -
and it decides on/off based on elapsed time. Call the semantic setters
(wifi_connected(), system_nominal(), etc.) whenever the firmware's state
changes; calling the same one repeatedly is harmless (no-op if unchanged).

If your LEDs are wired active-low (anode to 3V3, cathode + resistor to the
GPIO) instead of the usual active-high (GPIO -> resistor -> LED -> GND),
flip ACTIVE_HIGH to False below - everything else stays the same.
"""

from machine import Pin
import time

SYSTEM_PIN = 26
WIFI_PIN = 25
ACTIVE_HIGH = True

# (on_ms, off_ms) - off_ms=0 means solid on, on_ms=0 means solid off
PATTERN_OFF = (0, 1)
PATTERN_ON = (1, 0)
PATTERN_SLOW_BLINK = (500, 500)     # e.g. connecting / booting
PATTERN_FAST_BLINK = (120, 120)     # e.g. setup / AP mode - needs attention
PATTERN_WARN_BLINK = (300, 300)     # e.g. degraded - WiFi up but MQTT down
PATTERN_HEARTBEAT = (60, 1940)      # brief blip every ~2s - all nominal
PATTERN_OTA_BLINK = (80, 80)        # firmware update in progress - don't power off


class _Blinker:
    def __init__(self, pin_no):
        self.pin = Pin(pin_no, Pin.OUT)
        self.pattern = PATTERN_OFF
        self.on = False
        self.phase_start = time.ticks_ms()
        self._write(False)

    def _write(self, on):
        self.on = on
        if ACTIVE_HIGH:
            self.pin.value(1 if on else 0)
        else:
            self.pin.value(0 if on else 1)

    def set_pattern(self, pattern):
        if pattern == self.pattern:
            return
        self.pattern = pattern
        self.phase_start = time.ticks_ms()
        self._write(False)

    def update(self, now):
        on_ms, off_ms = self.pattern
        if on_ms == 0:
            if self.on:
                self._write(False)
            return
        if off_ms == 0:
            if not self.on:
                self._write(True)
            return
        period = on_ms if self.on else off_ms
        if time.ticks_diff(now, self.phase_start) >= period:
            self._write(not self.on)
            self.phase_start = now


_sys_led = _Blinker(SYSTEM_PIN)
_wifi_led = _Blinker(WIFI_PIN)


def update():
    now = time.ticks_ms()
    _sys_led.update(now)
    _wifi_led.update(now)


# ---- WiFi LED (IO25) - semantic states --------------------------------
def wifi_idle():
    """Not connected, not trying (e.g. no SSID saved yet)."""
    _wifi_led.set_pattern(PATTERN_OFF)


def wifi_connecting():
    _wifi_led.set_pattern(PATTERN_SLOW_BLINK)


def wifi_ap_mode():
    """Broadcasting the LevelMicro-Setup-XXXX access point."""
    _wifi_led.set_pattern(PATTERN_FAST_BLINK)


def wifi_connected():
    _wifi_led.set_pattern(PATTERN_ON)


# ---- System LED (IO26) - semantic states -------------------------------
def system_starting():
    _sys_led.set_pattern(PATTERN_SLOW_BLINK)


def system_setup_mode():
    _sys_led.set_pattern(PATTERN_FAST_BLINK)


def system_nominal():
    """WiFi + MQTT both up, publishing normally."""
    _sys_led.set_pattern(PATTERN_HEARTBEAT)


def system_degraded():
    """WiFi up but MQTT is not (broker unreachable, reconnecting, etc.)."""
    _sys_led.set_pattern(PATTERN_WARN_BLINK)


def system_updating():
    """Firmware update in progress. Deliberately doesn't touch the WiFi LED
    - it stays solid since WiFi is still connected - so this is visually
    distinct from setup mode (where both LEDs fast-blink together)."""
    _sys_led.set_pattern(PATTERN_OTA_BLINK)
