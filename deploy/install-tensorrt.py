#!/usr/bin/env python3
"""Deploy NVIDIA's dynamic SDK from versions.lock.json in a private data prefix.

Use the official binary packages to avoid downloading the tar's static SDK.
This does not register packages with dpkg or alter the system linker config.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

LOCK = json.loads((Path(__file__).resolve().parents[1]/'config/versions.lock.json').read_text())
VERSION = LOCK['tensorrt']+'-1+cuda'+LOCK['cuda']
BASE = 'https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/'
PACKAGES = ('libnvinfer10', 'libnvinfer-headers-dev', 'libnvinfer-headers-plugin-dev',
            'libnvinfer-plugin10', 'libnvonnxparsers10', 'libnvinfer-lean10',
            'libnvinfer-dispatch10', 'libnvinfer-vc-plugin10', 'libnvinfer-bin')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', required=True, type=Path)
    args = parser.parse_args()
    if platform.machine() != 'x86_64':
        raise RuntimeError('This SDK targets x86_64')
    prefix = args.prefix.resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    marker = prefix / 'sdk.json'
    if not marker.exists():
        download = prefix / 'downloads'
        download.mkdir(exist_ok=True)
        receipt = prefix / 'installed-packages.json'
        installed = json.loads(receipt.read_text()) if receipt.exists() else []
        for package in PACKAGES:
            if package in installed:
                continue
            filename = f'{package}_{VERSION}_amd64.deb'
            archive = download / filename
            print(f'Downloading {package} {VERSION}', flush=True)
            subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                            '--retry', '4', '--retry-all-errors', '--connect-timeout', '15',
                            '--speed-time', '60', '--speed-limit', '1024', '--max-time', '1800',
                            '--continue-at', '-', '--output', str(archive),
                            BASE + filename], check=True)
            actual = subprocess.check_output(['dpkg-deb', '-f', str(archive), 'Version'], text=True).strip()
            if actual != VERSION:
                raise RuntimeError(f'Unexpected SDK package version: {actual}')
            subprocess.run(['dpkg-deb', '-x', str(archive), str(prefix)], check=True)
            archive.unlink()
            installed.append(package)
            receipt.write_text(json.dumps(installed) + '\n')
        library = prefix / 'usr/lib/x86_64-linux-gnu'
        link = library / 'libnvinfer.so'
        if not link.is_symlink():
            link.symlink_to('libnvinfer.so.10')
        marker.write_text(json.dumps({'version': LOCK['tensorrt'], 'cuda': LOCK['cuda'],
                                     'packages': list(PACKAGES), 'source': BASE}, indent=2) + '\n')
    if json.loads(marker.read_text())['version'] != LOCK['tensorrt']:
        raise RuntimeError('Unexpected existing TensorRT SDK')
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(prefix / 'usr/lib/x86_64-linux-gnu') + ':' + env.get('LD_LIBRARY_PATH', '')
    executable = prefix / 'usr/src/tensorrt/bin/trtexec'
    subprocess.run([str(executable), '--help'], stdout=subprocess.DEVNULL, env=env, check=True)
    print(f'TensorRT dynamic SDK ready: {prefix}', flush=True)


if __name__ == '__main__':
    main()
