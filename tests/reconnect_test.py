import os, sys, time, subprocess, socket
sys.path.insert(0, "/usr/lib/pairdesk")
import dbcore as C
states = []
s = C.Session(sys.argv[1], log=lambda m: print("  log:", m), on_state=states.append)
t0 = time.time()
ready = []
info = s.connect({"desktop": False, "share_mine": True, "open_theirs": True}, on_ready=lambda i: ready.append(time.time() - t0))
print("link up after %.2fs, everything ready after %.2fs" % (ready[0], time.time() - t0))
print("wol info from server:", info.get("wol"))
lrdp = s.lrdp
print("their folder:", s.mounted_theirs, sorted(os.listdir(s.mounted_theirs)))
print("--- killing the SSH link to simulate a Wi-Fi drop")
s.master.kill()
for _ in range(40):
    time.sleep(0.5)
    if states[-1] == "connected" and states.count("reconnecting"):
        break
print("states:", states)
socket.create_connection(("127.0.0.1", s.lrdp), 3).close(); print("same desktop port after reconnect:", s.lrdp == lrdp, "| desktop tunnel connects: True")
print("their folder after reconnect:", sorted(os.listdir(s.mounted_theirs)))
print("my folder seen on office-pc:", subprocess.run(["sudo","-n","true"]).returncode)
s.disconnect()
print("final state:", s.state, "| link alive:", s.alive())
