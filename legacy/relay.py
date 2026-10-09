"""Optional Linux legacy relay."""
import socket
import ipaddress
import time
from pathlib import Path
from lifecycle import ProcessOwner, call, ssh, remote_command
HERE = Path(__file__).resolve().parent


def preflight(c):
    ipaddress.IPv4Address(c['client_lan_ip'])
    for key in ['ca_cert', 'token_file']:
        if not Path(c[key]).is_file():
            raise ValueError('Missing legacy ' + key)
    for port in [16006, 16008, 16009]:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((c['client_lan_ip'], 47998))
    if call(ssh(c, "ss -H -lntu 'sport = :6006 or sport = :6008 or sport = :47998'" )).strip():
        raise ValueError('Remote streaming ports already in use')


class RelayStack(ProcessOwner):
    def start(self):
        c = self.c
        preflight(c)
        s = c['isaac']
        self.spawn('isaac', remote_command(c, ['bash', s['launcher']],
                   dict(s['env'], STREAM_PUBLIC_IP=c['client_lan_ip'])))
        self.spawn('remote-relay', remote_command(c, ['python3', c['remote_relay'], 'server',
            '--bind', '127.0.0.1:6008', '--target', '127.0.0.1:47998',
            '--cert', c['remote_cert'], '--key', c['remote_key'], '--token-file', c['remote_token']]))
        self.spawn('media-ssh', ssh(c, '')[:-2] + ['-N', '-o', 'ExitOnForwardFailure=yes',
            '-L', '127.0.0.1:16008:127.0.0.1:6008', c['remote']])
        self.spawn('relay-client', ['python3', str(HERE / 'udp_tcp_tunnel.py'), 'client',
            '--remote', '127.0.0.1:16008', '--udp-bind', c['client_lan_ip'] + ':47998',
            '--ca-cert', c['ca_cert'], '--token-file', c['token_file']])
        # Signaling is a separate SSH process in both modes.
        self.spawn('signal-ssh', ssh(c, '')[:-2] + ['-N', '-o', 'ExitOnForwardFailure=yes',
            '-L', '127.0.0.1:16006:127.0.0.1:6006', c['remote']])
        self.wait_for(lambda: all(p.poll() is None for p in self.children))
        time.sleep(1)
        if any(p.poll() is not None for p in self.children):
            raise RuntimeError('Startup process failed; inspect logs')
        print(f'Legacy relay running; logs: {self.directory}', flush=True)
        while not self.done.wait(1):
            self.update_status()
            if any(p.poll() is not None for p in self.children):
                raise RuntimeError('Legacy relay child exited; inspect logs')
