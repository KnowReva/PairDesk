import os, socket, subprocess, sys, time
sys.path.insert(0, "/usr/lib/pairdesk")
import dbcore as C

def probe(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    data = s.recv(64); s.close(); return data

C.share_dir()
open(os.path.join(C.share_dir(), "from-laptop.txt"), "w").write("hello from laptop\n")
s = C.Session("office-pc")
info = s.open()
print("INFO", info)
print("RDP via tunnel ->", probe(s.lrdp))
print("USB(peer->me) via tunnel ->", probe(s.lusb))
s.mount_theirs()
print("THEIR FOLDER:", os.listdir(s.mounted_theirs), open(os.path.join(s.mounted_theirs, "from-office-pc.txt")).read().strip())
open(os.path.join(s.mounted_theirs, "written-by-laptop.txt"), "w").write("ok\n")
s.share_mine()
print("REMOTE devices:", s.remote_usb())
# forbidden things with the paired key
base = s._ssh_base()
r = subprocess.run(base + [s.dest(), "id"], capture_output=True, text=True); print("ARBITRARY CMD ->", r.stdout.strip() or r.stderr.strip())
r = subprocess.run(base + [s.dest(), "pairdesk usb-import 9999 1-1"], capture_output=True, text=True); print("WRONG PORT ->", r.stdout.strip())
r = subprocess.run(["ssh", "-p", "22", "-i", C.key_path(), "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + C.known_hosts(),
                    "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N", "-f", "-L", "127.0.0.1:47999:127.0.0.1:22", "alex@127.0.0.1"],
                   capture_output=True, text=True, timeout=15); print("FORBIDDEN FORWARD rc", r.returncode, r.stderr.strip()[-120:])
input_ = None
s.disconnect()
print("MOUNTED AFTER:", C.is_mounted(os.path.join(C.mount_root(), "office-pc")))
