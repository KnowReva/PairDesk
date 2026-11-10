"""PairDesk core: config, keys, USB, SSH link, folder sharing, pairing.

Everything between two computers travels inside one SSH connection:
  * RDP (full desktop, clipboard, sound, microphone)  -> local forward to peer:3389
  * USB/IP, peer -> me                                 -> local forward to peer:3240
  * USB/IP, me -> peer                                 -> remote forward peer:RUSB -> me:3240
  * folder, peer -> me (sshfs over this link)
  * folder, me -> peer (peer runs sshfs back through remote forward peer:RSSH -> me:22)
"""

import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time

APP = "pairdesk"
VERSION = "2.9.2"
HELPER = "/usr/libexec/pairdesk/root-helper"
SFTP_MARKER = "/usr/bin/pairdesk-sftp"
SLOT_BASE = 47300          # USB back-channel port on the remote side: SLOT_BASE + 2*slot
SYS_USB = "/sys/bus/usb/devices"
BUSID_RE = re.compile(r"^\d+-\d+(\.\d+)*$")
NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

DEFAULT_OPTS = {
    "desktop": True, "fullscreen": True, "sound": True, "microphone": True,
    "clipboard": True, "multimon": False, "share_mine": True, "open_theirs": True,
    "printers": False, "quality": "auto", "direct_desktop": False, "clean_mic": False,
    "sound_where": "here", "mic_device": "", "speaker_device": "",
}
QUALITY = {   # picture quality presets -> FreeRDP options
    "best": ["/network:lan"],
    "auto": ["/network:auto"],
    "fast": ["/network:broadband-low", "/bpp:16", "-wallpaper", "-themes"],
}
SECURITY_KEY_VENDORS = {"1050", "20a0", "096e"}   # Yubico, Nitrokey, Feitian


class DBError(Exception):
    pass


# ---------------------------------------------------------------- paths/config
def safe_name(s):
    s = NAME_RE.sub("-", s.strip())[:32].strip("-.")
    return s or "computer"


def hostname():
    """This computer's PairDesk name (config "name", else the host name)."""
    try:
        with open(os.path.join(cfg_dir(), "config.json")) as f:
            n = json.load(f).get("name")
        if n:
            return safe_name(n)
    except (OSError, ValueError):
        pass
    return safe_name(socket.gethostname())


def cfg_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, APP)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def run_dir():
    """The same folder for every PairDesk process of this user: the desktop session's and PairDesk's link
    (which starts without XDG_RUNTIME_DIR) must find each other's files here."""
    base = "/run/user/%d" % os.getuid()
    if not os.path.isdir(base):
        base = os.environ.get("XDG_RUNTIME_DIR") or ""
    if not base or not os.path.isdir(base):
        base = "/tmp"
    d = os.path.join(base, "%s-%d" % (APP, os.getuid()))
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def key_path(kind="client"):
    """client: my identity when I connect out.  rev: used only when a computer
    connected to me opens my visitor's shared folder back through the link."""
    return os.path.join(cfg_dir(), "id_ed25519" if kind == "client" else "id_rev_ed25519")


def known_hosts():
    return os.path.join(cfg_dir(), "known_hosts")


def mount_root():
    return os.path.expanduser("~/PairDesk")


def default_config():
    return {"mode": "client", "share_dir": "~/PairDesk Shared", "share_readonly": False,
            "peers": {}, "incoming": {}}


def load_config():
    p = os.path.join(cfg_dir(), "config.json")
    c = default_config()
    try:
        with open(p) as f:
            c.update(json.load(f))
    except FileNotFoundError:
        pass
    except ValueError:
        raise DBError("Settings file is damaged: %s" % p)
    return c


def save_config(c):
    p = os.path.join(cfg_dir(), "config.json")
    fd, tmp = tempfile.mkstemp(dir=cfg_dir())
    with os.fdopen(fd, "w") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, p)


def share_dir(c=None):
    c = c or load_config()
    d = os.path.expanduser(c.get("share_dir") or "~/PairDesk Shared")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_key(kind="client"):
    k = key_path(kind)
    if not os.path.exists(k):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", k,
                        "-C", "pairdesk-%s@%s" % (kind, hostname())], check=True)
    with open(k + ".pub") as f:
        return f.read().strip()


def host_pubkey():
    """This computer's SSH host key, or None when no SSH server is installed (fine for a pure client)."""
    for t in ("ed25519", "ecdsa", "rsa"):
        p = "/etc/ssh/ssh_host_%s_key.pub" % t
        if os.path.exists(p):
            with open(p) as f:
                return " ".join(f.read().split()[:2])
    return None


HOSTKEY_RE = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/=]{40,}$")

# ---------------------------------------------------------------- optional features
# PairDesk needs only Python, GTK and the SSH client. Everything else is installed
# when it is first needed, after asking. "packages" lists groups of alternatives.
FEATURES = {
    "viewer": {
        "title": "Remote desktop viewer (FreeRDP)", "side": "client",
        "why": "It shows the desktop of a remote computer in a window on the current computer.",
        "packages": [["freerdp3-x11", "freerdp-x11", "freerdp2-x11"]]},   # freerdp-x11: newer Debian
    "folders": {
        "title": "Folder sharing (sshfs)", "side": "both",
        "why": "It opens the shared folder of another computer as a normal folder on the current computer.",
        "packages": [["sshfs"]]},
    "accept": {
        "title": "SSH server program (openssh-server)", "side": "server",
        "why": "PairDesk's link service is built on it: remote computers connect to the current computer "
               "through PairDesk's own port with it. If PairDesk installs it, the system's separate SSH "
               "login service is switched off again, so only PairDesk's port is open.",
        "packages": [["openssh-server"]]},
    "share-out": {
        "title": "File-sharing helper (openssh-sftp-server)", "side": "both",
        "why": "It lets the remote computer open the shared folder of the current computer.",
        "packages": [["openssh-sftp-server"]]},
    "usb": {
        "title": "USB sharing (usbip)", "side": "both",
        "why": "It sends USB devices such as security keys, webcams and drives over the network.",
        "packages": [["usbip", "linux-tools-generic"], ["sudo"]]},
    "lan-scan": {
        "title": "Network discovery (avahi-utils)", "side": "client",
        "why": "It finds PairDesk computers on your network, so you don't have to type an IP address.",
        "packages": [["avahi-utils"]]},
    "keyring": {
        "title": "Password storage (libsecret)", "side": "client",
        "why": "It remembers desktop passwords safely in your keyring.",
        "packages": [["gir1.2-secret-1"]]},
    "camera-send": {
        "title": "Camera sending (ffmpeg)", "side": "client",
        "why": "It sends a camera of the current computer to the remote computer as compressed video, which "
               "is much smoother over the network than sending the camera as a USB device.",
        "packages": [["ffmpeg"]]},
    "camera-receive": {
        "title": "Virtual camera (v4l2loopback and ffmpeg)", "side": "server",
        "why": "It shows a camera sent from another computer as a normal camera, so video calls and camera "
               "apps on the current computer can use it.",
        "packages": [["ffmpeg"], ["v4l2loopback-dkms"]]},
    "xrdp": {
        "title": "Desktop server (xrdp)", "side": "server",
        "why": "It lets remote computers log in to the desktop of the current computer with its normal "
               "user name and password.",
        "packages": [["xrdp"], ["xorgxrdp"], ["pipewire-module-xrdp", "pulseaudio-module-xrdp"]]},
}
SIDES = {"client": "the computer you connect from (client side)",
         "server": "the computer others connect to (server side)",
         "both": "each computer that uses it (client and server side)"}


def feature_present(name):
    if name == "viewer":
        return any(shutil.which(b) for b in ("xfreerdp3", "sdl-freerdp3", "xfreerdp"))
    if name == "folders":
        return bool(shutil.which("sshfs"))
    if name == "accept":
        return os.path.exists("/usr/sbin/sshd")
    if name == "share-out":
        return any(os.path.exists(p) for p in SFTP_SERVERS)
    if name == "usb":
        return bool(shutil.which("usbip")) or bool(glob.glob("/usr/lib/linux-tools/*/usbip"))
    if name == "lan-scan":
        return bool(shutil.which("avahi-browse"))
    if name == "keyring":
        return any(os.path.exists(p) for p in ("/usr/lib/x86_64-linux-gnu/girepository-1.0/Secret-1.typelib",
                                               "/usr/lib/aarch64-linux-gnu/girepository-1.0/Secret-1.typelib",
                                               "/usr/lib/i386-linux-gnu/girepository-1.0/Secret-1.typelib",
                                               "/usr/lib/girepository-1.0/Secret-1.typelib"))
    if name == "xrdp":      # xrdp needs its display part (xorgxrdp) to show a desktop
        return os.path.exists("/usr/sbin/xrdp") and xorgxrdp_present()
    if name == "camera-send":
        return bool(shutil.which("ffmpeg"))
    if name == "camera-receive":
        return bool(shutil.which("ffmpeg")) and loopback_available()
    return False


def loopback_available():
    """The virtual camera driver (v4l2loopback) is loaded or can be loaded."""
    if os.path.isdir("/sys/module/v4l2loopback"):
        return True
    for m in ("/usr/sbin/modinfo", "/sbin/modinfo"):
        if os.path.exists(m):
            return subprocess.run([m, "-n", "v4l2loopback"], capture_output=True).returncode == 0
    return False


# ----------------------------------------------------------------- sound devices
def list_audio(kind):
    """Microphones ("sources") or speakers ("sinks") of this computer: [{name, title}], monitors and
    PairDesk's own echo canceller left out."""
    if not shutil.which("pactl"):
        return []
    out = []
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", kind], capture_output=True, text=True, timeout=10)
        for it in json.loads(r.stdout or "[]"):
            name = it.get("name", "")
            if name.endswith(".monitor") or name.startswith("pairdesk_") or it.get("monitor_of_sink") not in (None, "n/a"):
                continue
            out.append({"name": name, "title": it.get("description") or name})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        try:
            r = subprocess.run(["pactl", "list", "short", kind], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                f = line.split("\t")
                if len(f) > 1 and not f[1].endswith(".monitor") and not f[1].startswith("pairdesk_"):
                    out.append({"name": f[1], "title": f[1]})
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out


def audio_args(opts, clean=False):
    """FreeRDP options for where the remote computer's sound plays and which microphone is sent."""
    a = []
    if opts.get("sound") and opts.get("sound_where", "here") == "remote":
        a.append("/audio-mode:1")                      # play on the remote computer's own speakers
    elif opts.get("sound"):
        dev = "pairdesk_speaker" if clean else opts.get("speaker_device")
        a.append("/sound:sys:pulse" + (",dev:" + dev if dev else "") if dev else "/sound")
    else:
        a.append("/audio-mode:2")                      # no sound at all
    if opts.get("microphone"):
        dev = "pairdesk_mic" if clean else opts.get("mic_device")
        a.append("/microphone:sys:pulse,dev:" + dev if dev else "/microphone")
    return a


# ----------------------------------------------------------------- cameras
CAMERA_LABEL = "PairDesk camera"


def list_cameras(root="/sys/class/video4linux"):
    """Cameras on this computer: [{dev, name}] (one entry per camera; virtual cameras skipped)."""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "video*")), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        try:
            name = open(os.path.join(d, "name")).read().strip()
            index = open(os.path.join(d, "index")).read().strip() if os.path.exists(os.path.join(d, "index")) else "0"
        except OSError:
            continue
        if index != "0" or not os.path.exists(os.path.join(d, "device")) or name.startswith("PairDesk"):
            continue
        out.append({"dev": "/dev/" + os.path.basename(d), "name": name.split(":")[0].strip() or "Camera"})
    return out


def camera_capture_cmd(dev, size="640x480"):
    """ffmpeg reading the camera and writing MJPEG to stdout: the camera's own MJPEG when it has it
    (no work for this computer), otherwise compressed here."""
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-video_size", size]
    try:
        fmts = subprocess.run(["ffmpeg", "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", dev],
                              capture_output=True, text=True, timeout=10).stderr
    except (OSError, subprocess.TimeoutExpired):
        fmts = ""
    if re.search(r"Compressed:\s+mjpeg", fmts):
        return base + ["-input_format", "mjpeg", "-framerate", "30", "-i", dev, "-c:v", "copy", "-f", "mjpeg", "pipe:1"]
    return base + ["-framerate", "15", "-i", dev, "-c:v", "mjpeg", "-q:v", "6", "-f", "mjpeg", "pipe:1"]


def camera_play_cmd(device):
    """ffmpeg on the receiving computer: MJPEG from stdin into the virtual camera."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mjpeg", "-i", "pipe:0",
            "-vf", "format=yuv420p", "-f", "v4l2", device]


def xorgxrdp_present():
    return bool(glob.glob("/usr/lib/xorg/modules/drivers/xrdpdev_drv.so") or
                glob.glob("/usr/lib/*/xorg/modules/drivers/xrdpdev_drv.so"))


def process_running(name):
    for d in glob.glob("/proc/[0-9]*/comm"):
        try:
            if open(d).read().strip() == name:
                return True
        except OSError:
            pass
    return False


def local_sessions(user):
    """IDs of desktop sessions the user has open on this computer's own screen."""
    try:
        out = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    ids = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 3 and f[2] == user:
            props = subprocess.run(["loginctl", "show-session", f[0], "-p", "Type", "-p", "Remote", "-p", "State",
                                    "-p", "Seat", "-p", "Class"], capture_output=True, text=True).stdout
            kv = dict(l.split("=", 1) for l in props.splitlines() if "=" in l)
            # Only a session on a real seat is "on the screen". GNOME Remote Login's own sessions, earlier
            # remote ones still running, SSH logins and sessions being closed have no seat or don't count.
            if kv.get("Type") in ("x11", "wayland") and kv.get("Remote") == "no" and kv.get("Seat") \
                    and kv.get("Class", "user") == "user" and kv.get("State") in ("active", "online"):
                ids.append(f[0])
    return ids


def signed_in_locally(user):
    return bool(local_sessions(user))


X11_GNOME_SESSIONS = ("ubuntu.desktop", "ubuntu-xorg.desktop", "gnome.desktop", "gnome-xorg.desktop",
                      "gnome-classic.desktop", "gnome-classic-xorg.desktop")


def gnome_wayland_only():
    """GNOME 49+ (Ubuntu 25.10 and later) has no X11 session, so xrdp can't start it."""
    if not shutil.which("gnome-shell"):
        return False
    have = {os.path.basename(f) for f in glob.glob("/usr/share/xsessions/*.desktop")}
    return not (have & set(X11_GNOME_SESSIONS))


def rdp_server():
    """Which desktop server holds the desktop port: 'xrdp', 'gnome' (GNOME Remote Login) or None."""
    if not port_listening(3389):
        return None
    if process_running("xrdp"):
        return "xrdp"
    if process_running("gnome-remote-de"):      # gnome-remote-desktop-daemon (names are cut to 15 letters)
        return "gnome"
    return None


def desktop_conflict(user=None):
    """'xorgxrdp' (xrdp can't draw), 'wayland_only' (xrdp can't start this GNOME), 'signed_in' (the same user
    is on the local screen: neither xrdp nor GNOME Remote Login can open a second desktop) or None."""
    srv = rdp_server()
    if srv is None:
        return None
    if srv == "xrdp" and not xorgxrdp_present():
        return "xorgxrdp"
    if srv == "xrdp" and gnome_wayland_only() and not os.path.exists(os.path.expanduser("~/.xsession")):
        return "wayland_only"
    if signed_in_locally(user or getpass_user()):
        return "signed_in"
    return None


def logout_state_path(peer):
    """The sign-out notice and PairDesk's link talk through this file. In the home folder: it stays the same for
    both, and it survives the session ending."""
    d = os.path.join(os.path.expanduser("~"), ".cache", APP)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, "logout-%s.state" % safe_name(peer))


def logout_now(session_ids):
    """Sign the local desktop session(s) out: politely first, then for sure."""
    env = session_env()
    if shutil.which("gnome-session-quit") and env.get("DBUS_SESSION_BUS_ADDRESS"):
        r = subprocess.run(["gnome-session-quit", "--logout", "--no-prompt", "--force"], env=env,
                           capture_output=True, timeout=20)
        for _ in range(15 if r.returncode == 0 else 0):     # give a polite sign-out time to finish
            if not any(i in local_sessions(getpass_user()) for i in session_ids):
                return True
            time.sleep(1)
    for i in session_ids:
        subprocess.run(["loginctl", "terminate-session", i], capture_output=True)
    time.sleep(2)
    return not any(i in local_sessions(getpass_user()) for i in session_ids)


def desktop_warning(user=None):
    """Why the desktop server here would fail to show a desktop, in plain words (or None)."""
    srv = rdp_server()
    if srv is None:
        return None
    me = hostname()
    if srv == "gnome":
        if signed_in_locally(user or getpass_user()):
            return ("You're signed in on the screen of %s. GNOME Remote Login can't open a second desktop for the "
                    "same user. Sign out on %s first." % (me, me))
        return None
    if gnome_wayland_only() and not os.path.exists(os.path.expanduser("~/.xsession")):
        return ("xrdp can't start a desktop on %s: its GNOME runs only on Wayland (GNOME 49 and later, Ubuntu "
                "25.10 and later), and xrdp needs an X11 desktop. Switch %s to GNOME Remote Login: on %s, "
                "PairDesk > Current computer > Desktop sharing > Set up, or over SSH: pairdesk desktop gnome"
                % (me, me, me))
    if not xorgxrdp_present():
        return ("The desktop server on %s (xrdp) is missing its display part, xorgxrdp, so it can't show a "
                "desktop. On %s: PairDesk > Current computer > Desktop sharing > Set up, or run: "
                "sudo apt install xorgxrdp" % (me, me))
    if signed_in_locally(user or getpass_user()):
        return ("You're signed in on the screen of %s. Its desktop server (xrdp) can't open a second desktop "
                "for the same user. Sign out on %s first, or switch %s to GNOME Remote Login "
                "(Current computer > Desktop sharing > Set up)." % (me, me, me))
    return None


def feature_packages(name):
    return {p for g in FEATURES[name]["packages"] for p in g}


def added_by_pairdesk():
    """Packages PairDesk itself installed as add-ons (its record is readable by everyone)."""
    try:
        with open("/var/lib/pairdesk/requested.list") as f:
            return {l.strip() for l in f if l.strip()}
    except OSError:
        return set()


def feature_text(name):
    f = FEATURES[name]
    return ("%s\n\nNeeded on %s.\nIt is removed again when PairDesk is uninstalled."
            % (f["why"], SIDES[f["side"]]))


def install_feature(name, on_line=None):
    """Install an optional feature (asks for the admin password if setup was not done).
    on_line(text) receives live progress for a progress window."""
    return helper_stream("install-feature", name, on_line=on_line)


TERMINAL = False     # set by the command line when run in a terminal (also over SSH): sudo asks there


def _escalate():
    """How to ask for the admin password for an action the person started."""
    if TERMINAL and shutil.which("sudo"):
        return ["sudo"]          # sudo asks on the terminal itself, also over SSH
    return ["pkexec"] if shutil.which("pkexec") else None


def helper_stream(*args, on_line=None, stdin=None, interactive=True, admin=False):
    """Like helper(), but passes the helper's live output (apt progress, steps) to on_line.
    admin=True always asks for the admin password (pkexec), never the setup's sudo rule."""
    cmd = [HELPER] + [str(a) for a in args]
    if admin:
        if not shutil.which("pkexec"):
            raise DBError("pkexec isn't available here. Install the update in a terminal with: "
                          "pairdesk update --install")
        tries = [["pkexec"]]
    else:
        tries = [["sudo", "-n"]] + ([_escalate()] if interactive and _escalate() else [])
    for prefix in tries:
        p = subprocess.Popen(prefix + cmd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        if stdin:
            p.stdin.write(stdin)
            p.stdin.close()
        err = []
        for line in p.stderr:
            line = line.rstrip("\n")
            err.append(line)
            if on_line and not line.startswith(("sudo:", "sudo-rs:")):
                on_line(line)
        out = p.stdout.read().strip()
        rc = p.wait()
        if rc != 0 and prefix == ["sudo", "-n"] and err and err[0].startswith(("sudo:", "sudo-rs:")):
            continue                        # no setup yet: ask for the admin password instead
        if prefix[0] == "pkexec" and rc in (126, 127) and not out:
            raise SetupNeeded("Admin password was not given.")
        if rc != 0:
            msg = [l for l in err if l and not l.startswith(("pmstatus:", "dlstatus:", "step:", "sudo:", "sudo-rs:"))]
            raise DBError(msg[-1] if msg else "failed (%s)" % args[0])
        try:
            return json.loads(out.splitlines()[-1]) if out else {}
        except ValueError:
            return {"text": out}
    raise SetupNeeded("USB sharing isn't set up on %s yet." % hostname())


def slot_ports(slot):
    rusb = SLOT_BASE + 2 * int(slot)
    return rusb, rusb + 1


# ---------------------------------------------------------------- root helper
class SetupNeeded(DBError):
    pass


_setup_cache = [0.0, False]


def setup_done(max_age=30):
    """True when the optional setup ran for this user (USB sharing available).
    Cheap: asks sudo whether the rule exists, without running anything."""
    now = time.time()
    if now - _setup_cache[0] > max_age:
        try:
            ok = subprocess.run(["sudo", "-n", "-l", HELPER], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        _setup_cache[:] = [now, ok]
    return _setup_cache[1]


def helper(*args, stdin=None, interactive=False):
    """Run the privileged helper: sudo rule after setup; pkexec (admin password
    prompt) only for actions the person started (interactive=True)."""
    cmd = [HELPER] + [str(a) for a in args]
    r = subprocess.run(["sudo", "-n"] + cmd, input=stdin, capture_output=True, text=True)
    if r.returncode != 0 and r.stderr.startswith(("sudo:", "sudo-rs:")):
        if interactive and _escalate():
            r = subprocess.run(_escalate() + cmd, input=stdin, capture_output=True, text=True)
            if r.returncode in (126, 127) and not r.stdout.strip():
                raise SetupNeeded("Admin password was not given.")
        else:
            raise SetupNeeded("USB sharing isn't set up on %s yet (open PairDesk there and click "
                              "\"Set up USB sharing\")." % hostname())
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise DBError(msg[-1] if msg else "helper failed (%s)" % args[0])
    out = r.stdout.strip()
    try:
        return json.loads(out) if out else {}
    except ValueError:
        return {"text": out}


# ---------------------------------------------------------------- USB (sysfs)
def _rd(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def list_local_usb(root=SYS_USB):
    """Physical USB devices on this computer (hubs and imported devices skipped)."""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not BUSID_RE.match(name):
            continue
        p = os.path.join(root, name)
        if _rd(os.path.join(p, "bDeviceClass")) == "09":
            continue
        if "vhci_hcd" in os.path.realpath(p):
            continue
        drv = os.path.join(p, "driver")
        drv = os.path.basename(os.path.realpath(drv)) if os.path.exists(drv) else ""
        vid, pid = _rd(os.path.join(p, "idVendor")), _rd(os.path.join(p, "idProduct"))
        serial = _rd(os.path.join(p, "serial"))
        name_s = " ".join(x for x in (_rd(os.path.join(p, "manufacturer")),
                                      _rd(os.path.join(p, "product"))) if x)
        out.append({
            "busid": name, "usbid": "%s:%s" % (vid, pid), "serial": serial,
            "key": "%s:%s/%s" % (vid, pid, serial) if serial else "%s:%s" % (vid, pid),
            "name": name_s or "USB device %s:%s" % (vid, pid),
            "kind": usb_kind(p), "exported": drv == "usbip-host",
        })
    return out


FIDO_PAGE = b"\x06\xd0\xf1"        # HID Usage Page 0xF1D0 (FIDO / U2F / passkeys)
DIGITIZER_PAGE = b"\x05\x0d"       # HID Usage Page 0x0D (touchscreens, pens, touchpads)


def _hid_descriptors(iface):
    """Raw HID report descriptors of one USB interface (world-readable in sysfs)."""
    data = b""
    try:
        for e in os.listdir(iface):
            f = os.path.join(iface, e, "report_descriptor")
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    data += fh.read()
    except OSError:
        pass
    return data


def usb_kind(p):
    ifs = []
    for e in os.listdir(p) if os.path.isdir(p) else []:
        if ":" in e:
            ip = os.path.join(p, e)
            ifs.append((_rd(os.path.join(ip, "bInterfaceClass")), _rd(os.path.join(ip, "bInterfaceProtocol")), ip))
    classes = {c for c, _, _ in ifs}
    name = (_rd(os.path.join(p, "manufacturer")) + " " + _rd(os.path.join(p, "product"))).lower()
    if _rd(os.path.join(p, "idVendor")) in SECURITY_KEY_VENDORS or "0b" in classes:
        return "security key"
    if "0e" in classes:
        return "camera"
    if "08" in classes:
        return "storage"
    if "01" in classes:
        return "audio"
    if "07" in classes:
        return "printer"
    if "03" in classes:
        hid = [(pr, _hid_descriptors(ip)) for c, pr, ip in ifs if c == "03"]
        desc = b"".join(d for _, d in hid)
        protos = {pr for pr, _ in hid}
        if FIDO_PAGE in desc:                      # checked first: many keys also type OTP codes
            return "security key"
        if DIGITIZER_PAGE in desc or "touch" in name or "pen" in name.split() or "tablet" in name:
            return "touchscreen"
        if "01" in protos:
            return "keyboard"
        if "02" in protos:
            return "mouse"
        if not desc and any(w in name for w in ("fido", "u2f", "security key", "passkey", "token")):
            return "security key"                  # descriptor unreadable: go by the name
        return "input device"
    return "device"


def is_my_input(dev):
    """Keyboards, mice and touchscreens: sharing them takes them away from this computer."""
    return dev.get("kind") in ("keyboard", "mouse", "touchscreen")


def parse_usbip_port(text):
    """Parse `usbip port` output into [{port, host, tcp, busid}]."""
    res, cur = [], None
    for line in text.splitlines():
        m = re.match(r"\s*Port (\d+):", line)
        if m:
            cur = {"port": int(m.group(1))}
            res.append(cur)
            continue
        m = re.search(r"-> usbip://([^:/]+):(\d+)/(\S+)", line)
        if m and cur is not None:
            cur.update(host=m.group(1), tcp=int(m.group(2)), busid=m.group(3))
    return [r for r in res if "busid" in r]


# ---------------------------------------------------------------- misc
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_listening(port):
    hexp = ":%04X" % port
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if parts[1].endswith(hexp) and parts[3] == "0A":
                        return True
        except OSError:
            pass
    return False


def is_mounted(path):
    path = os.path.realpath(path)
    try:
        with open("/proc/self/mounts") as f:
            for line in f:
                mp = line.split()[1].replace("\\040", " ")
                if mp == path:
                    return True
    except OSError:
        pass
    return False


def unmount(path):
    if not is_mounted(path):
        return
    for tool in ("fusermount3", "fusermount"):
        if shutil.which(tool):
            r = subprocess.run([tool, "-u", path], capture_output=True)
            if r.returncode != 0:
                subprocess.run([tool, "-uz", path], capture_output=True)
            return


_rdp_cache = {}


def rdp_client():
    """(path, major version) of the FreeRDP viewer. The version is asked from the program,
    because newer Debian ships FreeRDP 3 under the plain name xfreerdp."""
    for b in ("xfreerdp3", "sdl-freerdp3", "xfreerdp"):
        p = shutil.which(b)
        if p:
            if p not in _rdp_cache:
                ver = 3 if "3" in os.path.basename(p) else 2
                try:
                    out = subprocess.run([p, "/version"], capture_output=True, text=True, timeout=10).stdout
                    m = re.search(r"version (\d+)\.", out)
                    if m:
                        ver = int(m.group(1))
                except (OSError, subprocess.TimeoutExpired):
                    pass
                _rdp_cache[p] = ver
            return p, _rdp_cache[p]
    raise DBError("The remote desktop viewer (FreeRDP) is not installed. Install it with: pairdesk install viewer")


def scan_lan(timeout=6):
    """Find computers with PairDesk remote access on. Returns [{name, address}],
    address being 'user@ip:port' ready to pair with."""
    if not shutil.which("avahi-browse"):
        return []
    try:
        out = subprocess.run(["avahi-browse", "-rpt", "_pairdesk._tcp"], capture_output=True,
                             text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return []
    seen, res = set(), []
    for line in out.splitlines():
        f = line.split(";")
        if len(f) >= 9 and f[0] == "=" and f[2] == "IPv4":
            txt = dict(re.findall(r'"(\w+)=([^"]*)"', f[9] if len(f) > 9 else ""))
            addr = "%s@%s:%s" % (txt.get("user", "?"), f[7], f[8])
            if addr not in seen:
                seen.add(addr)
                res.append({"name": txt.get("name") or f[3].replace("\\032", " "), "address": addr})
    return res


def is_ip(host):
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def to_ip(host, port=None):
    """The IP address for a computer name (IPv4 preferred), or None. An IP stays as it is."""
    if is_ip(host):
        return host
    try:
        infos = socket.getaddrinfo(host, int(port or LINK_PORT), type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
    return (v4 or [i[4][0] for i in infos] or [None])[0]


BANNER_RE = re.compile(r"PairDesk/(\d+) user=(\S+) name=(\S+)")


def probe(host, port=None, timeout=1.5):
    """Ask whatever listens at host:port who it is, before any login (PairDesk's link announces
    itself in its SSH greeting). state: pairdesk (with user and name), ssh (an SSH server: an older
    PairDesk or something else), closed (nothing answers), unknown-host (the name doesn't resolve)."""
    port = int(port or LINK_PORT)
    ip = to_ip(host, port)
    if not ip:
        return {"state": "unknown-host", "host": host, "port": port}
    typed, host = host, ip          # computers are remembered by IP; the typed name is kept as well
    named = {"host_name": typed} if typed != host else {}
    try:
        s = socket.create_connection((host, port), timeout)
    except OSError:
        return dict(named, state="closed", host=host, port=port)
    try:
        s.settimeout(timeout)
        data = s.recv(512).decode("latin-1", "replace")
    except OSError:
        data = ""
    finally:
        s.close()
    m = BANNER_RE.search(data)
    if m:
        return dict(named, state="pairdesk", host=host, port=port, user=m.group(2), name=m.group(3),
                    proto=int(m.group(1)), address="%s@%s:%d" % (m.group(2), host, port))
    return dict(named, state="ssh" if data.startswith("SSH-") else "other", host=host, port=port)


def resolve_address(text):
    """What the person typed (an IP, a computer name, user@host, host:port) -> probe result, with the
    user name filled in from the remote computer when they didn't type one."""
    text = (text or "").strip().replace("pairdesk://", "")
    if not text:
        raise DBError("Type the address shown on the other computer, e.g. 192.168.1.20")
    typed_user = text.rsplit("@", 1)[0] if "@" in text else None
    try:
        user, host, port = parse_address(text)
    except ValueError:
        raise DBError("That doesn't look like an address. Type it like 192.168.1.20 (or 192.168.1.20:4722).")
    r = probe(host, port)
    if typed_user:
        r["user"] = typed_user
    if r.get("user"):
        r["address"] = "%s@%s:%d" % (r["user"], host, port)
    r["typed_user"] = typed_user
    return r


def _local_nets():
    """IPv4 addresses of this computer on real network interfaces, with their prefix length."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True).stdout
    except OSError:
        out = ""
    res = []
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4 or f[1] == "lo" or f[1].startswith(("docker", "br-", "veth", "virbr", "podman", "cni", "tun")):
            continue
        ip, _, pre = f[3].partition("/")
        res.append((ip, int(pre or 24)))
    if not res:     # no "ip" tool: the address used to reach other networks, as a /24
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))
            res.append((s.getsockname()[0], 24))
            s.close()
        except OSError:
            pass
    return res


def installed_version():
    r = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}|${Version}", APP], capture_output=True, text=True)
    st, _, v = r.stdout.strip().partition("|")
    return v if r.returncode == 0 and st.startswith("ii") else None


def local_package_file():
    """A PairDesk .deb on this computer, to install on another one: the installed version from apt's
    cache or Downloads if there is one, else the newest found. None when there's none."""
    pats = ["/var/cache/apt/archives/%s_*.deb" % APP] + [os.path.expanduser("~/%s/%s_*.deb" % (d, APP))
                                                         for d in ("Downloads", "Desktop", ".")]
    files = [f for pat in pats for f in glob.glob(pat) if os.path.isfile(f)]
    if not files:
        return None
    want = installed_version()

    def ver(f):
        r = subprocess.run(["dpkg-deb", "-f", f, "Version"], capture_output=True, text=True)
        return r.stdout.strip()
    vers = {f: ver(f) for f in files}
    same = [f for f, v in vers.items() if v and v == want]
    if same:
        return same[0]
    best = None
    for f, v in vers.items():
        if v and (best is None or subprocess.run(["dpkg", "--compare-versions", v, "gt", vers[best]]).returncode == 0):
            best = f
    return best


# ----------------------------------------------------------------- updates between paired computers
UPDATE_MAX = 50 * 1024 * 1024
DEB_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~:-]{0,60}$")


def remote_needs(info, opts, peer):
    """Add-ons the other computer is missing for the chosen options (from its own report)."""
    f = info.get("features") or {}
    need = []
    if opts.get("share_mine") and f.get("folders") is False:
        need.append("folders")
    if opts.get("open_theirs") and f.get("share-out") is False:
        need.append("share-out")
    if (peer.get("usb_send") or peer.get("usb_take")) and info.get("usb_ready") is False:
        need.append("usb")
    return need


def remote_install_command(need):
    """What to run on the other computer (over SSH) to add those add-ons; USB also needs its setup."""
    parts = ["pairdesk install %s" % n for n in need if n != "usb"]
    if "usb" in need:
        parts.append("pairdesk install usb && pairdesk setup")
    return " && ".join(parts)


def updates_dir():
    """Where a PairDesk package received from a paired computer waits to be installed. Fixed (not
    XDG_CACHE_HOME), because the root helper only installs packages from exactly this folder."""
    return os.path.join(os.path.expanduser("~"), ".cache", APP, "updates")


def version_newer(a, b):
    """True if Debian version a is newer than b."""
    if not a or not b:
        return False
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def my_version():
    """The installed package version (e.g. 2.7.0-1), or the program version when run from source."""
    return installed_version() or VERSION


def update_direction(mine, theirs):
    """'pull' when the other computer has the newer PairDesk, 'push' when the current one does."""
    if version_newer(theirs, mine):
        return "pull"
    if version_newer(mine, theirs):
        return "push"
    return None


def deb_fields(path):
    r = subprocess.run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture"],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out if r.returncode == 0 else {}


def repack_installed(dest_dir):
    """Build a .deb of the PairDesk installed here from its installed files (like dpkg-repack),
    for when the original package file is gone (apt cache cleaned)."""
    ver = installed_version()
    if not ver:
        raise DBError("PairDesk isn't installed as a package on %s, so it can't be sent." % hostname())
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    out = os.path.join(dest_dir, "%s_%s_all.deb" % (APP, ver.replace(":", "%3a")))
    if os.path.isfile(out) and deb_fields(out).get("Version") == ver:
        return out
    tmp = tempfile.mkdtemp(prefix="repack-", dir=dest_dir)
    try:
        os.chmod(tmp, 0o755)
        listed = subprocess.run(["dpkg-query", "-L", APP], capture_output=True, text=True).stdout.splitlines()
        for f in listed:
            if not f.startswith("/") or f == "/." or not os.path.lexists(f):
                continue
            t = tmp + f
            if os.path.isdir(f) and not os.path.islink(f):
                os.makedirs(t, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(t), exist_ok=True)
            if os.path.islink(f):
                os.symlink(os.readlink(f), t)
            else:
                shutil.copy2(f, t)
        deb = os.path.join(tmp, "DEBIAN")
        os.makedirs(deb, mode=0o755, exist_ok=True)
        status = subprocess.run(["dpkg-query", "-s", APP], capture_output=True, text=True).stdout
        keep, skip = [], False
        for line in status.splitlines():
            if line[:1] in (" ", "\t"):
                if not skip:
                    keep.append(line)
                continue
            skip = line.split(":", 1)[0] in ("Status", "Config-Version", "Conffiles")
            if not skip:
                keep.append(line)
        with open(os.path.join(deb, "control"), "w") as fh:
            fh.write("\n".join(keep) + "\n")
        for part in ("preinst", "postinst", "prerm", "postrm", "conffiles", "triggers", "md5sums"):
            src = "/var/lib/dpkg/info/%s.%s" % (APP, part)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(deb, part))
                os.chmod(os.path.join(deb, part), 0o755 if part.endswith(("inst", "rm")) else 0o644)
        r = subprocess.run(["dpkg-deb", "--root-owner-group", "--build", tmp, out + ".part"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise DBError("Couldn't pack PairDesk for sending: " + (r.stderr.strip() or "dpkg-deb failed"))
        os.replace(out + ".part", out)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def package_for_peer():
    """A .deb of exactly the PairDesk installed here, to send to a paired computer."""
    ver = installed_version()
    f = local_package_file()
    if f and ver and deb_fields(f).get("Version") == ver:
        return f
    return repack_installed(os.path.join(os.path.expanduser("~"), ".cache", APP, "send"))


def store_package(tmp):
    """Check a received file is a newer PairDesk package and keep it in updates_dir().
    Returns (path, version). The file is removed when it isn't acceptable."""
    try:
        f = deb_fields(tmp)
        ver = f.get("Version", "")
        if f.get("Package") != APP or f.get("Architecture") != "all" or not DEB_VERSION_RE.match(ver):
            raise DBError("what arrived isn't a PairDesk package")
        have = installed_version()
        if have and not version_newer(ver, have):
            raise DBError("PairDesk %s isn't newer than %s on %s" % (ver, have, hostname()))
    except DBError:
        os.unlink(tmp)
        raise
    d = updates_dir()
    final = os.path.join(d, "%s_%s_all.deb" % (APP, ver.replace(":", "%3a")))
    for old in glob.glob(os.path.join(d, "%s_*.deb" % APP)):
        if old != final:
            os.unlink(old)
    os.replace(tmp, final)
    os.chmod(final, 0o600)
    return final, ver


def receive_package(stream, peer):
    """Read a PairDesk package sent by a paired computer (at most UPDATE_MAX bytes), keep it, and
    remember it so PairDesk offers to install it here. Nothing is installed without an administrator."""
    d = updates_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
    size = 0
    with os.fdopen(fd, "wb") as out:
        while True:
            b = stream.read(65536)
            if not b:
                break
            size += len(b)
            if size > UPDATE_MAX:
                out.close()
                os.unlink(tmp)
                raise DBError("the package is too large")
            out.write(b)
    path, ver = store_package(tmp)
    c = load_config()
    c["pending_update"] = {"path": path, "version": ver, "peer": peer}
    save_config(c)
    return path, ver


def pending_update():
    """The update a paired computer sent, if it's still there and still newer than what's installed."""
    p = load_config().get("pending_update") or {}
    if p.get("path") and os.path.isfile(p["path"]) and version_newer(p.get("version"), installed_version() or "0"):
        return p
    return None


def install_update(path, on_line=None):
    """Install a received PairDesk package. Always asks for an administrator's password (pkexec),
    even after USB setup, so a paired computer can never install software here by itself."""
    rep = helper_stream("install-update", path, on_line=on_line, admin=True)
    c = load_config()
    if c.pop("pending_update", None) is not None:
        save_config(c)
    return rep


def ssh_ctl_opts():
    """One shared SSH connection per computer for 10 minutes: the password is asked once per session
    (by PairDesk's own window), never saved."""
    return ["-o", "ControlMaster=auto", "-o", "ControlPersist=600",
            "-o", "ControlPath=" + os.path.join(run_dir(), "ssh-%C")]


def ssh_run(login, command, timeout=120):
    """Run a command on another computer through its normal SSH login (e.g. alex@192.168.1.20)."""
    env = dict(os.environ, SSH_ASKPASS="/usr/bin/pairdesk", SSH_ASKPASS_REQUIRE="force")
    cmd = ["ssh"] + ssh_ctl_opts() + ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=ask",
                                      "-o", "NumberOfPasswordPrompts=3", login, command]
    try:
        r = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DBError("%s didn't answer over SSH in time." % login)
    if r.returncode == 255:
        err = (r.stderr.strip().splitlines() or ["no reply"])[-1]
        why = ("the password or user name wasn't accepted" if "Permission denied" in err else
               "its SSH server isn't running (sudo apt install openssh-server there)" if "refused" in err else
               "that computer wasn't found" if "resolve" in err else
               "the computer's identity wasn't confirmed" if "Host key verification" in err else err)
        raise DBError("Couldn't sign in to %s over SSH: %s." % (login, why))
    return r


def ssh_get_code(login):
    """Pair without reading the other screen: fetch the pairing code over SSH (turns remote access on there)."""
    r = ssh_run(login, "pairdesk pair-code --plain --turn-on")
    m = re.search(r"\b[%s]{4}(?:-[%s]{4}){3}\b" % (CODE_ALPHABET, CODE_ALPHABET), r.stdout)
    if m:
        return m.group(0)
    if r.returncode == 127 or "not found" in r.stderr:
        raise DBError("PairDesk isn't installed on that computer yet.")
    raise DBError((r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["no code came back"])[-1])


def remote_setup_script(deb_name, version, client):
    """Shell script run on the other computer over SSH: install or update PairDesk only when needed
    (never downgrade), then the guided setup, which checks each step and asks before changing anything."""
    q = shlex.quote
    return """#!/bin/sh
# PairDesk setup, sent from %(client)s
have=$(dpkg-query -W -f='${db:Status-Abbrev}|${Version}' pairdesk 2>/dev/null || true)
case "$have" in ii*) have=${have#*|} ;; *) have="" ;; esac     # removed but not purged counts as missing
if [ -z "$have" ] || dpkg --compare-versions "$have" lt %(ver)s; then
    echo "Installing PairDesk %(ver)s${have:+ (replacing $have)}. sudo asks for your password on this computer."
    sudo apt-get install -y /tmp/%(deb)s || { echo "Installing failed (see above)."; rm -f /tmp/%(deb)s; exit 1; }
else
    echo "PairDesk $have is already installed here."
fi
rm -f /tmp/%(deb)s /tmp/pairdesk-setup.sh
echo
exec pairdesk headless-setup --from-client %(client)s
""" % {"client": q(client), "ver": q(version), "deb": q(deb_name)}


def find_computers(timeout=0.5):
    """Computers on the local network with PairDesk remote access on, without any add-on: a quick
    knock on PairDesk's port across this computer's network (the /24 around each address), then each
    answer is asked who it is. Computers announced over Avahi on other ports are added too."""
    import concurrent.futures
    import ipaddress
    mine, hosts = set(), []
    for ip, pre in _local_nets():
        mine.add(ip)
        net = ipaddress.ip_network("%s/%d" % (ip, max(pre, 24)), strict=False)
        hosts += [str(h) for h in net.hosts() if str(h) != ip]
    ports = sorted({LINK_PORT, link_port()})

    def knock(h):
        for port in ports:
            try:
                socket.create_connection((h, port), timeout).close()
            except OSError:
                continue
            r = probe(h, port, timeout=1.5)
            if r["state"] == "pairdesk":
                return r
        return None
    found = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(knock, dict.fromkeys(hosts)):
            if r:
                found[(r["host"], r["port"])] = r
    for it in scan_lan(timeout=3):
        try:
            user, host, port = parse_address(it["address"])
        except (DBError, ValueError):
            continue
        if host not in mine and (host, port) not in found:
            found[(host, port)] = {"state": "pairdesk", "host": host, "port": port, "user": user,
                                   "name": it.get("name") or host, "address": it["address"]}
    # one entry per computer (a computer on Ethernet and Wi-Fi answers twice), never the current one
    me, best = hostname(), {}
    for r in sorted(found.values(), key=lambda r: tuple(int(x) if x.isdigit() else 0 for x in r["host"].split("."))):
        if r["name"] == me:
            continue
        k = (r["name"], r["user"], r["port"])
        if k in best:
            best[k].setdefault("also", []).append(r["host"])
        else:
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["name"].lower(), r["host"]))


def ssh_speed_opts(single=False):
    """Fastest safe cipher for this CPU: AES-GCM with AES-NI, else ChaCha20 (older CPUs without AES-NI).
    single=True for sshfs, which splits option values on commas."""
    try:
        aesni = " aes " in " %s " % open("/proc/cpuinfo").read().split("flags", 2)[1].split("\n")[0]
    except (OSError, IndexError):
        aesni = False
    order = ["aes128-gcm@openssh.com", "chacha20-poly1305@openssh.com"]
    if not aesni:
        order.reverse()
    ciphers = order[:1] if single else order + ["aes256-gcm@openssh.com", "aes128-ctr"]
    return ["-o", "Ciphers=" + ",".join(ciphers), "-o", "Compression=no"]


def reachable(host, port=22, timeout=1.5):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wake_on_lan(targets):
    """targets: [{mac, bcast}] -> send magic packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sent = 0
    for t in targets:
        try:
            pkt = b"\xff" * 6 + bytes.fromhex(t["mac"].replace(":", "").replace("-", "")) * 16
        except (KeyError, ValueError):
            continue
        for addr in {t.get("bcast") or "255.255.255.255", "255.255.255.255"}:
            for port in (9, 7):
                try:
                    s.sendto(pkt, (addr, port))
                    sent += 1
                except OSError:
                    pass
    s.close()
    return sent


def wol_targets():
    """Wired network cards of this computer (for Wake-on-LAN), from sysfs and `ip`."""
    res = []
    try:
        data = json.loads(subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True).stdout or "[]")
    except (OSError, ValueError):
        data = []
    for it in data:
        name = it.get("ifname", "")
        if name == "lo" or os.path.exists("/sys/class/net/%s/wireless" % name) \
                or not os.path.exists("/sys/class/net/%s/device" % name):
            continue
        mac = _rd("/sys/class/net/%s/address" % name)
        for a in it.get("addr_info", []):
            res.append({"mac": mac, "bcast": a.get("broadcast"), "ifname": name})
            break
    return res


def log_file():
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), APP)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "pairdesk.log")


def flog(msg):
    try:
        p = log_file()
        if os.path.exists(p) and os.path.getsize(p) > 1_000_000:
            os.replace(p, p + ".1")
        with open(p, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def launcher_path(name, kind):
    if kind == "autostart":
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                            "applications")
    return os.path.join(base, "pairdesk-connect-%s.desktop" % safe_name(name))


def set_launcher(name, kind, on):
    """kind 'menu': app-menu entry "PairDesk: NAME"; 'autostart': connect at login."""
    p = launcher_path(name, kind)
    if not on:
        if os.path.exists(p):
            os.unlink(p)
        return p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=PairDesk: %s\nComment=Connect to %s\n"
                "Exec=pairdesk --connect %s\nIcon=pairdesk\nTerminal=false\nCategories=Network;RemoteAccess;\n"
                % (name, name, safe_name(name)))
        if kind == "autostart":
            f.write("X-GNOME-Autostart-Delay=8\nX-GNOME-Autostart-enabled=true\n")
    os.chmod(p, 0o755)
    return p


# ---------------------------------------------------------------- authorized_keys
# ---------------------------------------------------------------- link service
# PairDesk's own SSH server: runs as the user (never root) on its own port, with its
# own host key and allowed-computer list. It runs only while remote access is on, so
# with remote access off no PairDesk port is open at all.
AK_TAG = "pairdesk-peer="
AK_REV = "pairdesk-rev="          # 1.x only (removed on upgrade)
PAIR_TAG = "pairdesk-pairing"
LINK_PORT = 4722
HOMEPAGE = "https://pairdesk.knowreva.com"
KNOCK = "/usr/lib/pairdesk/pairdesk-knock"   # lets a not-yet-paired computer's fresh key reach the pairing gate
PAIR_PROTO = 2       # 2: a new random knock key for every pairing attempt (no shared key)
LINK_UNIT = "pairdesk-link.service"
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L: easy to read aloud
SFTP_SERVERS = ("/usr/lib/openssh/sftp-server", "/usr/libexec/openssh/sftp-server",
                "/usr/lib/ssh/sftp-server", "/usr/libexec/sftp-server")

SSHD_CONFIG = """# PairDesk link service - generated, do not edit (Settings > Port changes it)
Port {port}
HostKey {dir}/ssh_host_ed25519_key
PidFile {dir}/sshd.pid
AuthorizedKeysFile {dir}/authorized_keys
AllowUsers {user}
# lets "Add a remote computer" find this computer and its user name before any login
VersionAddendum PairDesk/2 user={user} name={name}
# a computer that isn't paired yet knocks with a fresh random key: it may only reach the pairing gate
AuthorizedKeysCommand /usr/lib/pairdesk/pairdesk-knock %u %t %k
AuthorizedKeysCommandUser {user}
ExposeAuthInfo yes
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
PermitRootLogin no
StrictModes yes
PermitTTY no
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
GatewayPorts no
AllowTcpForwarding yes
AllowStreamLocalForwarding yes
StreamLocalBindUnlink yes
StreamLocalBindMask 0177
PermitUserEnvironment no
PermitUserRC no
# drop a connection whose computer went away (sleep, Wi-Fi gone) within 30 s, so its ports are free again
ClientAliveInterval 10
ClientAliveCountMax 3
HostKeyAlgorithms ssh-ed25519
PubkeyAcceptedAlgorithms ssh-ed25519
KexAlgorithms {kex}
Ciphers chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com
LoginGraceTime 20
MaxAuthTries 3
MaxSessions 20
MaxStartups 10:30:60
LogLevel INFO
"""


def link_dir():
    d = os.path.join(cfg_dir(), "link")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def link_ak_path():
    return os.path.join(link_dir(), "authorized_keys")


def link_port(c=None):
    return int((c or load_config()).get("link_port") or LINK_PORT)


def link_hostkey():
    """The link service's host key ('ssh-ed25519 AAAA...'), created on first use."""
    k = os.path.join(link_dir(), "ssh_host_ed25519_key")
    if not os.path.exists(k):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", k,
                        "-C", "pairdesk-link@" + hostname()], check=True)
    with open(k + ".pub") as f:
        return " ".join(f.read().split()[:2])


def write_link_config():
    d = link_dir()
    link_hostkey()
    user = os.environ.get("USER") or getpass_user()
    path = os.path.join(d, "sshd_config")
    for kex in ("sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256@libssh.org",
                "curve25519-sha256,curve25519-sha256@libssh.org"):     # post-quantum where supported
        with open(path, "w") as f:
            f.write(SSHD_CONFIG.format(port=link_port(), dir=d, user=user, kex=kex, name=hostname()))
        os.chmod(path, 0o600)
        r = subprocess.run(["/usr/sbin/sshd", "-t", "-f", path], capture_output=True, text=True)
        if r.returncode == 0:
            break
    else:
        raise DBError("The link service config was refused: " + r.stderr.strip())
    ak = link_ak_path()
    if not os.path.exists(ak):
        open(ak, "w").close()
    # the shared pairing key of PairDesk 2.8 and earlier is no longer let in by the file (pairdesk-knock
    # lets any not-yet-paired key reach the pairing gate instead)
    _edit_lines(ak, lambda l: not l.rstrip().endswith(PAIR_TAG))
    os.chmod(ak, 0o600)
    return path


def getpass_user():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def link_pid():
    try:
        pid = int(open(os.path.join(link_dir(), "sshd.pid")).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def link_running():
    return link_pid() is not None and port_listening(link_port())


def _user_systemd():
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def link_start():
    """Open the PairDesk port (no admin rights needed)."""
    if not os.path.exists("/usr/sbin/sshd"):
        raise SetupNeeded("The SSH server program (openssh-server) is not installed.")
    write_link_config()
    c = load_config()
    if _user_systemd():
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", LINK_UNIT], capture_output=True)
        subprocess.run(["systemctl", "--user", "restart", LINK_UNIT], capture_output=True)
        # keep running after logout (allowed for your own account on most systems)
        lg = subprocess.run(["loginctl", "show-user", getpass_user(), "-p", "Linger"],
                            capture_output=True, text=True).stdout.strip()
        if lg == "Linger=no" and subprocess.run(["loginctl", "enable-linger"],
                                                capture_output=True).returncode == 0:
            c["linger_by_pairdesk"] = True
            save_config(c)
    else:   # no user systemd (rare): run it as a background process
        if not link_pid():
            subprocess.Popen(["/usr/bin/pairdesk", "link-run"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if link_running():
            return True
        time.sleep(0.1)
    raise DBError("The PairDesk link service did not start on port %d (is the port in use?). "
                  "See %s" % (link_port(), os.path.join(link_dir(), "sshd.log")))


def link_refresh():
    """After an upgrade: rewrite the running link's settings and have sshd reload them (SIGHUP), so
    connections that are open stay up. Returns True when a running link was refreshed."""
    pid = link_pid()
    if not pid:
        return False
    write_link_config()
    os.kill(pid, 1)
    return True


def link_stop():
    """Close the PairDesk port. Open sessions end."""
    if _user_systemd():
        subprocess.run(["systemctl", "--user", "disable", "--now", LINK_UNIT], capture_output=True)
    pid = link_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    c = load_config()
    if c.pop("linger_by_pairdesk", None):
        subprocess.run(["loginctl", "disable-linger"], capture_output=True)
        save_config(c)


def link_run():
    """Foreground: the link service itself (started by systemd --user or link_start)."""
    import signal
    cfg = write_link_config()
    if not pairing_open():
        stop_pairing()          # an old, expired pairing window: remove its key line
    procs = [subprocess.Popen(["/usr/sbin/sshd", "-D", "-f", cfg, "-E", os.path.join(link_dir(), "sshd.log")])]
    if shutil.which("avahi-publish"):   # let "Find on network" see this computer
        procs.append(subprocess.Popen(["avahi-publish", "-s", "PairDesk on %s" % hostname(), "_pairdesk._tcp",
                                       str(link_port()), "user=%s" % getpass_user(), "name=%s" % hostname()],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def stop(*_):
        for p in procs:
            p.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    rc = procs[0].wait()
    stop()
    return rc


# ---------------------------------------------------------------- allowed computers
def ak_line(pubkey, peer, slot):
    peer = safe_name(peer)
    rusb = slot_ports(slot)[0]
    opts = ('command="/usr/bin/pairdesk-remote serve --peer %s --slot %d",restrict,port-forwarding,'
            'permitopen="127.0.0.1:3389",permitopen="127.0.0.1:3240",permitlisten="127.0.0.1:%d"'
            % (peer, int(slot), rusb))
    kt, kb = pubkey.split()[:2]
    return "%s %s %s %s%s" % (opts, kt, kb, AK_TAG, peer)


def _edit_lines(path, keep, add=None):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    new = [l for l in lines if keep(l)] + ([add] if add else [])
    if new != lines:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(new) + ("\n" if new else ""))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def ak_set(peer, line):
    tag = AK_TAG + safe_name(peer)
    _edit_lines(link_ak_path(), lambda l: not l.rstrip().endswith(tag), line)


def kh_set(alias, hostkey):
    p = known_hosts()
    _edit_lines(p, lambda l: l.split(" ", 1)[0] != alias, "%s %s" % (alias, hostkey))
    os.chmod(p, 0o600)


LEGACY_APP = "deskbridge"     # PairDesk was called DeskBridge until version 2.4


def migrate_from_deskbridge():
    """Bring DeskBridge's settings, keys and pairings over once, so paired computers keep working
    without pairing again. The old folder is left as it was. Returns True when something was moved."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    old, new = os.path.join(base, LEGACY_APP), os.path.join(base, APP)
    if not os.path.isfile(os.path.join(old, "config.json")) or os.path.exists(os.path.join(new, "config.json")):
        return False
    shutil.copytree(old, new, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("sshd.pid", "*.sock", "sshd.log", "*.tmp"))
    swaps = [("/usr/bin/deskbridge-remote", "/usr/bin/pairdesk-remote"), ("/usr/bin/deskbridge", "/usr/bin/pairdesk"),
             ("/usr/lib/deskbridge/", "/usr/lib/pairdesk/"), ("/.config/deskbridge/", "/.config/pairdesk/"),
             ("/.config/deskbridge\n", "/.config/pairdesk\n"), ("deskbridge-peer=", "pairdesk-peer="),
             ("deskbridge-pairing", "pairdesk-pairing")]
    for dp, _dirs, files in os.walk(new):
        for f in files:
            p = os.path.join(dp, f)
            if f == "config.json":
                with open(p) as fh:
                    c = json.load(fh)
                if c.pop("linger_by_deskbridge", None):
                    c["linger_by_pairdesk"] = True
                with open(p, "w") as fh:
                    json.dump(c, fh, indent=2)
                continue
            try:
                with open(p) as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            t = text
            for a, b in swaps:
                t = t.replace(a, b)
            if t != text:
                mode = os.stat(p).st_mode
                with open(p, "w") as fh:
                    fh.write(t)
                os.chmod(p, mode)
    flog("Moved settings and pairings over from DeskBridge (%s)" % old)
    return True


def migrate_v2():
    """Upgrade from 1.x: PairDesk no longer uses the system SSH login. Remove its old
    lines from ~/.ssh/authorized_keys; old pairings must be made again with a code."""
    p = os.path.expanduser("~/.ssh/authorized_keys")
    if os.path.exists(p):
        _edit_lines(p, lambda l: not re.search(r"pairdesk-(peer|rev)=\S+\s*$", l))
    c = load_config()
    old = [n for n, v in c["peers"].items() if not v.get("link") and not v.get("needs_pairing")]
    inc = [n for n, v in c["incoming"].items() if not v.get("link")]
    if old or inc:
        for n in old:
            c["peers"][n]["needs_pairing"] = True
        for n in inc:
            c["incoming"].pop(n)
        save_config(c)
    return old


# ---------------------------------------------------------------- pairing with a code
def _norm_code(code):
    return "".join(ch for ch in code.upper() if ch in CODE_ALPHABET)


def _proof(code, role, hostkey, pubkey):
    import hashlib
    import hmac
    msg = "pairdesk-pair-v1|%s|%s|%s" % (role, " ".join(hostkey.split()[:2]), " ".join(pubkey.split()[:2]))
    return hmac.new(_norm_code(code).encode(), msg.encode(), hashlib.sha256).hexdigest()


def knock_key(host, port):
    """The client's knock key for pairing with host:port: random, made for this pairing attempt only (the
    code request and the pairing itself use the same one), deleted when paired or after 15 minutes."""
    path = os.path.join(run_dir(), "knock-%s-%d" % (re.sub(r"[^A-Za-z0-9.-]", "_", host), int(port)))
    try:
        fresh = time.time() - os.path.getmtime(path) < 15 * 60
    except OSError:
        fresh = False
    if not fresh:
        for f in (path, path + ".pub"):
            if os.path.exists(f):
                os.unlink(f)
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pairdesk-knock", "-f", path],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise DBError("Couldn't make a pairing key: " + r.stderr.strip())
    return path


def forget_knock_key(host, port):
    path = os.path.join(run_dir(), "knock-%s-%d" % (re.sub(r"[^A-Za-z0-9.-]", "_", host), int(port)))
    for f in (path, path + ".pub"):
        if os.path.exists(f):
            os.unlink(f)


def knocking_key():
    """Server side, in the pairing gate: the key the knocking computer used (sshd's ExposeAuthInfo)."""
    try:
        with open(os.environ.get("SSH_USER_AUTH") or "") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and p[0] == "publickey":
                    return "%s %s" % (p[1], p[2])
    except OSError:
        pass
    return None


def fmt_code(code):
    return "-".join(code[i:i + 4] for i in range(0, 16, 4))


def start_pairing(minutes=10, requested_by=None, knock=None):
    """Server side: open a pairing window (or reuse the open one) and return the code."""
    import secrets
    c = load_config()
    if c.get("mode") not in ("server", "both"):
        raise DBError("Turn on remote access first.")
    if not link_running() and requested_by is None:
        link_start()
    pr = c.get("pairing")
    if not pr or pr["expires"] < time.time():
        pr = {"code": "".join(secrets.choice(CODE_ALPHABET) for _ in range(16)),       # ~79 bits
              "expires": int(time.time()) + minutes * 60, "tries": 0}
    if requested_by:
        pr["requested_by"] = requested_by
        pr["request_id"] = pr.get("request_id", 0) + 1
        if pr.get("knock") and pr["knock"] != knock:     # another computer asks: new code, only for it
            pr["code"] = "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))
            pr["tries"] = 0
        pr["knock"] = knock
    c["pairing"] = pr
    save_config(c)
    os.chmod(os.path.join(cfg_dir(), "config.json"), 0o600)
    return fmt_code(pr["code"])


def stop_pairing():
    c = load_config()
    if c.pop("pairing", None) is not None:
        save_config(c)


def pairing_open():
    pr = load_config().get("pairing")
    return bool(pr) and pr["expires"] > time.time()


def server_accept_code(req, knock=None):
    """Runs on the server (the pairing gate). knock: the key the computer knocked with; when the code was
    asked for by a computer, only that same knock key can use it."""
    import hmac
    c = load_config()
    pr = c.get("pairing")
    if c.get("mode") not in ("server", "both"):
        raise DBError("%s is not accepting connections (remote access is off there)." % hostname())
    if not pr or pr["expires"] < time.time():
        stop_pairing()
        raise DBError("No pairing is open on %s. Press \"Pair a new computer\" there to get a code." % hostname())
    if pr.get("knock") and knock != pr["knock"]:
        raise DBError("This pairing code was asked for by another computer. Ask for a new code.")
    hk = link_hostkey()
    if not hmac.compare_digest(_proof(pr["code"], "client", hk, req["pubkey"]), req.get("proof", "")):
        pr["tries"] = pr.get("tries", 0) + 1
        if pr["tries"] >= 3:
            stop_pairing()
            raise DBError("Wrong code. Pairing was closed after 3 wrong tries; start it again on %s." % hostname())
        c["pairing"] = pr
        save_config(c)
        raise DBError("Wrong code (%d of 3 tries)." % pr["tries"])
    peer = safe_name(req["name"])
    used = {v["slot"] for k, v in c["incoming"].items() if k != peer}
    slot = c["incoming"].get(peer, {}).get("slot")
    if slot is None or slot in used:
        slot = next(i for i in range(200) if i not in used)
    ak_set(peer, ak_line(req["pubkey"], peer, slot))
    rsock = os.path.join(link_dir(), "folder-%s.sock" % peer)
    c = load_config()
    c["incoming"][peer] = {"slot": slot, "user": req["user"], "added": int(time.time()), "link": True}
    c.pop("pairing", None)
    save_config(c)
    return {"name": hostname(), "user": getpass_user(), "slot": slot, "rusb": slot_ports(slot)[0],
            "rsock": rsock, "proof": _proof(pr["code"], "server", hk, req["pubkey"])}


def gui_running():
    try:
        pid = int(open(os.path.join(run_dir(), "gui.pid")).read())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


SESSION_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP")


def session_env():
    """Environment of the user's signed-in desktop session, so PairDesk's link service (whose commands
    start with a bare environment) can show a window on that screen: the pairing code, the sign-out notice."""
    env = dict(os.environ)
    run = "/run/user/%d" % os.getuid()
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(run):
        env["XDG_RUNTIME_DIR"] = run
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(os.path.join(run, "bus")):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + os.path.join(run, "bus")
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                             timeout=10, env=env).stdout
        for line in out.splitlines():
            k, _, v = line.partition("=")
            if k in SESSION_KEYS and v:
                env[k] = v
    except (OSError, subprocess.TimeoutExpired):
        pass
    _drop_dead_display(env)
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        found = _desktop_process_env()
        for k in SESSION_KEYS:
            if found.get(k):
                env[k] = found[k]
    return env


def _drop_dead_display(env):
    """The user manager keeps the display of a session that has ended: only keep displays that exist."""
    w = env.get("WAYLAND_DISPLAY")
    if w and not os.path.exists(w if w.startswith("/") else os.path.join(env.get("XDG_RUNTIME_DIR", ""), w)):
        env.pop("WAYLAND_DISPLAY")
    m = re.match(r"^[^:]*:(\d+)", env.get("DISPLAY", ""))
    if env.get("DISPLAY") and m and not env["DISPLAY"].split(":")[0] and \
            not os.path.exists("/tmp/.X11-unix/X" + m.group(1)):
        env.pop("DISPLAY")
    return env


def _desktop_process_env():
    """The display settings of one of this user's desktop-session processes (gnome-shell first)."""
    best, uid = {}, os.getuid()
    for d in glob.glob("/proc/[0-9]*"):
        try:
            if os.stat(d).st_uid != uid:
                continue
            raw = open(os.path.join(d, "environ"), "rb").read().split(b"\0")
            comm = open(os.path.join(d, "comm")).read().strip()
        except OSError:
            continue
        e = {}
        for item in raw:
            k, _, v = item.decode("utf-8", "replace").partition("=")
            if k in SESSION_KEYS:
                e[k] = v
        if e.get("WAYLAND_DISPLAY") or e.get("DISPLAY"):
            if comm in ("gnome-shell", "gnome-session-b", "plasmashell", "xfce4-session", "cinnamon", "mate-session"):
                return e
            best = best or e
    return best


def server_pair_request(req, knock=None):
    """A remote computer asks to pair: open a pairing window and show its code on this screen. The code is
    tied to the key it knocked with."""
    c = load_config()
    if c.get("mode") not in ("server", "both"):
        raise DBError("%s is not accepting connections (remote access is off there)." % hostname())
    if c.get("pair_only_on_press") and not pairing_open():
        raise DBError("%s only accepts pairing after \"Pair a new computer\" is pressed there." % hostname())
    now = time.time()
    recent = [t for t in c.get("pair_requests", []) if now - t < 60]
    if len(recent) >= 5:
        raise DBError("Too many pairing requests; wait a minute.")
    c["pair_requests"] = recent + [now]
    save_config(c)
    who = "%s (user %s)" % (safe_name(req.get("name", "?")), safe_name(req.get("user", "?")))
    start_pairing(requested_by=who, knock=knock)
    shown = gui_running()                 # the open window shows it within a second
    env = session_env()
    if not shown and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        subprocess.Popen(["/usr/bin/pairdesk", "--pairing-popup"], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        shown = True
    if shutil.which("notify-send") and env.get("DBUS_SESSION_BUS_ADDRESS"):
        subprocess.Popen(["notify-send", "-a", "PairDesk", "-u", "critical", "-i", "pairdesk",
                          "%s wants to pair" % who, "Open PairDesk to see the pairing code."],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # no screen (e.g. no monitor): the code stays open for 10 minutes; read it over SSH
    return {"ok": True, "name": hostname(), "headless": not shown}


def parse_address(addr):
    """'user@host:port' (user and port optional) -> (user, host, port)."""
    addr = addr.strip()
    user = None
    if "@" in addr:
        user, addr = addr.rsplit("@", 1)
    port = LINK_PORT
    if addr.startswith("["):        # [ipv6]:port
        host, _, rest = addr[1:].partition("]")
        if rest.startswith(":"):
            port = int(rest[1:])
    elif addr.count(":") == 1:
        host, p = addr.split(":")
        port = int(p)
    else:
        host = addr
    if not host:
        raise DBError("Type the address shown on the remote computer, e.g. alex@192.168.1.20:4722")
    return user or getpass_user(), host, port


def _pair_call(address, command, payload, hk=None):
    """Run a pairing-gate command on the remote computer, knocking with this attempt's random key."""
    user, host, port = parse_address(address)
    found = probe(host, port)
    if found.get("state") == "pairdesk" and found.get("proto", PAIR_PROTO) < PAIR_PROTO:
        raise DBError("%s has an older PairDesk (2.8 or earlier) that can't pair with this one. Update it "
                      "first: on that computer, or here with + > Set it up over SSH." % found.get("name", host))
    alias = host if port == 22 else "[%s]:%d" % (host, port)
    if hk is None:
        r = subprocess.run(["ssh-keyscan", "-T", "6", "-t", "ed25519", "-p", str(port), host],
                           capture_output=True, text=True, timeout=20)
        keys = [l.split(None, 1)[1] for l in r.stdout.splitlines() if l and not l.startswith("#")]
        if not keys:
            raise DBError("No PairDesk computer answers at %s port %d. Check the address, that remote access "
                          "is on there, and that a firewall allows TCP port %d." % (host, port, port))
        hk = " ".join(keys[0].split()[:2])
    tmp_kh = os.path.join(run_dir(), "pair_known_hosts")
    with open(tmp_kh, "w") as f:
        f.write("%s %s\n" % (alias, hk))
    pk = knock_key(host, port)
    cmd = ["ssh", "-p", str(port), "-i", pk, "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + tmp_kh,
           "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "%s@%s" % (user, host), command]
    try:
        r = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    finally:
        if os.path.exists(tmp_kh):
            os.unlink(tmp_kh)
    out = r.stdout.strip().splitlines()
    try:
        rep = json.loads(out[-1]) if out else {}
    except ValueError:
        rep = {}
    if "error" in rep:
        raise DBError(rep["error"])
    if r.returncode != 0 or not rep:
        err = r.stderr.strip().splitlines()
        if any("Permission denied" in e for e in err):
            if found.get("state") == "pairdesk" and user != found.get("user"):
                raise DBError("The user name in the address isn't %s's (%s)." % (found.get("name", host),
                                                                               found.get("user")))
            raise DBError("%s doesn't accept pairing right now: press \"Pair a new computer\" there (it may "
                          "be set to accept pairing only then)." % found.get("name", host))
        raise DBError("Pairing failed: " + (err[-1] if err else "no reply"))
    return rep, hk


def client_request_pairing(address, log=print):
    """Ask the remote computer to show a pairing code on its screen."""
    rep, _ = _pair_call(address, "request", {"name": hostname(), "user": getpass_user()})
    name = rep.get("name", address)
    if rep.get("headless"):
        user, host, _port = parse_address(address)
        log("%s has no screen to show the code on. Read it over SSH:  ssh %s@%s pairdesk pairing"
            % (name, user, host))
    else:
        log("A pairing code is now showing on %s." % name)
    return name, bool(rep.get("headless"))


def client_pair_code(address, code, log=print, host_name=None):
    """Client side: pair using the address and one-time code shown on the remote computer."""
    import hmac
    user, host, port = parse_address(address)
    typed = host if not is_ip(host) else (host_name or host)
    host = to_ip(host, port)
    if not host:
        raise DBError("No computer called %s was found. Use its IP address, e.g. 192.168.1.20." % typed)
    address = "%s@%s:%d" % (user, host, port)
    if len(_norm_code(code)) != 16:
        raise DBError("The code has 16 letters and digits, e.g. K7QM-4XTR-9PEH-2HVA.")
    alias = host if port == 22 else "[%s]:%d" % (host, port)
    log("Contacting %s ..." % alias)
    pubkey = ensure_key()
    # host key first (unauthenticated), then the proofs bind the code to exactly that key
    r = subprocess.run(["ssh-keyscan", "-T", "6", "-t", "ed25519", "-p", str(port), host],
                       capture_output=True, text=True, timeout=20)
    keys = [l.split(None, 1)[1] for l in r.stdout.splitlines() if l and not l.startswith("#")]
    if not keys:
        raise DBError("No PairDesk computer answers at %s port %d. Check the address, that remote access "
                      "is on there, and that a firewall allows TCP port %d." % (host, port, port))
    hk = " ".join(keys[0].split()[:2])
    req = {"name": hostname(), "user": getpass_user(), "pubkey": pubkey,
           "proof": _proof(code, "client", hk, pubkey)}
    try:
        rep, _ = _pair_call(address, "pair", req, hk=hk)
    except DBError as e:
        if "doesn't accept pairing" in str(e):
            raise DBError("%s isn't waiting for a pairing (the code may have expired). Press \"Show the code "
                          "again\", or \"Pair a new computer\" on %s." % (host, host))
        raise
    # the remote computer must prove it knows the same code for the same host key: stops anyone in between
    if not hmac.compare_digest(_proof(code, "server", hk, pubkey), rep.get("proof", "")):
        raise DBError("The computer at %s couldn't prove it knows the code. Someone may be in between; "
                      "pairing was stopped." % host)
    peer = safe_name(rep["name"])
    kh_set(alias, hk)
    c = load_config()
    old = c["peers"].get(peer, {})
    c["peers"][peer] = {
        "host": host, "user": rep.get("user", user), "port": port, "slot": rep["slot"], "rusb": rep["rusb"],
        "rsock": rep["rsock"], "link": True, "rdp_user": old.get("rdp_user", rep.get("user", user)),
        "opts": old.get("opts", dict(DEFAULT_OPTS, **(c.get("default_opts") or {}))),
        "usb_send": old.get("usb_send", []), "usb_take": old.get("usb_take", []),
    }
    if typed != host:
        c["peers"][peer]["host_name"] = typed      # to find it again if its IP address changes
    save_config(c)
    forget_knock_key(host, port)          # used for this pairing only
    log("Paired with %s." % peer)
    return peer


def _alias(host, port):
    return host if int(port) == 22 else "[%s]:%d" % (host, int(port))


def kh_get(alias):
    try:
        with open(known_hosts()) as f:
            for line in f:
                a, _, key = line.strip().partition(" ")
                if a == alias:
                    return key
    except OSError:
        pass
    return None


def move_peer(name, ip, log=print):
    """Remember a paired computer at a new IP address, but only if the computer there proves it is the
    same one (the same link key as at pairing). Returns True when the address was changed."""
    c = load_config()
    p = c["peers"].get(name)
    if not p or not ip or ip == p["host"]:
        return False
    port = int(p.get("port", LINK_PORT))
    key = kh_get(_alias(p["host"], port))
    try:
        r = subprocess.run(["ssh-keyscan", "-T", "4", "-t", "ed25519", "-p", str(port), ip],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    keys = [" ".join(l.split()[1:3]) for l in r.stdout.splitlines() if l and not l.startswith("#")]
    if not key or not keys or keys[0] != key:
        flog("[%s] not moved to %s: a different computer answers there" % (name, ip))
        return False
    kh_set(_alias(ip, port), key)
    if not is_ip(p["host"]):
        p.setdefault("host_name", p["host"])
    old, p["host"] = p["host"], ip
    save_config(c)
    log("%s is now at %s (was %s)." % (name, ip, old))
    return True


def locate_peer(name, log=print):
    """Before connecting: keep a paired computer's address a current IP. A name is turned into its IP;
    an IP that no longer answers (the router gave it a new one) is found again by name or on the
    network. Returns True when the address changed."""
    p = load_config()["peers"].get(name)
    if not p:
        return False
    host, port = p["host"], int(p.get("port", LINK_PORT))
    if not is_ip(host):
        return move_peer(name, to_ip(host, port), log)
    if reachable(host, port, 1.5):
        return False
    cands = []
    if p.get("host_name"):
        cands.append(to_ip(p["host_name"], port))
    try:
        for r in find_computers():
            if safe_name(r.get("name", "")) == name:
                cands += [r["host"]] + r.get("also", [])
    except Exception:      # noqa: BLE001 - finding it again is a bonus; connecting reports the real error
        pass
    for ip in cands:
        if ip and ip != host and move_peer(name, ip, log):
            return True
    return False


def forget_peer(name, outgoing=True):
    c = load_config()
    p = (c["peers"] if outgoing else c["incoming"]).pop(name, None)
    save_config(c)
    if not outgoing:
        ak_set(name, None)
    elif p and p.get("host"):
        port = int(p.get("port", 22))
        alias = p["host"] if port == 22 else "[%s]:%d" % (p["host"], port)
        _edit_lines(known_hosts(), lambda l: l.split(" ", 1)[0] != alias)


def paired_computers():
    """[{name, out, in}] - out: the current computer connects to it; in: it connects here."""
    c = load_config()
    names = sorted(set(c["peers"]) | set(c["incoming"]))
    return [{"name": n, "out": c["peers"].get(n), "in": c["incoming"].get(n)} for n in names]


def pairing_status(name):
    """'ok', 'revoked' (the remote computer removed this pairing) or 'unreachable'."""
    p = load_config()["peers"].get(name)
    if not p or not p.get("link"):
        return "unreachable"
    cmd = ["ssh", "-p", str(p.get("port", LINK_PORT)), "-i", key_path(), "-o", "IdentitiesOnly=yes",
           "-o", "UserKnownHostsFile=" + known_hosts(), "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=4", "%s@%s" % (p["user"], p["host"]), "pairdesk ping"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return "unreachable"
    if "Permission denied" in r.stderr:
        return "revoked"
    if "no longer paired" in r.stdout:
        return "revoked"
    return "ok" if r.returncode == 0 or "not accepting" in r.stdout else "unreachable"


def cleanup_revoked():
    """Remove computers that removed their pairing with the current computer. Returns their names."""
    gone = []
    for name, p in list(load_config()["peers"].items()):
        if p.get("link") and pairing_status(name) == "revoked":
            forget_peer(name, outgoing=True)
            gone.append(name)
    return gone


def unpair(name, both=True, log=print):
    """Remove a pairing in both directions. With both=True the remote computer is told to
    forget the current computer too (needs a working connection to it). Returns
    (remote_done, note)."""
    c = load_config()
    out = c["peers"].get(name)
    remote_done, note = False, ""
    if both and out and out.get("link") and not out.get("needs_pairing"):
        try:
            s = Session(name, log=lambda m: None)
            try:
                s.open()
                s.call("unpair")
                remote_done = True
            finally:
                s._close_master()
                s.fs.stop()
        except DBError as e:
            note = ("%s couldn't be reached (%s). It was removed here; to remove the current computer there "
                    "too, unpair it on %s." % (name, str(e).split(": ", 1)[-1], name))
    elif both and c["incoming"].get(name) and not out:
        note = ("%s can no longer connect. It removes the current computer from its list by itself the next "
                "time PairDesk starts or tries to connect there." % name)
    if name in c["peers"]:
        forget_peer(name, outgoing=True)
    if name in load_config()["incoming"]:
        forget_peer(name, outgoing=False)
    log("Unpaired %s%s." % (name, " on both computers" if remote_done else ""))
    return remote_done, note


# ---------------------------------------------------------------- folder sharing back
class FolderServer:
    """Serves the current computer's shared folder to one remote computer over a private
    socket (owner-only) that the link forwards; no SSH server is needed on this side."""

    def __init__(self, name):
        self.path = os.path.join(run_dir(), "fs-%s.sock" % safe_name(name))
        self.sock = None

    def start(self):
        if self.sock:
            return self.path
        sftp = next((s for s in SFTP_SERVERS if os.path.exists(s)), None)
        if not sftp:
            raise DBError("Sharing the current folder needs the file-sharing helper (openssh-sftp-server).")
        if os.path.exists(self.path):
            os.unlink(self.path)
        old = os.umask(0o177)
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(self.path)
        finally:
            os.umask(old)
        self.sock.listen(4)
        threading.Thread(target=self._loop, args=(sftp,), daemon=True).start()
        return self.path

    def _loop(self, sftp):
        while self.sock:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            c = load_config()
            args = [sftp, "-d", share_dir(c)] + (["-R"] if c.get("share_readonly") else [])
            subprocess.Popen(args, stdin=conn.fileno(), stdout=conn.fileno(), stderr=subprocess.DEVNULL)
            conn.close()

    def stop(self):
        s, self.sock = self.sock, None
        if s:
            s.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


# ---------------------------------------------------------------- session
class Session:
    """One live connection to a paired peer. Reconnects by itself after a drop
    and puts USB devices and folders back; the desktop window reconnects too,
    because the local tunnel ports stay the same."""

    def __init__(self, name, log=print, on_state=None):
        c = load_config()
        if name not in c["peers"]:
            raise DBError("Unknown computer: %s" % name)
        if c["peers"][name].get("needs_pairing") or not c["peers"][name].get("link"):
            raise DBError("%s was paired with an older PairDesk. Pair it again: on %s press \"Pair a new "
                          "computer\", then here \"Add remote computer…\" with the code." % (name, name))
        self.name, self.p, self.cfg = name, c["peers"][name], c
        self.fs = FolderServer(name)
        self._ui_log = log
        self.on_state = on_state or (lambda st: None)
        self.state = "idle"        # connecting | connected | reconnecting | closed
        self.ctl = os.path.join(run_dir(), "c-" + safe_name(name))
        self.master = None
        self.rdp = None
        self.lrdp = self.lusb = None
        self.sent = {}      # my busid -> dev       (my devices attached on peer)
        self.usb_failed = {}  # busid -> why; not retried until the switch is flipped again or it's replugged
        self.camera = None    # {"dev", "name", "procs"} while a camera is sent as video
        self.audio_module = None   # echo cancelling (pactl module) while the desktop is open
        self.embed_host = None   # set by the app: () -> (xid, width, height) to show the desktop in its window
        self.taken = {}     # their busid -> dev    (peer devices attached here)
        self.mounted_theirs = None
        self.shared_mine = False
        self.opts = dict(DEFAULT_OPTS)
        self.closing = False
        self.info = {}
        self.lock = threading.RLock()

    def log(self, msg):
        flog("[%s] %s" % (self.name, msg))
        self._ui_log(msg)

    def _set(self, st):
        self.state = st
        self.on_state(st)

    # -- ssh
    def _ssh_base(self):
        return ["ssh", "-p", str(self.p.get("port", 22)), "-i", key_path(), "-o", "IdentitiesOnly=yes",
                "-o", "UserKnownHostsFile=" + known_hosts(), "-o", "StrictHostKeyChecking=yes",
                "-o", "BatchMode=yes", "-o", "ControlPath=" + self.ctl, "-o", "ConnectTimeout=8"]

    def dest(self):
        return "%s@%s" % (self.p["user"], self.p["host"])

    def alive(self):
        return self.master is not None and self.master.poll() is None

    def wake(self):
        """Wake the peer with Wake-on-LAN if it does not answer."""
        port = self.p.get("port", 22)
        if reachable(self.p["host"], port) or not self.p.get("wol"):
            return
        self.log("%s is not answering; sending Wake-on-LAN ..." % self.name)
        wake_on_lan(self.p["wol"])
        for i in range(45):
            if self.closing:
                return
            if reachable(self.p["host"], port, 2):
                self.log("%s is awake." % self.name)
                time.sleep(2)     # let its services finish starting
                return
            if i % 10 == 9:
                wake_on_lan(self.p["wol"])
            time.sleep(2)          # a booting machine refuses at once; pace the retries (~90 s)
        self.log("%s did not wake up (is Wake-on-LAN on in its BIOS?)." % self.name)

    def open(self):
        if self.lrdp is None:   # same ports for the whole session, so the desktop can reconnect
            self.lrdp, self.lusb = free_port(), free_port()
        cmd = self._ssh_base() + ssh_speed_opts() + [
            "-o", "ControlMaster=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3", "-o", "TCPKeepAlive=yes", "-N",
            "-L", "127.0.0.1:%d:127.0.0.1:3389" % self.lrdp,
            "-L", "127.0.0.1:%d:127.0.0.1:3240" % self.lusb,
            "-R", "127.0.0.1:%d:127.0.0.1:3240" % self.p["rusb"]]
        cmd.append(self.dest())
        for attempt in range(5):
            err = self._start_master(cmd)
            if err is None:
                break
            # the server may still be releasing ports from a session that just ended
            if "port forwarding failed" in err and attempt < 4:
                time.sleep(1.5 if attempt < 2 else 8)      # up to ~20 s: its link drops a dead one within 30 s
                continue
            if "port forwarding failed" in err:
                raise DBError("%s still has an earlier connection from the current computer open. Wait half a "
                              "minute and connect again; if it keeps happening, turn remote access off and on "
                              "there (or restart it)." % self.name)
            if "Permission denied" in err:
                forget_peer(self.name, outgoing=True)     # it removed the pairing: follow it here
                raise DBError("%s removed its pairing with the current computer, so it was removed here too. "
                              "Pair again to connect." % self.name)
            raise DBError("Could not connect: " + err)
        try:
            info = self.call("info")
        except DBError:
            self._close_master()     # refused (e.g. remote access off there): don't leave the link open
            raise
        self.info = info
        self._share_back(info.get("rsock") or self.p.get("rsock"))
        # remember how to wake it, which desktop login it uses (for the password prompt) and where it
        # expects the current computer's shared folder (it says so itself, so a moved settings folder works)
        keep = {"wol": info.get("wol") or self.p.get("wol"), "rdp_backend": info.get("rdp_backend"),
                "rsock": info.get("rsock") or self.p.get("rsock")}
        c = load_config()
        if self.name in c["peers"] and any(c["peers"][self.name].get(k) != v for k, v in keep.items()):
            c["peers"][self.name].update(keep)
            save_config(c)
        self.p.update(keep)
        return info

    def _share_back(self, rsock):
        """Private socket for sharing the current folder back through the link. Added once the link is up,
        at the path the remote computer reports, so a problem here never stops the connection."""
        if not rsock:
            return
        try:
            local = self.fs.start()
        except DBError:
            return       # nothing to serve (file-sharing helper missing)
        r = subprocess.run(self._ssh_base() + ["-O", "forward", "-R", "%s:%s" % (rsock, local), self.dest()],
                           capture_output=True, text=True)
        if r.returncode != 0:
            flog("Folder sharing back to %s not available: %s" % (self.name, r.stderr.strip()))

    def _close_master(self):
        m = self.master
        if m is not None:
            subprocess.run(self._ssh_base() + ["-O", "exit", self.dest()], capture_output=True)
            try:
                m.wait(5)
            except subprocess.TimeoutExpired:
                m.kill()
            self.master = None

    def _end_earlier_links(self):
        """An earlier connection to this computer from the current one (another PairDesk window, or one
        left from before an update) holds its ports there: end it properly before connecting again."""
        if os.path.exists(self.ctl):
            subprocess.run(self._ssh_base() + ["-O", "exit", self.dest()], capture_output=True, timeout=10)
            for _ in range(20):
                if not os.path.exists(self.ctl):
                    break
                time.sleep(0.1)
            if os.path.exists(self.ctl):
                os.unlink(self.ctl)
        # one whose control socket was already removed can't be asked: end it by its exact command line
        me = os.getuid()
        mine = {self.master.pid} if self.master is not None else set()
        for d in os.listdir("/proc"):
            if not d.isdigit() or int(d) in mine:
                continue
            try:
                if os.stat("/proc/" + d).st_uid != me:
                    continue
                with open("/proc/%s/cmdline" % d, "rb") as f:
                    args = f.read().split(b"\0")
            except OSError:
                continue
            if args and os.path.basename(args[0]) == b"ssh" and b"-N" in args and \
                    ("ControlPath=" + self.ctl).encode() in args:
                try:
                    os.kill(int(d), 15)
                    flog("[%s] ended an earlier connection (process %s)" % (self.name, d))
                except OSError:
                    pass

    def _owner_path(self):
        return os.path.join(run_dir(), "owner-" + safe_name(self.name))

    def taken_over_by(self):
        """The process of another PairDesk window that connected to this computer after this one, or None."""
        try:
            pid = int(open(self._owner_path()).read())
            if pid != os.getpid():
                os.kill(pid, 0)
                return pid
        except (OSError, ValueError):
            pass
        return None

    def _start_master(self, cmd):
        """Start the SSH link. Returns None when up, else the error text."""
        self._end_earlier_links()
        with open(self._owner_path(), "w") as f:     # the newest connection wins; older windows stand down
            f.write(str(os.getpid()))
        self.master = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True)
        for _ in range(120):
            if self.master.poll() is not None:
                break
            if os.path.exists(self.ctl) and subprocess.run(
                    self._ssh_base() + ["-O", "check", self.dest()], capture_output=True).returncode == 0:
                time.sleep(0.4)    # let forward failures (ExitOnForwardFailure) surface
                if self.master.poll() is None:
                    return None
                break
            time.sleep(0.1)
        if self.master.poll() is None:
            self.master.kill()
            return "timed out connecting to %s" % self.p["host"]
        err = self.master.stderr.read().strip().splitlines()
        return err[-1] if err else "ssh exited"

    def call(self, *args):
        try:
            r = subprocess.run(self._ssh_base() + [self.dest(), "pairdesk " + shlex.join([str(a) for a in args])],
                               capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            raise DBError("%s did not answer in time" % self.name)
        out = r.stdout.strip()
        try:
            rep = json.loads(out.splitlines()[-1]) if out else {}
        except ValueError:
            rep = {"error": out or r.stderr.strip()}
        if r.returncode != 0 and "error" not in rep:
            rep = {"error": r.stderr.strip() or "remote command failed"}
        if "error" in rep:
            raise DBError("%s: %s" % (self.name, rep["error"]))
        return rep

    # -- updates
    TOO_OLD = ("%s's PairDesk is too old to exchange updates over PairDesk. Update it once from its own screen, "
               "or over SSH (the + button's Set it up over SSH); after that, either computer can update the other.")

    def _update_error(self, out, err):
        try:
            msg = json.loads(out.strip().splitlines()[-1]).get("error", "")
        except (ValueError, IndexError, AttributeError):
            msg = err.strip() or out.strip() or "no reply"
        if "unknown command" in msg:
            raise DBError(self.TOO_OLD % self.name)
        raise DBError("%s: %s" % (self.name, msg))

    def fetch_package(self):
        """Get the newer PairDesk package from the other computer. Returns (path, version)."""
        d = updates_dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                r = subprocess.run(self._ssh_base() + [self.dest(), "pairdesk package-get"], stdin=subprocess.DEVNULL,
                                   stdout=f, stderr=subprocess.PIPE, timeout=600)
        except subprocess.TimeoutExpired:
            os.unlink(tmp)
            raise DBError("%s didn't send the update in time" % self.name)
        if r.returncode != 0 or os.path.getsize(tmp) > UPDATE_MAX:
            with open(tmp, "rb") as f:
                out = f.read(4096).decode("utf-8", "replace")
            os.unlink(tmp)
            self._update_error(out, r.stderr.decode("utf-8", "replace"))
        return store_package(tmp)

    def push_package(self, path):
        """Send this computer's PairDesk package to the other one, where an administrator is asked
        to install it. Returns its reply: state 'offered' (asked on its screen) or 'saved'."""
        try:
            with open(path, "rb") as f:
                r = subprocess.run(self._ssh_base() + [self.dest(), "pairdesk package-put"], stdin=f,
                                   capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise DBError("%s didn't take the update in time" % self.name)
        out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
        try:
            rep = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            rep = {}
        if r.returncode != 0 or "error" in rep or not rep:
            self._update_error(out, err)
        return rep

    # -- camera as video, me -> peer
    def send_camera(self, cam):
        """Send a camera of the current computer as compressed video; on the other computer it shows up as
        a camera called "PairDesk camera"."""
        with self.lock:
            self.stop_camera()
            if not feature_present("camera-send"):
                raise DBError("NEEDS:camera-send")
            if not (self.info.get("features") or {}).get("camera-receive"):
                raise DBError("NEEDS-REMOTE:camera-receive")
            self.call("camera-start")             # loads the virtual camera there (its USB setup's admin rule)
            cap = subprocess.Popen(camera_capture_cmd(cam["dev"]), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out = subprocess.Popen(self._ssh_base() + [self.dest(), "pairdesk camera-in"], stdin=cap.stdout,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cap.stdout.close()
            time.sleep(2.5)                       # a camera that can't start fails at once
            if cap.poll() is not None or out.poll() is not None:
                err = (cap.stderr.read() if cap.poll() is not None else b"") + (out.stdout.read() + out.stderr.read()
                                                                               if out.poll() is not None else b"")
                for p in (cap, out):
                    if p.poll() is None:
                        p.kill()
                msg = err.decode("utf-8", "replace").strip().splitlines()
                try:
                    msg = [json.loads(msg[-1]).get("error", msg[-1])]
                except (ValueError, IndexError):
                    pass
                raise DBError("The camera couldn't be sent: %s" % (msg[-1] if msg else "it stopped at once"))
            self.camera = dict(cam, procs=[cap, out])
            self.log("%s is now a camera on %s (\"%s\")." % (cam["name"], self.name, CAMERA_LABEL))

    def stop_camera(self):
        cam, self.camera = self.camera, None
        if cam:
            for p in cam["procs"]:
                if p.poll() is None:
                    p.terminate()
            for p in cam["procs"]:
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.log("Stopped sending %s." % cam["name"])
        return cam

    def camera_alive(self):
        return bool(self.camera) and all(p.poll() is None for p in self.camera["procs"])

    # -- USB, me -> peer
    def send_usb(self, dev):
        with self.lock:
            if self.closing:
                return          # disconnecting: devices are being taken back, not sent
            if not port_listening(3240):
                why = ("The USB service of the current computer (%s) isn't running, so %s can't use its devices. "
                       "Settings > Set up USB sharing turns it on (once)." % (hostname(), self.name))
                self.usb_failed[dev["busid"]] = why
                raise DBError(why)
            try:
                helper("usb-bind", dev["busid"])
                self.call("usb-import", self.p["rusb"], dev["busid"])
            except DBError as e:
                try:
                    helper("usb-unbind", dev["busid"])
                except DBError:
                    pass
                why = str(e)
                if "Attach Request" in why or "Op Common Status" in why:
                    why = ("%s couldn't take over %s: the current computer didn't hand it over. Unplug it and "
                           "plug it back in, then flip its switch again. If it keeps happening, run Settings > "
                           "Set up USB sharing again on the current computer." % (self.name, dev["name"]))
                self.usb_failed[dev["busid"]] = why
                raise DBError(why)
            self.usb_failed.pop(dev["busid"], None)
            self.sent[dev["busid"]] = dev
            self.log("%s is now on remote %s." % (dev["name"], self.name))

    def recall_usb(self, busid):
        with self.lock:
            try:
                self.call("usb-release", self.p["rusb"], busid)
            except DBError as e:
                self.log(str(e))
            helper("usb-unbind", busid)
            self.sent.pop(busid, None)

    # -- USB, peer -> me
    def remote_usb(self):
        return self.call("usb-list").get("devices", [])

    def take_usb(self, dev):
        with self.lock:
            if self.closing:
                return
            self.call("usb-export", dev["busid"])
            try:
                helper("usb-attach", self.lusb, dev["busid"])
            except DBError:
                self.call("usb-unexport", dev["busid"])
                raise
            self.taken[dev["busid"]] = dev
            self.log("%s from %s is now on the current computer." % (dev["name"], self.name))

    def return_usb(self, busid):
        with self.lock:
            try:
                helper("usb-detach-from", self.lusb, busid)
            except DBError as e:
                self.log(str(e))
            try:
                self.call("usb-unexport", busid)
            except DBError as e:
                self.log(str(e))
            self.taken.pop(busid, None)

    # -- folders
    def mount_theirs(self):
        target = os.path.join(mount_root(), self.name)
        os.makedirs(target, exist_ok=True)
        unmount(target)
        if not shutil.which("sshfs"):
            raise DBError("Folder sharing (sshfs) is not installed. Install it with: pairdesk install folders")
        cmd = ["sshfs", "-p", str(self.p.get("port", 22))] + ssh_speed_opts(single=True) + [
               "-o", "IdentityFile=" + key_path(),
               "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + known_hosts(),
               "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "-o", "ControlPath=" + self.ctl,
               "-o", "reconnect", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
               "-o", "dir_cache=yes", "-o", "kernel_cache", "-o", "sftp_server=" + SFTP_MARKER,
               self.dest() + ":", target]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise DBError("Could not open %s's folder: %s" % (self.name, r.stderr.strip()))
        self.mounted_theirs = target
        self.log("Remote shared folder of %s: %s" % (self.name, target))

    def share_mine(self):
        if not self.fs.sock:
            self.fs.start()     # raises with the reason (file-sharing helper missing)
        try:
            rep = self.call("mount-peer")
        except DBError as e:
            if "needs-feature:folders" in str(e):
                raise DBError("To open the current folder on %s, %s needs Folder sharing (sshfs). Open "
                              "PairDesk on %s: it will offer to install it." % (self.name, self.name, self.name))
            raise
        self.shared_mine = True
        self.log("Current shared folder is open on %s at %s" % (self.name, rep.get("path")))

    # -- desktop
    def direct_target(self, ver):
        """(host, fingerprint) for a direct desktop stream, or None to use the link.
        The fingerprint comes over the authenticated link, so only the real desktop
        server is accepted; the stream itself is protected by the desktop server's TLS."""
        if ver < 3:
            self.log("Direct desktop needs FreeRDP 3; using the encrypted link.")
            return None
        try:
            fp = self.call("rdp-fingerprint").get("sha256")
        except DBError as e:
            self.log("Direct desktop not possible (%s); using the encrypted link." % e)
            return None
        if not fp or not reachable(self.p["host"], 3389, 2):
            self.log("%s's desktop port isn't reachable directly (its \"Only allow the desktop through "
                     "PairDesk\" option, or a firewall); using the encrypted link." % self.name)
            return None
        return self.p["host"], fp

    def rdp_cmd(self, password, opts, direct=None, embed=None):
        binp, ver = rdp_client()
        if direct:
            # deny = refuse at once (no question) unless the certificate matches the pin
            target = ["/v:%s:3389" % direct[0], "/cert:deny,fingerprint:sha256:%s" % direct[1]]
        else:   # through the link: the link already authenticated the computer
            target = ["/v:127.0.0.1:%d" % self.lrdp, "/cert:ignore" if ver == 3 else "/cert-ignore"]
        args = target + ["/u:" + self.p.get("rdp_user", self.p["user"]),
                         "/p:" + password, "/t:PairDesk - " + self.name, "/dynamic-resolution",
                         "+auto-reconnect", "/auto-reconnect-max-retries:60", "/gfx",
                         ]
        if embed:      # inside PairDesk's window: its toolbar does full screen, USB, camera, ...
            args += ["/parent-window:%d" % embed[0], "/size:%dx%d" % (embed[1], embed[2]), "-toggle-fullscreen"]
        else:          # FreeRDP's own window, with its bar: minimize, full screen on/off, close
            args.append("/floatbar:sticky:off,default:visible,show:always")
        args += QUALITY.get(opts.get("quality", "auto"), QUALITY["auto"])
        if opts.get("fullscreen") and not embed:
            args.append("/f")
        if opts.get("multimon") and not embed:
            args.append("/multimon")
        args.append("+clipboard" if opts.get("clipboard") else "-clipboard")
        args += audio_args(opts, clean=bool(getattr(self, "audio_module", None)))
        if opts.get("printers"):
            args.append("/printer")
        return binp, ver, args

    def clean_audio_on(self, opts=None):
        """Echo cancelling and noise reduction for the microphone sent to the remote computer (PipeWire's or
        PulseAudio's WebRTC echo canceller): its sound plays through it, so your speakers aren't heard back
        in the call, and the room's echo and noise are reduced. Returns True when it's on."""
        if self.audio_module or not shutil.which("pactl"):
            return bool(self.audio_module)
        try:
            r = subprocess.run(["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc",
                                "source_name=pairdesk_mic", "sink_name=pairdesk_speaker",
                                "source_properties=device.description=PairDesk-microphone",
                                "sink_properties=device.description=PairDesk-speaker",
                                "aec_args=noise_suppression=true high_pass_filter=true voice_detection=true "
                                "extended_filter=true"] +
                               (["source_master=" + opts["mic_device"]] if (opts or {}).get("mic_device") else []) +
                               (["sink_master=" + opts["speaker_device"]] if (opts or {}).get("speaker_device") else []),
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode != 0 or not r.stdout.strip().isdigit():
            flog("[%s] echo cancelling not available: %s" % (self.name, (r.stderr or r.stdout).strip()))
            return False
        self.audio_module = r.stdout.strip()
        return True

    def clean_audio_off(self):
        m, self.audio_module = self.audio_module, None
        if m:
            subprocess.run(["pactl", "unload-module", m], capture_output=True, timeout=15)

    def start_desktop(self, password, opts):
        self.clean_audio_off()
        if opts.get("clean_mic") and opts.get("microphone"):
            if self.clean_audio_on(opts):
                self.log("Microphone: echo and noise reduction on.")
        self.desktop_password = password            # kept in memory only, to reopen the view after changes
        direct = self.direct_target(rdp_client()[1]) if opts.get("direct_desktop") else None
        embed = None
        if self.embed_host and not opts.get("multimon") and "xfreerdp" in os.path.basename(rdp_client()[0]):
            try:
                embed = self.embed_host()
            except Exception as e:  # noqa: BLE001 - FreeRDP's own window still works
                flog("[%s] desktop window not available: %s" % (self.name, e))
        self.embedded = bool(embed)
        binp, ver, args = self.rdp_cmd(password, opts, direct, embed)
        if direct:
            self.log("Desktop stream goes directly to %s (certificate pinned)." % direct[0])
        if ver == 3:   # keep the password off the process list
            self.rdp = subprocess.Popen([binp, "/args-from:stdin"], stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.rdp.stdin.write("\n".join(args) + "\n")
            self.rdp.stdin.close()
        else:
            self.rdp = subprocess.Popen([binp] + args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.log("Opening %s's desktop ..." % self.name if embed else
                 "Opening %s's desktop (Ctrl+Alt+Enter leaves full screen) ..." % self.name)
        return self.rdp

    # -- whole session
    def connect(self, opts=None, rdp_password=None, on_desktop_exit=None, on_ready=None):
        """Connect. The desktop opens first; folders and USB follow while it starts.
        on_ready(info) is called as soon as the link is up."""
        self.opts = dict(DEFAULT_OPTS, **(opts or self.p.get("opts", {})))
        self.closing = False
        self._set("connecting")
        if locate_peer(self.name, log=self.log):
            self.p = load_config()["peers"][self.name]
        self.log("Connecting to %s ..." % self.p["host"])
        self.wake()
        info = self.open()
        self.log("Connected to %s (%s)." % (info.get("name"), info.get("rdp_backend") or "no desktop server"))
        if self.on_update and self.on_update(self, info):   # the other computer was updated: its link restarted
            for _ in range(15):
                try:
                    info = self.call("info") if self.alive() else self.open()
                    break
                except DBError:
                    time.sleep(2)
            self.info = info
        if self.opts.get("desktop") and info.get("rdp_conflict") == "signed_in" and self.on_conflict \
                and rdp_password is not None and self.on_conflict(self.name):
            if self.sign_out_remote():
                info = self.call("info")
                self.info = info
        if self.opts.get("desktop") and info.get("rdp_warning"):
            self.log(info["rdp_warning"])        # opening it would only show the desktop server's error
        elif self.opts.get("desktop"):
            if not info.get("rdp_listening"):
                self.log("%s has no desktop server running; turn on Server mode there." % self.name)
            elif rdp_password is None:
                self.log("Desktop skipped: no password given.")
            else:
                self.start_desktop(rdp_password, self.opts)
                if on_desktop_exit:
                    threading.Thread(target=self._watch_rdp, args=(on_desktop_exit,), daemon=True).start()
        self._set("connected")
        if on_ready:
            on_ready(info)
        self._restore(initial=True)
        threading.Thread(target=self._watchdog, daemon=True).start()
        return info

    taken_over = False    # another PairDesk window took over this connection
    signout_refused = False   # someone at the other computer pressed Cancel in the sign-out notice
    on_update = None      # set by the app: offer an update before signing in; True if the peer was updated
    on_conflict = None    # set by the app: ask whether to sign the remote's local session out

    def sign_out_remote(self, seconds=30):
        """Ask the remote computer to sign its local session out after a visible countdown."""
        rep = self.call("logout-local", seconds)
        if rep.get("state") == "none":
            return True
        self.log("%s shows a %d-second notice on its screen before signing out the local session ..."
                 % (self.name, seconds))
        for _ in range(seconds + 60):
            time.sleep(1)
            st = self.call("logout-status").get("state")
            if st == "cancelled":
                self.signout_refused = True
                self.log("Someone at %s pressed Cancel: the session there stays signed in." % self.name)
                return False
            if st == "done":
                self.log("The local session on %s is signed out." % self.name)
                time.sleep(2)
                return True
            if st == "failed":
                self.log("%s couldn't sign the local session out." % self.name)
                return False
            if st in ("signing-out", "done", "failed", "unknown") or _ % 5 == 4:
                # the screen's session may already be gone even if no "done" came back: ask the computer itself
                try:
                    if self.call("info").get("rdp_conflict") != "signed_in":
                        self.log("The local session on %s is signed out." % self.name)
                        time.sleep(1.5)          # let the desktop server finish with the old session
                        return True
                except DBError:
                    pass
        return False

    def _restore(self, initial):
        """Folders and USB devices: first time from settings, after a reconnect from state."""
        jobs = []
        if self.opts.get("share_mine"):
            jobs.append(self.share_mine)
        if self.opts.get("open_theirs"):
            jobs.append(self.mount_theirs)
        # folders in parallel with USB: each is its own SSH round trip
        th = [threading.Thread(target=self._safe, args=(j,), daemon=True) for j in jobs]
        for t in th:
            t.start()
        usb_here = setup_done() and port_listening(3240)
        usb_there = bool(self.info.get("usb_ready", True))
        if not (usb_here and usb_there) and (self.p.get("usb_send") or self.p.get("usb_take")):
            self.log("USB devices skipped: USB sharing isn't set up on %s."
                     % ("the current computer" if not usb_here else self.name))
        if not (usb_here and usb_there):
            for t in th:
                t.join(40)
            return
        if initial:
            mine = {d["key"]: d for d in list_local_usb()}
            send = [mine[k] for k in self.p.get("usb_send", []) if k in mine and mine[k]["busid"] not in self.usb_failed]
        else:
            fresh = {d["busid"]: d for d in list_local_usb()}
            send = [fresh[b] for b in list(self.sent) if b in fresh]
            self.sent.clear()
        for d in send:
            self._safe(self.send_usb, d)
        if initial:
            take = []
            if self.p.get("usb_take"):
                try:
                    theirs = {d["key"]: d for d in self.remote_usb()}
                    take = [theirs[k] for k in self.p["usb_take"] if k in theirs]
                except DBError as e:
                    self.log(str(e))
        else:
            take = list(self.taken.values())
            self.taken.clear()
        for d in take:
            self._safe(self.take_usb, d)
        if not initial and self.camera and not self.camera_alive():   # the link dropped: send it again
            cam = dict(self.camera)
            self.camera = None
            self._safe(self.send_camera, cam)
        for t in th:
            t.join(40)

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except DBError as e:
            self.log(str(e))

    def _watchdog(self):
        while not self.closing:
            time.sleep(2)
            if self.closing or self.alive():
                continue
            if self.taken_over_by():
                self.taken_over = True
                self.log("Another PairDesk window connected to %s, so this one stopped." % self.name)
                self.disconnect()
                return
            self._set("reconnecting")
            self.log("Connection to %s lost; reconnecting ..." % self.name)
            delay = 2.0
            while not self.closing:
                try:
                    self.wake()
                    self.open()
                    if self.closing:   # user disconnected meanwhile: drop this new link
                        subprocess.run(self._ssh_base() + ["-O", "exit", self.dest()], capture_output=True)
                        break
                    self._restore(initial=False)
                    self._set("connected")
                    self.log("Reconnected to %s." % self.name)
                    break
                except DBError as e:
                    flog("[%s] reconnect: %s" % (self.name, e))
                    if self.taken_over_by():
                        self.taken_over = True
                        self.disconnect()
                        return
                    time.sleep(delay)
                    delay = min(delay * 1.5, 15)

    def _watch_rdp(self, cb):
        self._on_desktop_exit = cb
        p = self.rdp
        p.wait()
        err = p.stderr.read() if p.stderr else ""
        if err:
            flog("[%s] desktop exit %s: %s" % (self.name, p.returncode, err[-600:]))
        if p is not self.rdp or getattr(self, "reopening", False):
            return                       # replaced by reopen_desktop(): the session goes on
        cb(p.returncode, err)

    def reopen_desktop(self, opts):
        """Apply changed sound or microphone choices: open the desktop view again (the remote session itself
        stays; it only takes a moment)."""
        pw = getattr(self, "desktop_password", None)
        if pw is None or not self.rdp:
            return False
        self.opts = dict(self.opts, **opts)
        self.reopening = True
        try:
            if self.rdp.poll() is None:
                self.rdp.terminate()
                try:
                    self.rdp.wait(10)
                except subprocess.TimeoutExpired:
                    self.rdp.kill()
            time.sleep(1)
            self.start_desktop(pw, self.opts)
        finally:
            self.reopening = False
        cb = getattr(self, "_on_desktop_exit", None)
        if cb:
            threading.Thread(target=self._watch_rdp, args=(cb,), daemon=True).start()
        return True

    def disconnect(self):
        with self.lock:
            if self.state == "closed" or getattr(self, "_disconnecting", False):
                return
            self._disconnecting = True
        try:
            self._disconnect()
        finally:
            self._disconnecting = False

    def _disconnect(self):
        self.closing = True
        self.stop_camera()
        self.clean_audio_off()
        self.log("Disconnecting ...")
        if self.rdp and self.rdp.poll() is None:
            self.rdp.terminate()
        with self.lock:
            if self.alive():
                for b in list(self.sent):
                    self.recall_usb(b)
                for b in list(self.taken):
                    self.return_usb(b)
                try:
                    self.call("umount-peer")
                except DBError:
                    pass
            else:   # link is down: clean up our side only
                for b in list(self.sent):
                    try:
                        helper("usb-unbind", b, interactive=False)
                    except DBError:
                        pass
                for b in list(self.taken):
                    try:
                        helper("usb-detach-from", self.lusb, b, interactive=False)
                    except DBError:
                        pass
                self.sent.clear()
                self.taken.clear()
        if self.mounted_theirs:
            unmount(self.mounted_theirs)
        m = self.master
        if m is not None:
            subprocess.run(self._ssh_base() + ["-O", "exit", self.dest()], capture_output=True)
            try:
                m.wait(5)
            except subprocess.TimeoutExpired:
                m.kill()
        self.master = None
        self.fs.stop()
        self._set("closed")
        self.log("Disconnected from %s." % self.name)


def rdp_fingerprint(host="127.0.0.1", port=3389, timeout=5):
    """SHA-256 fingerprint of an RDP server's TLS certificate (X.224 negotiation, then TLS)."""
    import hashlib
    import ssl
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        # TPKT + X.224 Connection Request + RDP_NEG_REQ (TLS | CredSSP)
        s.sendall(bytes.fromhex("030000130ee000000000000100080003000000"))
        hdr = s.recv(4)
        if len(hdr) < 4 or hdr[0] != 3:
            raise DBError("not an RDP server")
        rest = b""
        need = int.from_bytes(hdr[2:4], "big") - 4
        while len(rest) < need:
            chunk = s.recv(need - len(rest))
            if not chunk:
                break
            rest += chunk
        if len(rest) >= 15 and rest[7] == 3:     # RDP_NEG_FAILURE
            raise DBError("the desktop server refused TLS")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE        # only reading the certificate; trust comes from the link
        t = ctx.wrap_socket(s, server_hostname=None)
        der = t.getpeercert(binary_form=True)
        t.close()
    finally:
        try:
            s.close()
        except OSError:
            pass
    d = hashlib.sha256(der).hexdigest()
    return ":".join(d[i:i + 2] for i in range(0, len(d), 2))


def desktop_server_here():
    """What the current computer can use to share its desktop (no admin rights needed)."""
    return {"listening": port_listening(3389),
            "gnome": bool(shutil.which("grdctl")),
            "xrdp": os.path.exists("/usr/sbin/xrdp") or bool(shutil.which("xrdp"))}


DESKTOP_HELP = {
    "gnome": ("GNOME Remote Login lets remote computers log in to the desktop of the current computer. "
              "It uses its own user name and password, which you choose; remote computers type them "
              "when they connect.",
              ["Open Settings > System > Remote Desktop.",
               "Open the Remote Login tab and click Unlock (your admin password).",
               "Turn on Remote Login.",
               "Set a user name and password. Remote computers will type these.",
               "Back in PairDesk, open Current computer and press Refresh."]),
    "xrdp": ("xrdp lets remote computers log in to the current computer with its normal user name "
             "and password.",
             ["Open a terminal and run:  sudo apt install xrdp pipewire-module-xrdp",
              "(on older systems use pulseaudio-module-xrdp for sound)",
              "Back in PairDesk, switch Server off and on again, then press Refresh."]),
}
