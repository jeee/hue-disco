#!/bin/sh
set -eu
python3 - <<'PY2'
import os, signal
me = os.getpid()
ppid = os.getppid()
for pid in sorted(p for p in os.listdir('/proc') if p.isdigit()):
    ipid = int(pid)
    if ipid in (1, me, ppid):
        continue
    try:
        cmd = open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\x00', b' ').decode().strip()
    except Exception:
        continue
    if cmd.endswith('mg21_daemon.py') or ' /opt/hue-emulator/ext/mg21-native/mg21_daemon.py' in cmd:
        try:
            os.kill(ipid, signal.SIGKILL)
        except Exception:
            pass
PY2
nohup /opt/mg21-venv/bin/python /opt/hue-emulator/ext/mg21-native/mg21_daemon.py >/tmp/mg21-daemon.log 2>&1 &
