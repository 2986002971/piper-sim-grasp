#!/usr/bin/env python3
"""Forward one command, optionally stopping its process group on stdin EOF or timeout."""
import json
import os
import signal
import subprocess
import sys
import threading

if len(sys.argv) > 1:
    payload = json.loads(sys.argv[1])
else:
    # Do not buffer beyond the header: remaining stdin owns the child lifetime.
    header = bytearray()
    while True:
        byte = os.read(sys.stdin.fileno(), 1)
        if byte in (b'\n', b''): break
        header.extend(byte)
    payload = json.loads(header)
env = os.environ.copy()
env.update(payload.get('env', {}))
child = subprocess.Popen(payload['argv'], env=env, start_new_session=True)
stopping = threading.Event()


def stop(*_):
    if stopping.is_set():
        return
    stopping.set()
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=payload.get('shutdown_timeout_seconds', 20))
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)


def watch():
    # A blocking buffered read in a daemon thread can abort Python shutdown
    # after the child exits. The raw fd has no BufferedReader lock to acquire.
    while os.read(sys.stdin.fileno(), 1):
        pass
    stop()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if payload.get('watch_stdin', True): threading.Thread(target=watch, daemon=True).start()
watchdog = threading.Timer(payload.get('watchdog_seconds', 4500), stop)
watchdog.daemon = True
if payload.get('watchdog_seconds', 4500): watchdog.start()
try:
    sys.exit(child.wait())
finally:
    stop()
