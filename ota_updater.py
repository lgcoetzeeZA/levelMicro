import ujson
import uos as os
import uhashlib
import ubinascii
import urequests
import machine
import time
import gc

VERSION_FILE = "firmware_version.txt"

# Points at github.com/lgcoetzeeZA/levelMicro's manifest.json. Assumes the
# repo's default branch is "main" - if yours is "master" (or something
# else), change the branch segment below to match.
MANIFEST_URL = "https://raw.githubusercontent.com/lgcoetzeeZA/levelMicro/main/manifest.json"


def get_current_version():
    try:
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def _set_current_version(version):
    with open(VERSION_FILE, "w") as f:
        f.write(version)


def _sha256_hex(data):
    h = uhashlib.sha256()
    h.update(data)
    return ubinascii.hexlify(h.digest()).decode()


def _download(url):
    response = urequests.get(url)
    try:
        data = response.content
    finally:
        response.close()
    return data


def check_for_update(display=None, wdt=None, on_progress=None):
    """Checks the manifest for a version different from the one currently
    installed. If found, downloads every listed file to a staging filename
    and verifies its sha256 hash. Only if EVERY file downloads and verifies
    cleanly does it swap them all into place and reboot - if anything fails
    at any point, the currently running files are left completely untouched.

    wdt: optional active WDT instance, fed between each file so a batch of
    many small downloads can't cumulatively exceed the watchdog timeout
    even when no single request is actually hung.

    on_progress: optional callback(message) - called with a short plain
    text status at each stage, e.g. for publishing progress over MQTT.

    Returns True if an update was applied (device reboots as part of this),
    False if already up to date or the check/update failed."""

    def _progress(msg):
        print(msg)
        if on_progress:
            on_progress(msg)

    gc.collect()
    current_version = get_current_version()
    print("Current firmware version:", current_version)
    _progress("Checking for update (current: v{})...".format(current_version))

    if wdt:
        wdt.feed()

    try:
        manifest_resp = urequests.get(MANIFEST_URL)
        manifest = manifest_resp.json()
        manifest_resp.close()
    except (OSError, ValueError) as e:
        print("OTA check failed - could not fetch manifest:", e)
        _progress("Update check failed - could not reach update server")
        return False

    remote_version = manifest.get("version", "")
    files = manifest.get("files", {})

    if not remote_version or not files:
        print("OTA manifest is missing version or files.")
        _progress("Update check failed - manifest was invalid")
        return False

    if remote_version == current_version:
        print("Already up to date.")
        _progress("Already up to date (v{})".format(current_version))
        return False

    print("New version available: {} -> {}".format(current_version, remote_version))
    _progress("Update found: v{} (current: v{})".format(remote_version, current_version))
    if display:
        display.show("Update Found", "", "v" + remote_version, "Downloading...")

    staged = []
    try:
        for filename, info in files.items():
            if wdt:
                wdt.feed()

            url = info["url"]
            expected_hash = info.get("sha256")

            print("Downloading", filename)
            _progress("Downloading: {}".format(filename))
            if display:
                display.show("Update Found", "v" + remote_version, "Downloading:", filename)
            data = _download(url)

            if wdt:
                wdt.feed()

            if expected_hash:
                actual_hash = _sha256_hex(data)
                if actual_hash != expected_hash:
                    raise ValueError("Hash mismatch for {}".format(filename))

            staging_name = filename + ".new"
            with open(staging_name, "wb") as f:
                f.write(data)
            staged.append((staging_name, filename))
            del data
            gc.collect()

    except (OSError, ValueError, KeyError) as e:
        print("OTA update failed during download - leaving current files untouched:", e)
        _progress("Update failed ({}) - keeping current version v{}".format(e, current_version))
        for staging_name, _ in staged:
            try:
                os.remove(staging_name)
            except OSError:
                pass
        if display:
            display.show("Update Failed", "", "Keeping current", "version")
        return False

    if wdt:
        wdt.feed()

    print("All files downloaded and verified. Applying update...")
    _progress("All {} file(s) verified. Applying update...".format(len(staged)))
    if display:
        display.show("Applying Update", "", "Do not power off...")

    for staging_name, filename in staged:
        try:
            os.remove(filename)
        except OSError:
            pass
        os.rename(staging_name, filename)

    _set_current_version(remote_version)
    print("Update applied. Restarting...")
    _progress("Update complete - now v{}. Restarting...".format(remote_version))
    if display:
        display.show("Update Complete", "", "v" + remote_version, "Restarting...")

    time.sleep(2)
    machine.reset()
    return True
