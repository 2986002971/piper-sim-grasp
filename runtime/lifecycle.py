"""Linux process identity and exclusive mutation of a deployment directory."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def exclusive_operation(config):
    state = Path(config['data_root'])/'state'
    state.mkdir(parents=True, exist_ok=True)
    with (state/'session.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Stop the active session/check and wait for deployment or build before changing its files') from None
        yield


def stamp(pid):
    # starttime protects stop from PID reuse. Field 2 may contain spaces.
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        # An exited child may remain in /proc until its owner calls wait().
        # It has already released its resources and must not keep stop waiting.
        return None if fields[0] in ('Z', 'X') else fields[19]
    except FileNotFoundError:
        return None

