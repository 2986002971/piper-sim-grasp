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
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.tensorrt_resources import LOCK, VERSION, BASE, download as download_package, prune_builder_resources
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
            download_package(package, archive)
            subprocess.run(['dpkg-deb', '-x', str(archive), str(prefix)], check=True)
            prune_builder_resources(prefix)
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
    prune_builder_resources(prefix)
    metadata = json.loads(marker.read_text())
    metadata['layout'] = 'runtime-only'
    marker.write_text(json.dumps(metadata, indent=2)+'\n')
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(prefix / 'usr/lib/x86_64-linux-gnu') + ':' + env.get('LD_LIBRARY_PATH', '')
    executable = prefix / 'usr/src/tensorrt/bin/trtexec'
    subprocess.run([str(executable), '--help'], stdout=subprocess.DEVNULL, env=env, check=True)
    print(f'TensorRT runtime SDK ready (builder resources are temporary): {prefix}', flush=True)


if __name__ == '__main__':
    main()
