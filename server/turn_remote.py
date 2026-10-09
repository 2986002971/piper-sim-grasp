#!/usr/bin/env python3
"""Private stdin-only coturn configuration; executed in the Isaac container."""
import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
from pathlib import Path


def relay_ip(requested=None):
    addresses = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr'], text=True))
    available = [a['local'] for link in addresses for a in link.get('addr_info', [])
                 if a.get('scope') == 'global' and not ipaddress.ip_address(a['local']).is_loopback]
    if requested:
        if requested not in available:
            raise ValueError('Configured relay_ip is not assigned to this container')
        return requested
    route = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'get', '1.1.1.1'], text=True))[0]
    address = route.get('prefsrc')
    if address not in available:
        raise ValueError('Cannot determine container IPv4; configure turn.relay_ip')
    return address


def render(c):
    address = str(ipaddress.IPv4Address(c['relay_ip']))
    if ipaddress.ip_address(address).is_loopback:
        raise ValueError('Use the container interface IPv4, not loopback')
    for key in ['username', 'password']:
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', c[key]):
            raise ValueError('Use randomized alphanumeric TURN credentials')
    # Client transport is TCP; allocations toward Isaac are UDP. No external-ip:
    # the peer and allocations are in the same network namespace.
    listen_ip = str(ipaddress.IPv4Address(c.get('listen_ip', '127.0.0.1')))
    listen_port = int(c.get('listen_port', 6008))
    if not 1024 <= listen_port <= 65535:
        raise ValueError('Invalid TURN listening port')
    return '\n'.join([
        f'listening-ip={listen_ip}', f'listening-port={listen_port}', f'relay-ip={address}',
        'min-port=49152', 'max-port=49215', 'no-udp', 'no-tls', 'no-dtls', 'no-tcp-relay',
        'lt-cred-mech', 'realm=piper-trial', 'fingerprint',
        f"user={c['username']}:{c['password']}",
        'no-cli', 'no-multicast-peers', 'no-software-attribute',
        'denied-peer-ip=0.0.0.0-255.255.255.255', f'allowed-peer-ip={address}',
        'user-quota=4', 'total-quota=4', 'max-allocate-lifetime=1800',
        'log-file=stdout', 'simple-log', 'verbose',
    ]) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['inspect', 'write', 'remove'])
    args = p.parse_args()
    os.umask(0o077)
    c = json.load(__import__('sys').stdin)
    if args.action == 'inspect':
        for field in ['remote_worker']:
            if not Path(c[field]).is_file():
                raise ValueError('Missing remote ' + field)
        isaac = c['isaac']
        env = isaac['env']
        required = [isaac['launcher'], str(Path(env['ISAAC_ENV']) / 'bin/python'),
                    str(Path(env['PIPER_STREAM_ROOT']) / 'libnvenc_fix.so')]
        if env.get('PIPER_SCENE_USD'):
            required.append(env['PIPER_SCENE_USD'])
        else:
            required.append(str(Path(isaac['launcher']).parents[1] / 'models/prepare.py'))
        if any(not Path(path).is_file() for path in required):
            raise ValueError('Missing Isaac environment, launcher, NVENC filter or model input')
        address = relay_ip(c.get('relay_ip'))
        ports = {int(c.get('listen_port', 6008)), 6008, 6006}
        for protocol, port in [(socket.SOCK_STREAM, port) for port in ports] + [
                               (socket.SOCK_DGRAM, 47998)]:
            with socket.socket(socket.AF_INET, protocol) as sock:
                if protocol == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('0.0.0.0', port))
        version = subprocess.run([c['binary'], '--version'], capture_output=True, text=True, check=True).stdout.strip()
        if '4.18.0' not in version:
            raise ValueError('Expected pinned coturn 4.18.0')
        if c.get('ingress_name'):
            subprocess.run([c['haproxy_binary'], '-vv'], capture_output=True, text=True, check=True)
        print(json.dumps({'relay_ip': address, 'coturn_version': version}))
        return
    root = Path(c['config_root']).resolve()
    if not root.is_absolute() or root == Path('/'):
        raise ValueError('Invalid private config root')
    if not re.fullmatch(r'turn-[0-9a-f]{32}\.conf', c['name']):
        raise ValueError('Invalid owned configuration filename')
    path = root / c['name']
    if args.action == 'write':
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('x') as f:
            f.write(render(c))
        os.chmod(path, 0o600)
        if c.get('ingress_name'):
            if not re.fullmatch(r'haproxy-[0-9a-f]{32}\.cfg', c['ingress_name']):
                path.unlink(missing_ok=True)
                raise ValueError('Invalid owned HAProxy filename')
            frontend_port = int(c.get('frontend_port', 6008))
            backend_port = int(c.get('listen_port', 16010))
            address = str(ipaddress.IPv4Address(c['relay_ip']))
            if frontend_port != 6008 or backend_port != 16010:
                path.unlink(missing_ok=True)
                raise ValueError('Unexpected trial ingress ports')
            ingress = root / c['ingress_name']
            try:
                with ingress.open('x') as f:
                    f.write('\n'.join([
                        'global', '  maxconn 128', 'defaults', '  mode tcp',
                        '  timeout connect 5s', '  timeout client 75m', '  timeout server 75m',
                        'frontend autodl_turn_tcp', '  bind 127.0.0.1:6008',
                        '  default_backend coturn_tcp', 'backend coturn_tcp',
                        f'  server coturn {address}:16010 source {address}', '',
                    ]))
                os.chmod(ingress, 0o600)
            except BaseException:
                path.unlink(missing_ok=True)
                ingress.unlink(missing_ok=True)
                raise
    else:
        path.unlink(missing_ok=True)
        if c.get('ingress_name'):
            if not re.fullmatch(r'haproxy-[0-9a-f]{32}\.cfg', c['ingress_name']):
                raise ValueError('Invalid owned HAProxy filename')
            (root / c['ingress_name']).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
