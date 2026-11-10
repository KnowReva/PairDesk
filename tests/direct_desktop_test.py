import sys, subprocess, os
sys.path.insert(0, "/usr/lib/pairdesk")
import dbcore as C
s = C.Session("office-pc", log=lambda m: print("  log:", m))
info = s.open()
print("office-pc desktop server listening:", info["rdp_listening"])
binp, ver = C.rdp_client()
d = s.direct_target(ver)
print("direct target:", d)
_, _, args = s.rdp_cmd("wrong-password", {"quality": "auto", "clipboard": True}, d)
print("viewer target args:", [a for a in args if a.startswith(("/v:", "/cert"))])
r = subprocess.run([binp] + args + ["+auth-only", "/sec:tls"], capture_output=True, text=True, timeout=30,
                   env=dict(os.environ, DISPLAY=":83"))
print("certificate accepted, reached login:", "Authentication only" in r.stderr and "TLS_CONNECT_FAILED" not in r.stderr)
s.disconnect()
