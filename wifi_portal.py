"""
LevelMicro setup portal.

Opens a WiFi access point with a captive-style setup page covering BOTH
WiFi credentials and tank settings (height, sensor offset, diameter,
manual calibration) plus the MQTT topic prefix - one form, same as the
LevelUP-style flow. Used on first boot (no saved WiFi) and whenever the
"setup" MQTT command is received.

A minimal DNS responder redirects all lookups to the AP's own IP so most
phones/laptops auto-open the setup page; if a device doesn't honor that,
browsing to 192.168.4.1 manually always works.
"""

import network
import socket
import time
import machine
import sys

import leds

AP_SSID_PREFIX = "LevelMicro-Setup-"
AP_PASSWORD = "levelmicro123"
PORTAL_TIMEOUT_S = 300

_FORM_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LevelMicro Setup</title>
<style>
body{{font-family:sans-serif;margin:20px;max-width:420px}}
label{{display:block;margin-top:12px;font-weight:bold}}
input{{width:100%;padding:8px;box-sizing:border-box;margin-top:4px}}
button{{margin-top:20px;padding:10px 20px}}
</style></head><body>
<h2>LevelMicro Setup</h2>
<form method="POST" action="/save">
<label>WiFi SSID</label><input name="ssid" value="{ssid}">
<label>WiFi Password (leave blank to keep current)</label><input name="password" type="password" value="">
<label>MQTT topic prefix (e.g. home/tank1)</label><input name="prefix" value="{prefix}">
<label>Tank height (cm)</label><input name="height" value="{height}">
<label>Sensor mounting offset (cm)</label><input name="offset" value="{offset}">
<label>Tank diameter (cm)</label><input name="diameter" value="{diameter}">
<label>Manual EMPTY calibration distance (cm, 0=auto)</label><input name="empty_dist" value="{empty_dist}">
<label>Manual FULL calibration distance (cm, 0=auto)</label><input name="full_dist" value="{full_dist}">
<button type="submit">Save &amp; Reboot</button>
</form></body></html>"""

_OK_PAGE = """<!DOCTYPE html><html><body>
<h3>Saved. LevelMicro is rebooting and will connect to your network.</h3>
</body></html>"""


def _device_id():
    import ubinascii
    return ubinascii.hexlify(machine.unique_id()).decode()


def _url_decode(v):
    v = v.replace("+", " ")
    out = ""
    i = 0
    while i < len(v):
        if v[i] == "%" and i + 2 < len(v):
            try:
                out += chr(int(v[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += v[i]
        i += 1
    return out


def _parse_form(body):
    fields = {}
    for pair in body.split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        fields[_url_decode(k)] = _url_decode(v)
    return fields


def _recv_http_request(cl, timeout_s=5):
    """Read a full HTTP request (headers + body, honoring Content-Length)
    instead of assuming one recv() call gets everything - a single recv can
    return just the headers if the body arrives in a later TCP segment."""
    cl.settimeout(timeout_s)
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    data = b""

    while b"\r\n\r\n" not in data:
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        chunk = cl.recv(1024)
        if not chunk:
            break
        data += chunk
        if len(data) > 8192:
            break

    if b"\r\n\r\n" in data:
        header_bytes, body_bytes = data.split(b"\r\n\r\n", 1)
    else:
        header_bytes, body_bytes = data, b""

    content_length = 0
    for line in header_bytes.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                content_length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                content_length = 0
            break

    while len(body_bytes) < content_length:
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        chunk = cl.recv(1024)
        if not chunk:
            break
        body_bytes += chunk

    return header_bytes.decode(), body_bytes.decode()


def _dns_reply(data, ip):
    """Minimal DNS response pointing every query at `ip` (captive-portal redirect)."""
    packet = data[:2] + b"\x81\x80"
    packet += data[4:6] * 2          # QDCOUNT -> also used as ANCOUNT
    packet += b"\x00\x00\x00\x00"    # NSCOUNT, ARCOUNT
    packet += data[12:]              # echo the original question
    packet += b"\xc0\x0c"            # pointer to name in question
    packet += b"\x00\x01\x00\x01"    # TYPE A, CLASS IN
    packet += b"\x00\x00\x00\x3c"    # TTL 60s
    packet += b"\x00\x04"
    packet += bytes(int(x) for x in ip.split("."))
    return packet


def run_setup_portal(cfg, wdt=None):
    """Blocking. Returns an updated cfg dict if the form was submitted,
    or None if the portal timed out with nothing saved.

    `wdt` is the machine.WDT instance from main.py, if any - this loop can
    run for up to PORTAL_TIMEOUT_S (5 min), so it needs to feed it too."""
    print("=== LevelMicro setup portal starting ===")
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    time.sleep_ms(200)
    ssid = AP_SSID_PREFIX + _device_id()[-8:]

    try:
        ap.config(essid=ssid, password=AP_PASSWORD, authmode=network.AUTH_WPA2_PSK)
    except Exception as e:
        print("AP config with WPA2 password failed, falling back to open AP:")
        sys.print_exception(e)
        try:
            ap.config(essid=ssid)
        except Exception as e2:
            print("Open AP config also failed:")
            sys.print_exception(e2)

    ip = ap.ifconfig()[0]
    print("AP up:", ssid, "password:", AP_PASSWORD, "-> http://" + ip)

    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.setblocking(False)
    dns.bind(("0.0.0.0", 53))
    print("DNS redirect listening on :53")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    srv.setblocking(False)
    print("HTTP server listening on :80 (timeout", PORTAL_TIMEOUT_S, "s)")

    result_cfg = dict(cfg)
    saved = False
    start = time.ticks_ms()

    while not saved and time.ticks_diff(time.ticks_ms(), start) < PORTAL_TIMEOUT_S * 1000:
        if wdt is not None:
            wdt.feed()
        leds.update()

        try:
            data, addr = dns.recvfrom(512)
            dns.sendto(_dns_reply(data, ip), addr)
        except OSError:
            pass

        cl = None
        try:
            cl, remote = srv.accept()
            print("connection from", remote)
        except OSError:
            time.sleep_ms(50)
            continue

        try:
            headers, body = _recv_http_request(cl)
            first_line = headers.split("\r\n", 1)[0]
            print("request:", first_line, "| body bytes:", len(body))
            parts = first_line.split(" ")
            method = parts[0] if len(parts) > 0 else ""
            path = parts[1] if len(parts) > 1 else "/"

            if method == "POST" and path.startswith("/save"):
                fields = _parse_form(body)
                print("parsed fields:", fields)

                if fields.get("ssid"):
                    result_cfg["wifi_ssid"] = fields["ssid"]
                if fields.get("password"):
                    result_cfg["wifi_password"] = fields["password"]
                result_cfg["mqtt_prefix"] = fields.get("prefix", result_cfg["mqtt_prefix"])

                for cfg_key, form_key in (
                    ("tank_height_cm", "height"),
                    ("sensor_offset_cm", "offset"),
                    ("tank_diameter_cm", "diameter"),
                    ("empty_dist_cm", "empty_dist"),
                    ("full_dist_cm", "full_dist"),
                ):
                    raw = fields.get(form_key)
                    if raw is not None:
                        try:
                            result_cfg[cfg_key] = int(raw)
                        except ValueError:
                            print("could not parse", form_key, "=", raw, "as int, keeping previous value")

                print("saving config:", result_cfg)
                cl.send(("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + _OK_PAGE).encode())
                print("confirmation page sent")
                saved = True
            else:
                print("serving setup form for", path)
                page = _FORM_TEMPLATE.format(
                    ssid=result_cfg["wifi_ssid"],
                    prefix=result_cfg["mqtt_prefix"],
                    height=result_cfg["tank_height_cm"],
                    offset=result_cfg["sensor_offset_cm"],
                    diameter=result_cfg["tank_diameter_cm"],
                    empty_dist=result_cfg["empty_dist_cm"],
                    full_dist=result_cfg["full_dist_cm"],
                )
                cl.send(("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + page).encode())
        except Exception as e:
            print("portal request error:")
            sys.print_exception(e)
        finally:
            if cl is not None:
                cl.close()

    if not saved:
        print("setup portal timed out after", PORTAL_TIMEOUT_S, "s with nothing saved")

    srv.close()
    dns.close()
    ap.active(False)
    print("=== LevelMicro setup portal stopped (saved =", saved, ") ===")
    return result_cfg if saved else None
