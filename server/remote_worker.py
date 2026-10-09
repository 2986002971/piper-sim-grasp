#!/usr/bin/env python3
"""Run exactly one child until SSH stdin closes; bound orphan life to 75 minutes."""
import json
import os
import signal
import subprocess
import sys
import threading

payload = json.loads(sys.argv[1])
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
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)


def watch():
    while sys.stdin.buffer.read(1):
        pass
    stop()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
threading.Thread(target=watch, daemon=True).start()
watchdog = threading.Timer(4500, stop)
watchdog.daemon = True
watchdog.start()
try:
    sys.exit(child.wait())
finally:
    stop()
