#!/usr/bin/env python3
"""Owned Isaac + coturn + optional HAProxy TURN stack."""
import json
import os
import shlex
import socket
import time
import uuid
from pathlib import Path
from lifecycle import ProcessOwner, call, remote_command, ssh


def remote(c, action, payload):
    argv = ['python3', c['turn']['remote_helper'], action]
    return json.loads(call(ssh(c, shlex.join(argv)), input=json.dumps(payload))) if action == 'inspect' else call(
        ssh(c, shlex.join(argv)), input=json.dumps(payload))


def preflight(c):
    for key in ['remote', 'ssh_port', 'remote_worker', 'raw_dir']:
        if not c.get(key):
            raise ValueError('Missing ' + key)
    t = c['turn']
    for key in ['host', 'port', 'username', 'password', 'binary', 'config_root', 'remote_helper']:
        if not t.get(key):
            raise ValueError('Missing turn.' + key)
    probe = dict(t)
    if t.get('tcp_ingress', True):
        for key in ['haproxy_binary']:
            if not t.get(key):
                raise ValueError('Missing turn.' + key)
        probe.update(listen_port=16010, ingress_name='haproxy-preflight.cfg')
    if not c.get('isaac'):
        raise ValueError('TURN must launch an owned Isaac with the container address')
    probe['isaac'] = c['isaac']
    probe['remote_worker'] = c['remote_worker']
    result = remote(c, 'inspect', probe)
    for port in [int(t['port']), int(t.get('signaling_port', 50239))]:
        with socket.create_connection((t['host'], port), timeout=8):
            pass
    # The platform forward accepts TCP before its target starts. The browser test
    # verifies TURN authentication and media; this check only confirms TCP ingress.
    print('TURN preflight passed: container address/version/free ports and platform TCP ingress.', flush=True)
    return result


class TurnStack(ProcessOwner):
    def __init__(self, c, mode):
        super().__init__(c, mode)
        self.config_name = 'turn-' + uuid.uuid4().hex + '.conf'
        self.ingress_name = 'haproxy-' + uuid.uuid4().hex + '.cfg'
        self.wrote_config = False
        self.runtime_file = None

    def start(self):
        c, t = self.c, self.c['turn']
        facts = preflight(c)
        ingress = bool(t.get('tcp_ingress', True))
        payload = dict(t, relay_ip=facts['relay_ip'], name=self.config_name,
                       listen_ip=facts['relay_ip'] if ingress else '127.0.0.1',
                       listen_port=16010 if ingress else 6008)
        if ingress:
            payload.update(ingress_name=self.ingress_name, frontend_port=6008)
        self.wrote_config = True
        remote(c, 'write', payload)
        # Private runtime file is served only on loopback; no password in bundle/argv/log.
        runtime = Path(c['raw_dir']) / 'viewer-runtime.json'
        self.runtime_file = runtime
        data = viewer_config(c, facts['relay_ip'])
        temporary = runtime.with_suffix('.tmp')
        temporary.write_text(json.dumps(data)); os.chmod(temporary, 0o600)
        temporary.replace(runtime)
        self.spawn('coturn', remote_command(c, [t['binary'], '-c', str(Path(t['config_root']) / self.config_name)]))
        self.wait_for(lambda: 'Relay address to use' in (self.directory / 'coturn.log').read_text(errors='replace'), timeout=30)
        if ingress:
            ingress_path = str(Path(t['config_root']) / self.ingress_name)
            self.spawn('turn-tcp-ingress', remote_command(c, [t['haproxy_binary'], '-db', '-f', ingress_path]))
            time.sleep(.5)
            if self.children[-1].poll() is not None:
                raise RuntimeError('HAProxy ingress exited; inspect private logs')
        isaac = c['isaac']
        self.spawn('isaac', remote_command(c, ['bash', isaac['launcher']],
                   dict(isaac['env'], STREAM_PUBLIC_IP=facts['relay_ip'])))
        (self.directory / 'manifest.json').write_text(json.dumps({
            'mode': 'turn', 'sdk': '5.6.0', 'coturn': '4.18.0',
            'media_ssh': False, 'ice_password_api': False, 'stun_rewrite': False,
            'tcp_ingress': 'haproxy-source-bound' if ingress else 'platform-direct',
        }, indent=2))
        print(f'TURN network running; wait for Isaac readiness. Private logs: {self.directory}', flush=True)
        while not self.done.wait(1):
            self.update_status(runtime.is_file())
            if any(p.poll() is not None for p in self.children):
                raise RuntimeError('TURN/Isaac child exited; inspect private logs')

    def close(self):
        try:
            super().close()
        finally:
            if self.runtime_file is not None:
                self.runtime_file.unlink(missing_ok=True)
            if self.wrote_config:
                payload = dict(self.c['turn'], name=self.config_name)
                if self.c['turn'].get('tcp_ingress', True):
                    payload['ingress_name'] = self.ingress_name
                remote(self.c, 'remove', payload)


def viewer_config(c, address=None):
    t = c['turn']
    return {
        'turn': {'signalingServer': t['host'], 'signalingPort': int(t.get('signaling_port', 50239)),
                 'mediaServer': address or t.get('relay_ip'), 'mediaPort': 47998,
                 'iceServerConfiguration': {'iceTransportPolicy': 'relay', 'iceServers': [{
                     'urls': f"turn:{t['host']}:{int(t['port'])}?transport=tcp",
                     'username': t['username'], 'credential': t['password'],
                 }]}},
    }
