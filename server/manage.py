#!/usr/bin/env python3
"""Manage the Linux SSH-owned streaming stack (Python >=3.10)."""
import argparse
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from lifecycle import stamp

ROOT = Path(__file__).resolve().parents[1]


def private_write(path, data):
    path = Path(path).expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError('Private configuration must be outside the checkout')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    try:
        with temporary.open('x') as handle:
            os.chmod(temporary, 0o600)
            json.dump(data, handle, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def state_status(raw):
    state_path = raw / 'network-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    active = bool(state.get('start') and stamp(state.get('pid')) == state['start'])
    facts = {}
    if active and state.get('directory'):
        status = Path(state['directory']) / 'status.json'
        if status.exists():
            facts = json.loads(status.read_text())
    return dict(facts, manager_active=active, mode=state.get('mode'),
                configuration_present=(raw / 'viewer-runtime.json').is_file(),
                components_started=active and facts.get('components_started', False),
                isaac_ready=active and facts.get('isaac_ready', False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'start', 'stop', 'status', 'export-client'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--mode', choices=['turn', 'relay'], default='turn')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    c = json.loads(args.config.read_text())
    raw = Path(c['raw_dir']).expanduser().resolve()
    if raw.is_relative_to(ROOT):
        raise ValueError('raw_dir must be outside checkout')
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    c['raw_dir'] = str(raw)
    state_path = raw / 'network-state.json'
    if args.action == 'status':
        print(json.dumps(state_status(raw), indent=2)); return
    if args.action == 'export-client':
        if not args.output:
            parser.error('export-client requires --output')
        status = state_status(raw)
        if not status['manager_active'] or not status['configuration_present']:
            raise ValueError('Start TURN before exporting its detected container address')
        data = json.loads((raw / 'viewer-runtime.json').read_text())
        if c.get('legacy_client'):
            data['relay'] = {key: c['legacy_client'][key] for key in
                             ('signalingServer', 'signalingPort', 'mediaServer', 'mediaPort')}
        private_write(args.output, data)
        print('Client configuration exported (private file; no credentials printed).'); return
    if args.action == 'stop':
        if not state_path.exists():
            print('No managed stack'); return
        state = json.loads(state_path.read_text())
        if not state.get('start') or stamp(state['pid']) != state['start']:
            print('Stale state; no process killed'); return
        os.kill(state['pid'], signal.SIGTERM)
        until = time.monotonic() + 90
        while state_path.exists() and time.monotonic() < until:
            time.sleep(.2)
        if state_path.exists():
            raise TimeoutError('Cleanup pending; inspect private manager logs')
        print('Stopped; owned processes and configuration cleaned'); return
    if args.mode == 'turn':
        from turn import TurnStack, preflight
        factory = TurnStack
    else:
        sys.path.insert(0, str(ROOT / 'legacy'))
        from relay import RelayStack, preflight
        factory = RelayStack
    if args.action == 'preflight':
        preflight(c); return
    with (raw / 'network.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = factory(c, args.mode)
        signal.signal(signal.SIGTERM, lambda *_: owner.done.set())
        signal.signal(signal.SIGINT, lambda *_: owner.done.set())
        private_write(state_path, {'pid': os.getpid(), 'start': stamp(os.getpid()),
                                 'mode': args.mode, 'directory': str(owner.directory)})
        try:
            owner.start()
        finally:
            try:
                owner.close()
            finally:
                state_path.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
