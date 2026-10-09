"""Linux management process ownership; independent of transport and browser."""
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

def call(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=25, **kwargs).stdout


def ssh(c, command):
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-p', str(c['ssh_port']), c['remote'], command]


def remote_command(c, argv, env=None):
    import shlex
    return ssh(c, shlex.join(['python3', '-u', c['remote_worker'],
                             json.dumps({'argv': argv, 'env': env or {}})]))


def c_worker_in(argv):
    return 'remote_worker.py' in argv[-1]


def stamp(pid):
    # starttime protects stop from PID reuse. Field 2 may contain spaces.
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


class ProcessOwner:
    def __init__(self, c, mode):
        self.c, self.mode = c, mode
        self.children, self.handles = [], []
        self.done = threading.Event()
        self.isaac_ready = False
        self.directory = Path(c['raw_dir']) / ('run-' + time.strftime('%Y%m%d-%H%M%S') + '-' + mode + '-' + str(os.getpid()))
        self.directory.mkdir(parents=True, mode=0o700)

    def spawn(self, name, argv):
        log = (self.directory / (name + '.log')).open('wb')
        self.handles.append(log)
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                start_new_session=True)
        proc.remote_worker = argv[0] == 'ssh' and c_worker_in(argv)
        self.children.append(proc)
        return proc

    def wait_for(self, condition, timeout=25):
        until = time.monotonic() + timeout
        while not condition():
            if self.done.is_set() or any(p.poll() is not None for p in self.children):
                raise RuntimeError('Managed child exited; inspect private logs')
            if time.monotonic() >= until:
                raise TimeoutError('Startup timed out; inspect private logs')
            self.done.wait(.2)

    def close(self):
        # Close SSH stdin first so remote_worker terminates its own child.
        for p in reversed(self.children):
            if p.stdin:
                p.stdin.close()
            if not p.remote_worker and p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        for p in reversed(self.children):
            try:
                p.wait(timeout=22 if p.remote_worker else 3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL); p.wait()
        for handle in self.handles:
            handle.close()

    def update_status(self, configuration_present=False):
        log = self.directory / 'isaac.log'
        if not self.isaac_ready and log.exists():
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size - 262144))
                self.isaac_ready = b'PIPER_ISAAC_READY' in handle.read()
        alive = all(p.poll() is None for p in self.children)
        temporary = self.directory / 'status.tmp'
        temporary.write_text(json.dumps({'configuration_present': configuration_present,
            'components_started': alive, 'isaac_ready': alive and self.isaac_ready,
            'log_directory': str(self.directory)}))
        temporary.replace(self.directory / 'status.json')
