import sys, os
sys.path.insert(0, "/usr/lib/pairdesk")
import dbcore as C
c = C.load_config(); c["peers"]["office-pc"]["usb_send"] = ["1050:0407"]; C.save_config(c)
s = C.Session("office-pc", log=lambda m: print("  log:", m))
s.connect({"desktop": False, "share_mine": True, "open_theirs": True})
print("remote folder files:", sorted(os.listdir(s.mounted_theirs)))
try:
    s.send_usb({"busid": "1-2", "key": "1050:0407", "name": "YubiKey"})
except C.DBError as e:
    print("manual USB attempt ->", type(e).__name__, e)
s.disconnect()
