"""
LevelMicro ultrasonic distance sensor.

One fixed sensor type: HC-SR04-style ultrasonic (TRIG = GPIO18, ECHO = GPIO19).
No sensor-select logic - this board has no DIP switch, so there's nothing to
choose between.
"""

from machine import Pin, time_pulse_us
import time

TRIG_PIN = 18
ECHO_PIN = 19
ECHO_TIMEOUT_US = 30000  # ~5 m round trip max
SAMPLES = 5               # median filter

_trig = Pin(TRIG_PIN, Pin.OUT)
_echo = Pin(ECHO_PIN, Pin.IN)
_trig.value(0)


def _read_once_mm():
    _trig.value(0)
    time.sleep_us(2)
    _trig.value(1)
    time.sleep_us(10)
    _trig.value(0)

    duration = time_pulse_us(_echo, 1, ECHO_TIMEOUT_US)
    if duration < 0:
        return -1  # timeout waiting for echo start/end

    # speed of sound ~343 m/s -> 0.343 mm/us, round trip so /2
    return int(duration * 0.343 / 2)


def read_distance_mm():
    """Median-of-N filtered reading in mm, or -1 if every sample failed."""
    samples = []
    for _ in range(SAMPLES):
        d = _read_once_mm()
        if d > 0:
            samples.append(d)
        time.sleep_ms(30)
    if not samples:
        return -1
    samples.sort()
    return samples[len(samples) // 2]
