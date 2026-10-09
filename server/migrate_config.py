#!/usr/bin/env python3
"""Split private legacy configuration, preserving credentials only in mode-0600 files."""
import argparse
import json
import os
from pathlib import Path
from manage import private_write
from turn import viewer_config

SERVER_FIELDS = ('remote', 'ssh_port', 'raw_dir', 'remote_worker', 'isaac', 'turn')
LEGACY_FIELDS = ('client_lan_ip', 'remote_relay', 'remote_cert', 'remote_key', 'remote_token', 'ca_cert', 'token_file')


def split(old, runtime=None, include_legacy=False, relay_ip=None):
    server = {key: old[key] for key in SERVER_FIELDS if key in old}
    for key in SERVER_FIELDS:
        if key not in server:
            raise ValueError('Missing legacy server field: ' + key)
    server['turn'] = dict(server['turn'], tcp_ingress=server['turn'].get('tcp_ingress', True))
    client = runtime or viewer_config(server, relay_ip or server['turn'].get('relay_ip'))
    # Select exported fields rather than pass the complete management document.
    client = {'turn': {key: client['turn'][key] for key in
                      ('signalingServer', 'signalingPort', 'mediaServer', 'mediaPort', 'iceServerConfiguration')}}
    if include_legacy:
        server.update({key: old[key] for key in LEGACY_FIELDS})
        client['relay'] = {'signalingServer': '127.0.0.1', 'signalingPort': 16006,
                           'mediaServer': old['client_lan_ip'], 'mediaPort': 47998}
        server['legacy_client'] = client['relay']
    return server, client


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--server', type=Path, required=True)
    p.add_argument('--client', type=Path)
    p.add_argument('--runtime', type=Path)
    p.add_argument('--relay-ip', help='Detected container address, not browser LAN address')
    p.add_argument('--include-legacy', action='store_true')
    args = p.parse_args()
    os.umask(0o077)
    old = json.loads(args.source.read_text())
    runtime = json.loads(args.runtime.read_text()) if args.runtime else None
    server, client = split(old, runtime, args.include_legacy, args.relay_ip)
    if args.client and not client['turn'].get('mediaServer'):
        p.error('Client export needs --runtime or --relay-ip; alternatively start new stack then export-client')
    private_write(args.server, server)
    if args.client:
        private_write(args.client, client)
    print('Private configuration migrated; update remote code paths before preflight.')


if __name__ == '__main__':
    main()
