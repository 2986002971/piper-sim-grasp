"""Public CLI; deployment commands execute on server, lifecycle supports SSH."""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys

from runtime.config import ROOT, load, paths, isaac_command, write_json


def forward(config, argv):
    ssh = config['ssh']
    command = shlex.join(['/usr/bin/python3', config['repo_path']+'/runtime/remote_worker.py'])
    args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-p', str(ssh['port']), ssh['remote'], command]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, text=True)
    payload = {'argv': ['/usr/bin/python3', config['repo_path']+'/piper', '--config', '@environment', '--local', *argv],
               'env': {'PIPER_MACHINE_CONFIG_JSON': json.dumps(config)}, 'watchdog_seconds': 0,
               'watch_stdin': argv[0] == 'start', 'shutdown_timeout_seconds': 120}
    proc.stdin.write(json.dumps(payload)+'\n'); proc.stdin.flush()
    def close(*_):
        if not proc.stdin.closed: proc.stdin.close()
    previous = {sig: signal.signal(sig, close) for sig in (signal.SIGINT, signal.SIGTERM)}
    try: return proc.wait()
    finally:
        close()
        for sig, handler in previous.items(): signal.signal(sig, handler)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, help='Private machine JSON')
    p.add_argument('--local', action='store_true', help='Execute on this simulation server instead of SSH')
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('deploy', 'build', 'doctor', 'status', 'stop'): sub.add_parser(name)
    start = sub.add_parser('start'); mode = start.add_mutually_exclusive_group()
    mode.add_argument('--stream', action='store_true'); mode.add_argument('--headless', action='store_true')
    experiment = sub.add_parser('experiment'); experiment.add_argument('operation', choices=['start', 'cancel'])
    client = sub.add_parser('export-client'); client.add_argument('--output', required=True, type=Path)
    assets = sub.add_parser('assets'); ops = assets.add_subparsers(dest='operation', required=True)
    exp = ops.add_parser('export')
    for key in ('scene', 'robot-urdf', 'assembly-urdf', 'scene-metadata', 'output'): exp.add_argument('--'+key, required=True)
    check = sub.add_parser('check'); check.add_argument('level', choices=['devices', 'control', 'perception', 'unit'])
    check.add_argument('--stream', action='store_true', help='Use same stream-enabled runner in device/control checks')
    return p


def execute(args, config):
    if args.action in ('deploy', 'build'):
        from deploy.pipeline import deploy, build
        from runtime.lifecycle import exclusive_operation
        with exclusive_operation(config):
            return {'deploy': deploy, 'build': build}[args.action](config)
    if args.action == 'doctor':
        from checks.doctor import doctor
        result = doctor(config); print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 1
    if args.action == 'assets':
        command = [ROOT/'models/bundle.py']
        for key in ('scene', 'robot_urdf', 'assembly_urdf', 'scene_metadata', 'output'):
            command += ['--'+key.replace('_','-'), getattr(args, key)]
        subprocess.run(isaac_command(config, command), check=True); return
    if args.action == 'check':
        from checks.run import check
        return check(config, args.level, args.stream)
    from runtime import session
    if args.action == 'start': return session.run(config, args.stream)
    if args.action == 'status': print(json.dumps(session.status(config), indent=2)); return
    if args.action == 'stop': return session.stop(config)
    if args.action == 'experiment': return session.experiment(config, args.operation)
    if args.action == 'export-client':
        if not session.status(config)['manager_active']: raise RuntimeError('Start a streaming session first')
        content = json.loads((paths(config)['state']/'viewer-runtime.json').read_text())
        if str(args.output) == '-': print(json.dumps(content))
        else: write_json(args.output, content, private=True)


def main():
    args = parser().parse_args()
    try:
        config = load(args.config)
        os.umask(0o077)
        if not args.local and config.get('ssh') and args.action in ('start', 'stop', 'status', 'experiment', 'doctor'):
            command = [args.action]
            if args.action == 'start' and args.stream: command += ['--stream']
            if args.action == 'experiment': command += [args.operation]
            raise SystemExit(forward(config, command))
        if not args.local and config.get('ssh') and args.action == 'export-client':
            # Capture the private payload, never print credentials to the terminal.
            ssh = config['ssh']
            remote = shlex.join(['/usr/bin/python3', config['repo_path']+'/runtime/remote_worker.py'])
            payload = {'argv': ['/usr/bin/python3', config['repo_path']+'/piper', '--local', '--config', '@environment',
                               'export-client', '--output', '-'],
                       'env': {'PIPER_MACHINE_CONFIG_JSON': json.dumps(config)}, 'watchdog_seconds': 0, 'watch_stdin': False}
            result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-p',str(ssh['port']),ssh['remote'],remote],
                                    input=json.dumps(payload)+'\n', text=True, capture_output=True, check=True)
            write_json(args.output, json.loads(result.stdout), private=True)
            print('Client configuration exported.'); return
        if not args.local and config.get('ssh') and args.action in ('deploy','build','assets','check'):
            raise ValueError('Run this command on the simulation server with --local; source synchronization is external')
        os.environ['ROS_DOMAIN_ID'] = str(config['ros_domain_id'])
        if config.get('vulkan_icd'): os.environ['VK_ICD_FILENAMES'] = config['vulkan_icd']
        raise SystemExit(execute(args, config) or 0)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        # Do not print subprocess argv, which may include private deployment metadata.
        if isinstance(error, subprocess.CalledProcessError):
            message = f'Command failed (exit {error.returncode}); see command output or deployment logs'
        else: message = str(error)
        print(f'{type(error).__name__}: {message}', file=sys.stderr)
        raise SystemExit(1)
