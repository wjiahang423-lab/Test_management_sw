"""Read device battery & WiFi status from /sys and /proc (Linux)."""
import glob
import os


def battery_status():
    """Return dict(percent, status, ac) for the primary battery, or None."""
    bats = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    if not bats:
        return None
    try:
        with open(os.path.join(bats[0], "capacity")) as f:
            percent = int(f.read().strip())
    except Exception:
        return None
    status = ""
    try:
        with open(os.path.join(bats[0], "status")) as f:
            status = f.read().strip()
    except Exception:
        status = ""
    ac = False
    for prefix in ("ADP", "AC"):
        for path in glob.glob("/sys/class/power_supply/{}*".format(prefix)):
            try:
                with open(os.path.join(path, "online")) as f:
                    if f.read().strip() == "1":
                        ac = True
            except Exception:
                pass
    return {"percent": percent, "status": status, "ac": ac}


def wifi_status():
    """Return dict(iface, level, quality) of the strongest WiFi link, or None."""
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()[2:]
        best = None
        for line in lines:
            parts = line.split()
            if len(parts) < 5:
                continue
            iface = parts[0].rstrip(":")
            try:
                quality = float(parts[2])
                level = float(parts[3])
            except ValueError:
                continue
            if quality <= 0 or level <= -256 or level > 0:
                continue
            if best is None or level > best["level"]:
                best = {"iface": iface, "level": level, "quality": quality}
        return best
    except Exception:
        return None


def wired_connected():
    """True when any non-loopback interface reports operstate=up."""
    try:
        for dev in os.listdir("/sys/class/net"):
            if dev == "lo":
                continue
            try:
                with open("/sys/class/net/{}/operstate".format(dev)) as f:
                    if f.read().strip() == "up":
                        return True
            except Exception:
                pass
    except Exception:
        pass
    return False