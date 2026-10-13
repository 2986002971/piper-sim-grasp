"""Container IPv4 discovery and coturn configuration for the session manager."""
import ipaddress
import json
import re
import subprocess


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
        raise ValueError('Cannot determine container IPv4 from interfaces and default route')
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

