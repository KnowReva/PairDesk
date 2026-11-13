import os
import sys
import io
import subprocess
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "files", "usr", "lib", "pairdesk"))
import dbcore as C  # noqa: E402


def mkdev(root, busid, vid, pid, prod, cls="00", ifclass="03", driver="usb", serial="", proto="00",
          desc=None):
    d = os.path.join(root, "real", busid)
    os.makedirs(os.path.join(d, busid + ":1.0"))
    for f, v in (("idVendor", vid), ("idProduct", pid), ("product", prod), ("manufacturer", "Acme"),
                 ("bDeviceClass", cls), ("serial", serial)):
        if v:
            open(os.path.join(d, f), "w").write(v + "\n")
    open(os.path.join(d, busid + ":1.0", "bInterfaceClass"), "w").write(ifclass + "\n")
    open(os.path.join(d, busid + ":1.0", "bInterfaceProtocol"), "w").write(proto + "\n")
    if desc is not None:
        hd = os.path.join(d, busid + ":1.0", "0003:%s:%s.0001" % (vid.upper(), pid.upper()))
        os.makedirs(hd)
        open(os.path.join(hd, "report_descriptor"), "wb").write(desc)
    os.makedirs(os.path.join(root, "drivers", driver), exist_ok=True)
    os.symlink(os.path.join(root, "drivers", driver), os.path.join(d, "driver"))
    os.symlink(d, os.path.join(root, "devices", busid))


class USB(unittest.TestCase):
    def test_list(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "devices"))
            mkdev(t, "1-2", "1050", "0407", "YubiKey OTP+FIDO+CCID", ifclass="03", serial="123")
            mkdev(t, "1-3", "046d", "085c", "C922 Pro Stream Webcam", ifclass="0e", driver="usbip-host")
            mkdev(t, "2-1", "05e3", "0610", "Hub", cls="09")
            mkdev(t, "1-4.1", "0781", "5583", "Ultra Fit", ifclass="08")
            mkdev(t, "3-1", "046d", "c31c", "Keyboard K120", ifclass="03", proto="01")
            mkdev(t, "3-2", "046d", "c077", "USB Optical Mouse", ifclass="03", proto="02")
            mkdev(t, "3-3", "32a3", "3201", "Token2 key", ifclass="03", proto="00",
                  desc=bytes.fromhex("06d0f1090 1a101 0920 1500 26ff00 7508 9540 8102 c0".replace(" ", "")))
            mkdev(t, "3-4", "04f3", "250e", "ELAN Touchscreen", ifclass="03", proto="00",
                  desc=bytes.fromhex("050d0904a101 0922a102 0942 1500 2501 c0c0".replace(" ", "")))
            mkdev(t, "3-5", "045e", "028e", "Controller", ifclass="03", proto="00",
                  desc=bytes.fromhex("05010905a101 0501 0930 c0".replace(" ", "")))
            mkdev(t, "3-6", "1d50", "60fc", "OnlyKey", ifclass="03", proto="01",
                  desc=bytes.fromhex("05010906a101 c0 06d0f10901a101 c0".replace(" ", "")))
            os.symlink("x", os.path.join(t, "devices", "usb1"))
            devs = {d["busid"]: d for d in C.list_local_usb(os.path.join(t, "devices"))}
            self.assertEqual(sorted(devs), ["1-2", "1-3", "1-4.1", "3-1", "3-2", "3-3", "3-4", "3-5", "3-6"])
            self.assertEqual(devs["3-4"]["kind"], "touchscreen")
            self.assertTrue(C.is_my_input(devs["3-4"]))
            self.assertEqual(devs["3-5"]["kind"], "input device")
            self.assertEqual(devs["3-6"]["kind"], "security key")   # FIDO + OTP keyboard
            self.assertEqual(devs["3-1"]["kind"], "keyboard")
            self.assertEqual(devs["3-2"]["kind"], "mouse")
            self.assertEqual(devs["3-3"]["kind"], "security key")
            self.assertTrue(C.is_my_input(devs["3-1"]) and not C.is_my_input(devs["1-2"]))
            self.assertEqual(devs["1-2"]["key"], "1050:0407/123")
            self.assertEqual(devs["1-3"]["kind"], "camera")
            self.assertTrue(devs["1-3"]["exported"])
            self.assertEqual(devs["1-4.1"]["kind"], "storage")
            self.assertEqual(devs["1-2"]["kind"], "security key")

    def test_port_parse(self):
        txt = """Imported USB devices
====================
Port 00: <Port in Use> at Full Speed(12Mbps)
       Yubico.com : Yubikey 4/5 OTP+U2F+CCID (1050:0407)
       3-1 -> usbip://127.0.0.1:47300/1-2
           -> remote bus/dev 001/004
Port 08: <Port in Use> at High Speed(480Mbps)
       unknown vendor : unknown product (046d:085c)
       4-1 -> usbip://127.0.0.1:39811/1-3
           -> remote bus/dev 001/005
"""
        p = C.parse_usbip_port(txt)
        self.assertEqual(p, [{"port": 0, "host": "127.0.0.1", "tcp": 47300, "busid": "1-2"},
                             {"port": 8, "host": "127.0.0.1", "tcp": 39811, "busid": "1-3"}])


class Keys(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self.t.name
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.t.name, ".config")

    def tearDown(self):
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.t.cleanup()

    def test_ak_lines(self):
        line = C.ak_line("ssh-ed25519 AAAAC3Nza pairdesk@x", "Laptop PC", 2)
        self.assertIn('--peer Laptop-PC --slot 2"', line)
        self.assertIn('permitlisten="127.0.0.1:47304"', line)
        self.assertEqual(line.count("permitlisten"), 1)
        self.assertIn("restrict,port-forwarding", line)
        self.assertTrue(line.endswith("pairdesk-peer=Laptop-PC"))

    def test_ak_set_in_link_file_only(self):
        os.makedirs(os.path.join(self.t.name, ".ssh"))
        mine = os.path.join(self.t.name, ".ssh", "authorized_keys")
        open(mine, "w").write("ssh-rsa AAA mine\n")
        C.ak_set("office-pc", C.ak_line("ssh-ed25519 A1", "office-pc", 0))
        C.ak_set("office-pc", C.ak_line("ssh-ed25519 A3", "office-pc", 1))
        lines = open(C.link_ak_path()).read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("A3", lines[0])
        self.assertEqual(open(mine).read(), "ssh-rsa AAA mine\n")       # the system file is untouched
        self.assertEqual(oct(os.stat(C.link_ak_path()).st_mode & 0o777), "0o600")
        C.ak_set("office-pc", None)
        self.assertEqual(open(C.link_ak_path()).read(), "")

    def test_migrate_removes_1x_lines(self):
        os.makedirs(os.path.join(self.t.name, ".ssh"))
        mine = os.path.join(self.t.name, ".ssh", "authorized_keys")
        open(mine, "w").write("ssh-rsa AAA mine\ncommand=\"x\" ssh-ed25519 B pairdesk-peer=office-pc\n"
                              "command=\"y\" ssh-ed25519 C pairdesk-rev=office-pc\n")
        c = C.load_config()
        c["peers"]["office-pc"] = {"host": "h", "user": "u", "port": 22}
        C.save_config(c)
        self.assertEqual(C.migrate_v2(), ["office-pc"])
        self.assertEqual(open(mine).read(), "ssh-rsa AAA mine\n")
        self.assertTrue(C.load_config()["peers"]["office-pc"]["needs_pairing"])

    def test_parse_address(self):
        self.assertEqual(C.parse_address("alex@192.168.1.20:4722"), ("alex", "192.168.1.20", 4722))
        self.assertEqual(C.parse_address("alex@office-pc.local"), ("alex", "office-pc.local", 4722))
        self.assertEqual(C.parse_address("alex@[fe80::1]:5000"), ("alex", "fe80::1", 5000))

    def _open_pairing(self, code="K7QM4XTR9PEH2HVA"):
        c = C.load_config()
        c["mode"] = "both"
        c["pairing"] = {"code": code, "expires": 9e12, "tries": 0}
        C.save_config(c)
        C.link_dir()
        open(os.path.join(C.link_dir(), "ssh_host_ed25519_key.pub"), "w").write("ssh-ed25519 SERVERKEY x\n")
        open(os.path.join(C.link_dir(), "ssh_host_ed25519_key"), "w").write("x")
        return code

    def test_pairing_proofs(self):
        code = self._open_pairing()
        client = "ssh-ed25519 CLIENTKEY"
        good = C._proof("k7qm-4xtr-9peh-2hva", "client", "ssh-ed25519 SERVERKEY", client)   # case/dashes ignored
        rep = C.server_accept_code({"name": "pent", "user": "pent", "pubkey": client, "proof": good})
        self.assertEqual(rep["proof"], C._proof(code, "server", "ssh-ed25519 SERVERKEY", client))
        self.assertIn("pent", C.load_config()["incoming"])
        self.assertNotIn("pairing", C.load_config())                        # one use only
        with self.assertRaises(C.DBError):
            C.server_accept_code({"name": "x", "user": "x", "pubkey": client, "proof": good})

    def test_man_in_the_middle_is_rejected(self):
        self._open_pairing()
        client = "ssh-ed25519 CLIENTKEY"
        # the client talked to an impostor with another host key: its proof doesn't fit the real server
        relayed = C._proof("K7QM4XTR9PEH2HVA", "client", "ssh-ed25519 IMPOSTORKEY", client)
        with self.assertRaises(C.DBError):
            C.server_accept_code({"name": "pent", "user": "pent", "pubkey": client, "proof": relayed})
        self.assertNotIn("pent", C.load_config()["incoming"])

    def test_three_wrong_codes_close_pairing(self):
        self._open_pairing()
        for i in range(3):
            with self.assertRaises(C.DBError) as e:
                C.server_accept_code({"name": "p", "user": "p", "pubkey": "ssh-ed25519 K",
                                      "proof": C._proof("AAAAAAAAAAAAAAAA", "client", "ssh-ed25519 SERVERKEY",
                                                        "ssh-ed25519 K")})
        self.assertIn("closed", str(e.exception))
        self.assertNotIn("pairing", C.load_config())

    def test_safe_name(self):
        self.assertEqual(C.safe_name("office-pc"), "office-pc")
        self.assertEqual(C.safe_name("../../etc"), "etc")
        self.assertEqual(C.safe_name('a"b;c'), "a-b-c")


class Extras(unittest.TestCase):
    def test_launchers(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["XDG_CONFIG_HOME"] = os.path.join(t, "c")
            os.environ["XDG_DATA_HOME"] = os.path.join(t, "d")
            m = C.set_launcher("office-pc", "menu", True)
            a = C.set_launcher("office-pc", "autostart", True)
            self.assertIn("Exec=pairdesk --connect office-pc", open(m).read())
            self.assertIn("autostart", a)
            C.set_launcher("office-pc", "menu", False)
            self.assertFalse(os.path.exists(m))
            del os.environ["XDG_CONFIG_HOME"], os.environ["XDG_DATA_HOME"]

    def test_wol_packet(self):
        import socket
        r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        r.bind(("127.0.0.1", 0))
        port = r.getsockname()[1]
        mac = "aa:bb:cc:dd:ee:ff"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        pkt = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        s.sendto(pkt, ("127.0.0.1", port))
        self.assertEqual(r.recv(200), pkt)
        self.assertGreater(C.wake_on_lan([{"mac": mac, "bcast": "127.255.255.255"}]), 0)
        self.assertEqual(C.wake_on_lan([{"mac": "zz"}]), 0)

    def test_speed_opts(self):
        o = C.ssh_speed_opts()
        self.assertIn("Compression=no", o)
        self.assertTrue(any(x.startswith("Ciphers=") for x in o))

    def test_quality_flags(self):
        self.assertIn("/network:lan", C.QUALITY["best"])
        self.assertIn("/bpp:16", C.QUALITY["fast"])


class Rename(unittest.TestCase):
    def test_settings_and_pairings_come_over_from_deskbridge(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["XDG_CONFIG_HOME"] = t
            try:
                old = os.path.join(t, "deskbridge", "link")
                os.makedirs(old)
                with open(os.path.join(t, "deskbridge", "config.json"), "w") as f:
                    f.write('{"mode": "both", "linger_by_deskbridge": true, "share_dir": "~/DeskBridge Shared", '
                            '"peers": {}, "incoming": {"pc": {"slot": 0}}}')
                with open(os.path.join(old, "authorized_keys"), "w") as f:
                    f.write('command="/usr/bin/deskbridge-remote serve --peer pc --slot 0",restrict ssh-ed25519 A '
                            'deskbridge-peer=pc\n')
                key = os.path.join(t, "deskbridge", "id_ed25519")
                with open(key, "w") as f:
                    f.write("PRIVATE")
                os.chmod(key, 0o600)
                self.assertTrue(C.migrate_from_deskbridge())
                new = os.path.join(t, "pairdesk")
                ak = open(os.path.join(new, "link", "authorized_keys")).read()
                self.assertIn("/usr/bin/pairdesk-remote serve", ak)
                self.assertTrue(ak.strip().endswith("pairdesk-peer=pc"))
                c = C.load_config()
                self.assertEqual(c["mode"], "both")
                self.assertTrue(c.get("linger_by_pairdesk"))
                self.assertEqual(c["share_dir"], "~/DeskBridge Shared")     # the user's files stay where they are
                self.assertEqual(os.stat(os.path.join(new, "id_ed25519")).st_mode & 0o777, 0o600)
                self.assertTrue(os.path.exists(os.path.join(t, "deskbridge", "config.json")))   # old left as it was
                self.assertFalse(C.migrate_from_deskbridge())             # only once
            finally:
                del os.environ["XDG_CONFIG_HOME"]


class FindAndSetUp(unittest.TestCase):
    def test_banner_names_user_and_computer(self):
        m = C.BANNER_RE.search("SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13 PairDesk/1 user=alex name=office-pc\r\n")
        self.assertEqual((m.group(2), m.group(3)), ("alex", "office-pc"))
        self.assertIsNone(C.BANNER_RE.search("SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13\r\n"))

    def test_link_config_announces_itself(self):
        text = C.SSHD_CONFIG.format(port=4722, dir="/d", user="alex", kex="k", name="office-pc")
        self.assertIn("VersionAddendum PairDesk/2 user=alex name=office-pc", text)
        self.assertIn("ClientAliveInterval 10", text)      # a vanished computer's ports are freed in 30 s

    def test_unknown_name_and_closed_port(self):
        self.assertEqual(C.resolve_address("no-such-computer.invalid")["state"], "unknown-host")
        s = __import__("socket").socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                   # nothing listens there now
        r = C.resolve_address("someone@127.0.0.1:%d" % port)
        self.assertEqual((r["state"], r["typed_user"]), ("closed", "someone"))
        with self.assertRaises(C.DBError):
            C.resolve_address("   ")

    def test_remote_setup_installs_only_when_needed(self):
        sh = C.remote_setup_script("pairdesk_2.6.0-1_all.deb", "2.6.0-1", "laptop")
        self.assertIn("db:Status-Abbrev", sh)                      # removed-but-not-purged counts as missing
        self.assertIn('dpkg --compare-versions "$have" lt 2.6.0-1', sh)   # never downgrades
        self.assertIn("pairdesk headless-setup --from-client laptop", sh)
        self.assertIn("rm -f /tmp/pairdesk_2.6.0-1_all.deb", sh)
        evil = C.remote_setup_script("x.deb", "1", "pc; rm -rf ~")
        self.assertIn("'pc; rm -rf ~'", evil)                      # names are quoted


def make_deb(d, pkg="pairdesk", ver="9.0.0-1", arch="all"):
    root = os.path.join(d, "root-%s-%s" % (pkg, ver))
    os.makedirs(os.path.join(root, "DEBIAN"))
    with open(os.path.join(root, "DEBIAN", "control"), "w") as f:
        f.write("Package: %s\nVersion: %s\nArchitecture: %s\nMaintainer: T <t@example.com>\n"
                "Description: test\n" % (pkg, ver, arch))
    out = os.path.join(d, "%s_%s.deb" % (pkg, ver))
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", root, out], check=True, capture_output=True)
    return out


class Wayland(unittest.TestCase):
    def test_wayland_only_gnome(self):
        with mock.patch.object(C.shutil, "which", return_value="/usr/bin/gnome-shell"), \
                mock.patch.object(C.glob, "glob", return_value=[]):
            self.assertTrue(C.gnome_wayland_only())
        with mock.patch.object(C.shutil, "which", return_value="/usr/bin/gnome-shell"), \
                mock.patch.object(C.glob, "glob", return_value=["/usr/share/xsessions/ubuntu-xorg.desktop"]):
            self.assertFalse(C.gnome_wayland_only())
        with mock.patch.object(C.shutil, "which", return_value=None):
            self.assertFalse(C.gnome_wayland_only())          # not GNOME: xrdp is fine

    def test_conflict_order(self):
        def run(srv, xorg=True, wl=False, signed=False):
            with mock.patch.object(C, "rdp_server", return_value=srv), \
                    mock.patch.object(C, "xorgxrdp_present", return_value=xorg), \
                    mock.patch.object(C, "gnome_wayland_only", return_value=wl), \
                    mock.patch.object(C.os.path, "exists", return_value=False), \
                    mock.patch.object(C, "signed_in_locally", return_value=signed):
                return C.desktop_conflict("alex")
        self.assertIsNone(run(None, signed=True))
        self.assertEqual(run("xrdp", xorg=False, wl=True), "xorgxrdp")
        self.assertEqual(run("xrdp", wl=True, signed=True), "wayland_only")
        self.assertEqual(run("xrdp", signed=True), "signed_in")
        self.assertEqual(run("gnome", wl=True, signed=True), "signed_in")   # GNOME Remote Login works on Wayland
        self.assertIsNone(run("gnome", wl=True))


class Updates(unittest.TestCase):
    def test_direction(self):
        self.assertEqual(C.update_direction("2.6.1-1", "2.7.0-1"), "pull")
        self.assertEqual(C.update_direction("2.7.0-1", "2.6.1-1"), "push")
        self.assertEqual(C.update_direction("2.7.0-1", "2.7.0-1"), None)
        self.assertEqual(C.update_direction("2.7.0-1", None), None)       # very old peer: says nothing
        self.assertEqual(C.update_direction("2.10.0-1", "2.9.0-1"), "push")  # not compared as text

    def _in_home(self, t, have):
        return mock.patch.multiple(C, updates_dir=lambda: os.path.join(t, "updates"),
                                   installed_version=lambda: have, load_config=lambda: {},
                                   save_config=lambda c: None)

    def test_accepts_only_newer_pairdesk(self):
        with tempfile.TemporaryDirectory() as t, self._in_home(t, "2.6.1-1"):
            good = make_deb(t, ver="2.7.0-1")
            path, ver = C.receive_package(open(good, "rb"), "laptop")
            self.assertEqual((os.path.basename(path), ver), ("pairdesk_2.7.0-1_all.deb", "2.7.0-1"))
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            for bad, why in ((make_deb(t, ver="2.6.0-1"), "isn't newer"),
                             (make_deb(t, pkg="other", ver="9-1"), "isn't a PairDesk"),
                             (make_deb(t, ver="9.0-1", arch="amd64"), "isn't a PairDesk")):
                with self.assertRaisesRegex(C.DBError, why):
                    C.receive_package(open(bad, "rb"), "laptop")
            with self.assertRaisesRegex(C.DBError, "isn't a PairDesk"):
                C.receive_package(io.BytesIO(b"not a deb"), "laptop")
            self.assertEqual(sorted(os.listdir(os.path.join(t, "updates"))), ["pairdesk_2.7.0-1_all.deb"])

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as t, self._in_home(t, "1"), mock.patch.object(C, "UPDATE_MAX", 10):
            with self.assertRaisesRegex(C.DBError, "too large"):
                C.receive_package(io.BytesIO(b"x" * 100), "laptop")
            self.assertEqual(os.listdir(os.path.join(t, "updates")), [])

    def test_install_always_asks_admin(self):
        with mock.patch.object(C, "helper_stream", return_value={"installed": "2.7.0-1"}) as hs, \
                mock.patch.multiple(C, load_config=lambda: {}, save_config=lambda c: None):
            C.install_update("/x.deb")
        self.assertTrue(hs.call_args.kwargs["admin"])


class SignedIn(unittest.TestCase):
    def test_only_sessions_on_a_seat_count(self):
        sessions = {"2": "Type=wayland\nRemote=no\nState=online\nSeat=\nClass=user",       # GNOME Remote Login
                    "3": "Type=tty\nRemote=yes\nState=active\nSeat=\nClass=user",          # SSH
                    "4": "Type=wayland\nRemote=no\nState=closing\nSeat=seat0\nClass=user",  # signing out
                    "5": "Type=wayland\nRemote=no\nState=active\nSeat=seat0\nClass=user"}  # on the screen

        def fake(cmd, **kw):
            out = "".join("%s 1000 alex seat0 tty2\n" % i for i in sessions) if cmd[1] == "list-sessions" \
                else sessions[cmd[2]]
            return subprocess.CompletedProcess(cmd, 0, out, "")
        with mock.patch.object(C.subprocess, "run", side_effect=fake):
            self.assertEqual(C.local_sessions("alex"), ["5"])
            del sessions["5"]
            self.assertFalse(C.signed_in_locally("alex"))


class Addresses(unittest.TestCase):
    def test_names_become_ips(self):
        self.assertEqual(C.to_ip("192.168.1.20"), "192.168.1.20")
        self.assertEqual(C.to_ip("localhost"), "127.0.0.1")
        self.assertIsNone(C.to_ip("no-such-computer.invalid"))
        r = C.probe("localhost", 1)                 # nothing listens on port 1
        self.assertEqual((r["state"], r["host"], r["host_name"]), ("closed", "127.0.0.1", "localhost"))
        self.assertNotIn("host_name", C.probe("127.0.0.1", 1))

    def _peer(self, t, host):
        cfg = {"peers": {"office-pc": {"host": host, "port": 4722}}, "incoming": {}}
        kh = os.path.join(t, "known_hosts")
        with open(kh, "w") as f:
            f.write("[%s]:4722 ssh-ed25519 AAAAgood\n" % host)
        return cfg, mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None,
                                        known_hosts=lambda: kh)

    def _scan(self, key):
        return mock.patch.object(C.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, "[10.0.0.9]:4722 ssh-ed25519 %s\n" % key, ""))

    def test_moves_only_to_the_same_computer(self):
        with tempfile.TemporaryDirectory() as t:
            cfg, env = self._peer(t, "192.168.1.20")
            with env, self._scan("AAAAother"):
                self.assertFalse(C.move_peer("office-pc", "10.0.0.9", log=lambda m: None))
            self.assertEqual(cfg["peers"]["office-pc"]["host"], "192.168.1.20")
            with env, self._scan("AAAAgood"):
                self.assertTrue(C.move_peer("office-pc", "10.0.0.9", log=lambda m: None))
                self.assertEqual(C.kh_get("[10.0.0.9]:4722"), "ssh-ed25519 AAAAgood")
            self.assertEqual(cfg["peers"]["office-pc"]["host"], "10.0.0.9")

    def test_name_is_kept_when_moving_to_ip(self):
        with tempfile.TemporaryDirectory() as t:
            cfg, env = self._peer(t, "office-pc.local")
            with env, self._scan("AAAAgood"):
                self.assertTrue(C.move_peer("office-pc", "10.0.0.9", log=lambda m: None))
            self.assertEqual(cfg["peers"]["office-pc"]["host_name"], "office-pc.local")


class Display(unittest.TestCase):
    def test_ended_sessions_display_is_dropped(self):
        with tempfile.TemporaryDirectory() as t:
            env = C._drop_dead_display({"XDG_RUNTIME_DIR": t, "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":97"})
            self.assertNotIn("WAYLAND_DISPLAY", env)
            self.assertNotIn("DISPLAY", env)
            open(os.path.join(t, "wayland-0"), "w").close()
            env = C._drop_dead_display({"XDG_RUNTIME_DIR": t, "WAYLAND_DISPLAY": "wayland-0",
                                        "DISPLAY": "remote-host:0"})
            self.assertEqual((env["WAYLAND_DISPLAY"], env["DISPLAY"]), ("wayland-0", "remote-host:0"))


class RemoteNeeds(unittest.TestCase):
    def test_what_the_other_computer_needs(self):
        info = {"features": {"folders": False, "share-out": True, "usb": True}, "usb_ready": False}
        opts = {"share_mine": True, "open_theirs": True}
        self.assertEqual(C.remote_needs(info, opts, {"usb_send": ["1-2"]}), ["folders", "usb"])
        self.assertEqual(C.remote_needs(info, {"share_mine": False}, {}), [])
        self.assertEqual(C.remote_needs({}, opts, {"usb_take": ["x"]}), [])       # old PairDesk: says nothing
        self.assertEqual(C.remote_install_command(["folders", "usb"]),
                         "pairdesk install folders && pairdesk install usb && pairdesk setup")


class Camera(unittest.TestCase):
    def test_lists_real_cameras_once(self):
        with tempfile.TemporaryDirectory() as t:
            def cam(n, name, index, real=True):
                d = os.path.join(t, "video%d" % n)
                os.makedirs(d)
                open(os.path.join(d, "name"), "w").write(name + "\n")
                open(os.path.join(d, "index"), "w").write(index + "\n")
                if real:
                    os.makedirs(os.path.join(d, "device"))
            cam(0, "HP Webcam: HP Webcam", "0")
            cam(1, "HP Webcam: HP Webcam", "1")          # its metadata node
            cam(2, "PairDesk camera", "0", real=False)    # our own virtual camera
            cam(10, "USB Camera", "0")
            self.assertEqual(C.list_cameras(t), [{"dev": "/dev/video0", "name": "HP Webcam"},
                                                 {"dev": "/dev/video10", "name": "USB Camera"}])

    def test_play_into_virtual_camera(self):
        cmd = C.camera_play_cmd("/dev/video9")
        self.assertEqual(cmd[-3:], ["-f", "v4l2", "/dev/video9"])
        self.assertIn("pipe:0", cmd)
        self.assertEqual(C.remote_install_command(["camera-receive"]), "pairdesk install camera-receive")


class EmbeddedDesktop(unittest.TestCase):
    def _args(self, embed, opts):
        s = C.Session.__new__(C.Session)
        s.p, s.lrdp, s.name = {"user": "alex", "rdp_user": "alex"}, 45000, "office-pc"
        with mock.patch.object(C, "rdp_client", return_value=("/usr/bin/xfreerdp3", 3)):
            return s.rdp_cmd("pw", opts, None, embed)[2]

    def test_inside_pairdesks_window(self):
        a = self._args((0x2a00007, 1440, 900), {"fullscreen": True, "multimon": True})
        self.assertIn("/parent-window:%d" % 0x2a00007, a)
        self.assertIn("/size:1440x900", a)
        self.assertIn("-toggle-fullscreen", a)
        self.assertNotIn("/f", a)                       # PairDesk's window does full screen
        self.assertNotIn("/multimon", a)
        self.assertFalse(any(x.startswith("/floatbar") for x in a))

    def test_own_window_keeps_its_bar(self):
        a = self._args(None, {"fullscreen": True})
        self.assertIn("/f", a)
        self.assertTrue(any(x.startswith("/floatbar") for x in a))


class Sound(unittest.TestCase):
    def test_choices_become_viewer_options(self):
        o = dict(C.DEFAULT_OPTS)
        self.assertEqual(C.audio_args(dict(o, microphone=False)), ["/sound"])
        self.assertEqual(C.audio_args(dict(o, sound=False, microphone=False)), ["/audio-mode:2"])
        self.assertEqual(C.audio_args(dict(o, sound_where="remote", microphone=False)), ["/audio-mode:1"])
        self.assertEqual(C.audio_args(dict(o, speaker_device="alsa_output.usb", mic_device="alsa_input.usb")),
                         ["/sound:sys:pulse,dev:alsa_output.usb", "/microphone:sys:pulse,dev:alsa_input.usb"])
        self.assertEqual(C.audio_args(dict(o, mic_device="x"), clean=True),
                         ["/sound:sys:pulse,dev:pairdesk_speaker", "/microphone:sys:pulse,dev:pairdesk_mic"])


class Knock(unittest.TestCase):
    def test_link_lets_unknown_keys_only_knock(self):
        text = C.SSHD_CONFIG.format(port=4722, dir="/d", user="alex", kex="k", name="office-pc")
        self.assertIn("AuthorizedKeysCommand /usr/lib/pairdesk/pairdesk-knock %u %t %k", text)
        self.assertIn("AuthorizedKeysCommandUser alex", text)
        self.assertIn("ExposeAuthInfo yes", text)
        self.assertIn("PairDesk/2", text)
        self.assertFalse(hasattr(C, "PAIRING_KEY"))          # no shared pairing key any more

    def test_fresh_knock_key_per_attempt(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(C, "run_dir", return_value=t):
            k1 = C.knock_key("192.168.1.20", 4722)
            self.assertEqual(C.knock_key("192.168.1.20", 4722), k1)     # same attempt: same key
            pub1 = open(k1 + ".pub").read()
            C.forget_knock_key("192.168.1.20", 4722)
            self.assertFalse(os.path.exists(k1))
            pub2 = open(C.knock_key("192.168.1.20", 4722) + ".pub").read()
            self.assertNotEqual(pub1, pub2)                              # next attempt: a new key

    def test_code_only_for_the_key_that_asked(self):
        cfg = {"mode": "server", "incoming": {}, "peers": {}}
        with mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None, link_running=lambda: True), \
                mock.patch.object(C.os, "chmod"):
            C.start_pairing(requested_by="laptop", knock="ssh-ed25519 AAAA1")
            first = cfg["pairing"]["code"]
            C.start_pairing(requested_by="intruder", knock="ssh-ed25519 BBBB2")
            self.assertNotEqual(cfg["pairing"]["code"], first)          # another knocker: a new code
            self.assertEqual(cfg["pairing"]["knock"], "ssh-ed25519 BBBB2")
            with self.assertRaisesRegex(C.DBError, "another computer"):
                C.server_accept_code({"pubkey": "x", "proof": "y", "name": "n", "user": "u"},
                                     knock="ssh-ed25519 AAAA1")

    def test_refuses_requests_when_only_on_press(self):
        cfg = {"mode": "server", "incoming": {}, "peers": {}, "pair_only_on_press": True}
        with mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None):
            with self.assertRaisesRegex(C.DBError, "Pair a new computer"):
                C.server_pair_request({"name": "laptop", "user": "alex"}, knock="ssh-ed25519 AAAA1")

    def test_banner_says_pairing_version(self):
        m = C.BANNER_RE.search("SSH-2.0-OpenSSH_9.6 PairDesk/2 user=alex name=office-pc")
        self.assertEqual(m.group(1), "2")


if __name__ == "__main__":
    unittest.main()


class Reinstalled(unittest.TestCase):
    """A computer reinstalled at the same address: its new SSH identity is trusted only after asking, and
    a new pairing replaces old ones at that address."""

    CHANGED = ("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
               "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
               "Offending ECDSA key in /home/u/.ssh/known_hosts:3\nHost key verification failed.\n")

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self.t.name
        os.makedirs(os.path.join(self.t.name, ".ssh"))
        self.kh = os.path.join(self.t.name, ".ssh", "known_hosts")
        with open(self.kh, "w") as f:
            f.write("other.lan ssh-ed25519 AAAAkeep\n192.168.1.20 ssh-ed25519 AAAAold\n")
        self.calls = []
        self.ssh_results = []

    def tearDown(self):
        C.HOSTKEY_CONFIRM = None
        self.t.cleanup()

    def fake_run(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "ssh" and cmd[1] == "-G":
            return subprocess.CompletedProcess(cmd, 0, "user alex\nhostname 192.168.1.20\nport 22\n", "")
        if cmd[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(cmd, 0, "# banner\n192.168.1.20 ssh-ed25519 AAAAnew\n"
                                                       "192.168.1.20 ecdsa-sha2-nistp256 AAAAecdsa\n", "")
        if cmd[:2] == ["ssh-keygen", "-lf"]:
            self.assertIn("ssh-ed25519 AAAAnew", kw["input"])
            return subprocess.CompletedProcess(cmd, 0, "256 SHA256:NEWFP 192.168.1.20 (ED25519)\n", "")
        if cmd[:2] == ["ssh-keygen", "-R"]:
            lines = [x for x in open(cmd[4]).read().splitlines() if x.split(" ", 1)[0] != cmd[2]]
            open(cmd[4], "w").write("\n".join(lines) + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "ssh":
            return self.ssh_results.pop(0)
        raise AssertionError(cmd)

    def ssh(self, code, out="", err=""):
        return subprocess.CompletedProcess(["ssh"], code, out, err)

    def test_changed_identity_trusted_after_yes_then_retried(self):
        asked = []
        C.HOSTKEY_CONFIRM = lambda h, p, fp: asked.append((h, p, fp)) or True
        self.ssh_results = [self.ssh(255, err=self.CHANGED), self.ssh(0, out="K7QM-4XTR-9PEH-2HVA\n")]
        with mock.patch.object(C.subprocess, "run", side_effect=self.fake_run):
            self.assertEqual(C.ssh_get_code("alex@192.168.1.20"), "K7QM-4XTR-9PEH-2HVA")
        self.assertEqual(asked, [("192.168.1.20", 22, "SHA256:NEWFP")])
        lines = open(self.kh).read().splitlines()
        self.assertIn("other.lan ssh-ed25519 AAAAkeep", lines)                     # other computers untouched
        self.assertNotIn("192.168.1.20 ssh-ed25519 AAAAold", lines)
        self.assertIn("192.168.1.20 ssh-ed25519 AAAAnew", lines)                   # exactly the keys shown
        self.assertEqual(oct(os.stat(self.kh).st_mode & 0o777), "0o600")

    def test_changed_identity_refused_keeps_the_old_one(self):
        C.HOSTKEY_CONFIRM = lambda h, p, fp: False
        self.ssh_results = [self.ssh(255, err=self.CHANGED)]
        with mock.patch.object(C.subprocess, "run", side_effect=self.fake_run):
            with self.assertRaises(C.DBError) as e:
                C.ssh_get_code("alex@192.168.1.20")
        self.assertIn("SHA256:NEWFP", str(e.exception))
        self.assertIn("192.168.1.20 ssh-ed25519 AAAAold", open(self.kh).read())
        self.assertFalse(any(c[:2] == ["ssh-keygen", "-R"] for c in self.calls))

    def test_without_anyone_to_ask_it_stops(self):
        self.ssh_results = [self.ssh(255, err=self.CHANGED)]
        with mock.patch.object(C.subprocess, "run", side_effect=self.fake_run):
            with self.assertRaises(C.DBError) as e:
                C.ssh_run("alex@192.168.1.20", "true")
        self.assertIn("reinstalled", str(e.exception))
        self.assertIn("192.168.1.20 ssh-ed25519 AAAAold", open(self.kh).read())

    def test_asks_only_once(self):
        asked = []
        C.HOSTKEY_CONFIRM = lambda h, p, fp: asked.append(fp) or True
        self.ssh_results = [self.ssh(255, err=self.CHANGED), self.ssh(255, err=self.CHANGED)]
        with mock.patch.object(C.subprocess, "run", side_effect=self.fake_run):
            with self.assertRaises(C.DBError) as e:
                C.ssh_run("alex@192.168.1.20", "true")
        self.assertEqual(len(asked), 1)
        self.assertIn("identity wasn't confirmed", str(e.exception))

    def test_other_ports_are_written_in_brackets(self):
        with mock.patch.object(C.subprocess, "run", side_effect=self.fake_run):
            C.ssh_trust_keys("192.168.1.20", 2222, ["[192.168.1.20]:2222 ssh-ed25519 AAAAnew"])
        self.assertIn("[192.168.1.20]:2222 ssh-ed25519 AAAAnew", open(self.kh).read())
        self.assertIn("192.168.1.20 ssh-ed25519 AAAAold", open(self.kh).read())    # port 22 entry stays

    def test_new_pairing_replaces_old_pairings_at_the_same_address(self):
        code = "K7QM-4XTR-9PEH-2HVA"
        cfg = {"peers": {"office-old": {"host": "10.0.0.5", "port": 4722, "user": "a"},
                         "laptop": {"host": "10.0.0.7", "port": 4722, "user": "b"},
                         "office-ssh": {"host": "10.0.0.5", "port": 22, "user": "c"}}, "incoming": {}}
        lkh = os.path.join(self.t.name, "pd_known_hosts")
        open(lkh, "w").write("[10.0.0.5]:4722 ssh-ed25519 AAAAold\n")
        pub = "ssh-ed25519 CLIENT"

        def pair_call(address, what, req, hk):
            return {"name": "office-new", "user": "a", "slot": 1, "rusb": 0, "rsock": "s",
                    "proof": C._proof(code, "server", hk, pub)}, None

        scan = subprocess.CompletedProcess([], 0, "[10.0.0.5]:4722 ssh-ed25519 AAAAnew\n", "")
        said = []
        with mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None, known_hosts=lambda: lkh,
                                 to_ip=lambda h, p=None: h, ensure_key=lambda: pub, _pair_call=pair_call,
                                 forget_knock_key=lambda h, p: None, hostname=lambda: "me",
                                 getpass_user=lambda: "me"), \
                mock.patch.object(C.subprocess, "run", return_value=scan):
            self.assertEqual(C.client_pair_code("a@10.0.0.5:4722", code, log=said.append), "office-new")
        self.assertEqual(sorted(cfg["peers"]), ["laptop", "office-new", "office-ssh"])   # other port stays
        self.assertTrue(any("office-old" in m for m in said))
        self.assertEqual(C_kh(lkh), ["[10.0.0.5]:4722 ssh-ed25519 AAAAnew"])


def C_kh(path):
    return open(path).read().splitlines()


class Forget(unittest.TestCase):
    def test_forget_uses_the_link_port_and_keeps_shared_lines(self):
        with tempfile.TemporaryDirectory() as t:
            kh = os.path.join(t, "known_hosts")
            open(kh, "w").write("[10.0.0.5]:4722 ssh-ed25519 A\n[10.0.0.6]:4722 ssh-ed25519 B\n")
            cfg = {"peers": {"old": {"host": "10.0.0.5"}, "x": {"host": "10.0.0.6", "port": 4722},
                             "y": {"host": "10.0.0.6", "port": 4722}}, "incoming": {}}
            with mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None, known_hosts=lambda: kh):
                C.forget_peer("old")                  # no port saved: the link port, not 22
                C.forget_peer("x")                    # y still uses 10.0.0.6: its line stays
            self.assertEqual(open(kh).read(), "[10.0.0.6]:4722 ssh-ed25519 B\n")

    def test_move_drops_the_old_address_line(self):
        with tempfile.TemporaryDirectory() as t:
            kh = os.path.join(t, "known_hosts")
            open(kh, "w").write("[192.168.1.20]:4722 ssh-ed25519 AAAAgood\n")
            cfg = {"peers": {"office-pc": {"host": "192.168.1.20", "port": 4722}}, "incoming": {}}
            scan = subprocess.CompletedProcess([], 0, "[10.0.0.9]:4722 ssh-ed25519 AAAAgood\n", "")
            env = mock.patch.multiple(C, load_config=lambda: cfg, save_config=lambda c: None, known_hosts=lambda: kh)
            with env, mock.patch.object(C.subprocess, "run", return_value=scan):
                self.assertTrue(C.move_peer("office-pc", "10.0.0.9", log=lambda m: None))
            self.assertEqual(open(kh).read(), "[10.0.0.9]:4722 ssh-ed25519 AAAAgood\n")


class FakeRdp:
    def __init__(self, rc, err=""):
        self.returncode, self.stderr = rc, io.StringIO(err)

    def wait(self, *a):
        return self.returncode

    def poll(self):
        return self.returncode


class DesktopEndsThere(unittest.TestCase):
    """The desktop view never takes a session back from someone signing in at the other screen: FreeRDP
    doesn't reconnect by itself, and PairDesk opens it again only after a real connection drop."""

    def session(self, rdps, infos, lost_at=0.0):
        s = C.Session.__new__(C.Session)
        s.name, s.closing, s.state, s.opts = "office-pc", False, "connected", {}
        s.rdp, s.desktop_password, s.link_lost_at = rdps.pop(0), "pw", lost_at
        s.started, s.calls = [], []

        def call(what):
            s.calls.append(what)
            r = infos.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        def start_desktop(pw, opts):
            s.started.append(pw)
            s.rdp = rdps.pop(0)

        s.call, s.start_desktop, s.alive, s.log = call, start_desktop, lambda: True, lambda m: None
        return s

    def run_watch(self, s):
        ended = []
        with mock.patch.object(C, "flog"), mock.patch.object(C.time, "sleep"):
            s._watch_rdp(lambda rc, err: ended.append(rc))
        return ended

    def test_freerdp_does_not_reconnect_by_itself(self):
        s = C.Session.__new__(C.Session)
        s.p, s.lrdp, s.name = {"user": "alex", "rdp_user": "alex"}, 45000, "office-pc"
        with mock.patch.object(C, "rdp_client", return_value=("/usr/bin/xfreerdp3", 3)):
            a = s.rdp_cmd("pw", {}, None, None)[2]
        self.assertFalse(any("auto-reconnect" in x for x in a))

    def test_closed_here_just_ends(self):
        s = self.session([FakeRdp(0)], [])
        self.assertEqual(self.run_watch(s), [0])
        self.assertEqual((s.calls, s.started, s.desktop_end), ([], [], None))

    def test_someone_signed_in_at_the_screen_keeps_it(self):
        s = self.session([FakeRdp(131)], [{"rdp_conflict": "signed_in"}])
        self.assertEqual(self.run_watch(s), [131])
        self.assertEqual((s.started, s.desktop_end), ([], "signed_in"))
        self.assertIn("handed to them", C.desktop_end_text(s.desktop_end, s.name))

    def test_ended_there_with_the_link_up_is_not_reopened(self):
        s = self.session([FakeRdp(1)], [{"rdp_conflict": None}])
        self.assertEqual(self.run_watch(s), [1])
        self.assertEqual((s.started, s.desktop_end), ([], "ended_there"))

    def test_a_real_drop_reopens_once_the_link_is_back(self):
        s = self.session([FakeRdp(131), FakeRdp(0)], [{"rdp_conflict": None}], lost_at=C.time.time())
        self.assertEqual(self.run_watch(s), [0])          # reopened, then closed here
        self.assertEqual((s.started, s.desktop_end), (["pw"], None))

    def test_link_down_counts_as_a_drop(self):
        s = self.session([FakeRdp(131), FakeRdp(0)], [C.DBError("no link"), {"rdp_conflict": None}])
        self.assertEqual(self.run_watch(s), [0])
        self.assertEqual(s.started, ["pw"])

    def test_after_a_drop_someone_at_the_screen_wins(self):
        s = self.session([FakeRdp(131)], [{"rdp_conflict": "signed_in"}], lost_at=C.time.time())
        self.assertEqual(self.run_watch(s), [131])
        self.assertEqual((s.started, s.desktop_end), ([], "signed_in"))

    def test_disconnecting_meanwhile_ends_it(self):
        s = self.session([FakeRdp(131)], [], lost_at=C.time.time())
        s.closing = True
        self.assertEqual(self.run_watch(s), [131])
        self.assertEqual(s.started, [])
