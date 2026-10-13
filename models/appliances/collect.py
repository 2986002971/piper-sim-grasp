#!/usr/bin/env python3
"""Download pinned appliance assets; retain usable files rather than source ZIPs."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import struct
import urllib.request
import zipfile


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_glb(data):
    if len(data) < 20 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise ValueError('Invalid GLB header')
    chunks = {}
    offset = 12
    while offset < len(data):
        size, kind = struct.unpack_from('<II', data, offset)
        offset += 8
        if offset + size > len(data) or kind in chunks:
            raise ValueError('Invalid GLB chunks')
        chunks[kind] = data[offset:offset + size]
        offset += size
    return json.loads(chunks[0x4e4f534a]), chunks.get(0x004e4942, b'')


def normalize_glb(data):
    """Expand integer vertex attributes to float32; preserve transforms/animation.

    Trimesh 4.11.2 ignores normalized signed integer POSITION accessors. This
    expands KHR_mesh_quantization without changing the model's physical scale.
    """
    import numpy as np

    doc, binary = read_glb(data)
    if len(doc.get('buffers', [])) != 1 or 'uri' in doc['buffers'][0]:
        raise ValueError('Only self-contained single-buffer GLBs are supported')
    binary = bytearray(binary[:doc['buffers'][0]['byteLength']])
    ids = set()
    for mesh in doc.get('meshes', []):
        for primitive in mesh['primitives']:
            for attrs in [primitive['attributes'], *primitive.get('targets', [])]:
                ids.update(i for name, i in attrs.items() if not name.startswith('JOINTS_'))
    dtypes = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4'}
    dimensions = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}
    converted = []
    for i in sorted(ids):
        a = doc['accessors'][i]
        if a['componentType'] == 5126:
            continue
        if 'sparse' in a or a['type'] not in dimensions:
            raise ValueError('Unsupported sparse or matrix vertex accessor')
        view = doc['bufferViews'][a['bufferView']]
        if view['buffer'] != 0:
            raise ValueError('External buffer')
        dtype = np.dtype(dtypes[a['componentType']])
        width = dimensions[a['type']]
        stride = view.get('byteStride', width * dtype.itemsize)
        local = a.get('byteOffset', 0)
        end = local + max(0, a['count'] - 1) * stride + width * dtype.itemsize
        if a['count'] < 1 or stride < width * dtype.itemsize or end > view['byteLength']:
            raise ValueError('Accessor outside buffer view')
        values = np.ndarray((a['count'], width), dtype=dtype, buffer=binary,
                            offset=view.get('byteOffset', 0) + local,
                            strides=(stride, dtype.itemsize)).astype(np.float64)
        if a.get('normalized', False):
            limits = np.iinfo(dtype)
            values /= limits.max
            if limits.min < 0:
                values = np.maximum(values, -1.0)
        values = values.astype('<f4')
        del view
        # numpy's copy above owns its memory, so growing this buffer is safe.
        binary.extend(b'\0' * (-len(binary) % 4))
        start = len(binary)
        payload = values.tobytes()
        binary.extend(payload)
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': start,
                                  'byteLength': len(payload), 'target': 34962})
        a.update(bufferView=len(doc['bufferViews']) - 1, byteOffset=0, componentType=5126)
        a.pop('normalized', None)
        if 'min' in a:
            a['min'] = values.min(axis=0).tolist()
        if 'max' in a:
            a['max'] = values.max(axis=0).tolist()
        converted.append(i)
    for key in ('extensionsUsed', 'extensionsRequired'):
        if key in doc:
            doc[key] = [e for e in doc[key] if e != 'KHR_mesh_quantization']
            if not doc[key]:
                del doc[key]
    doc['buffers'][0]['byteLength'] = len(binary)
    encoded = json.dumps(doc, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    chunks = (struct.pack('<II', len(encoded), 0x4e4f534a) + encoded +
              struct.pack('<II', len(binary), 0x004e4942) + binary)
    return struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks, converted


def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=90) as response:
        data = response.read(150 * 1024 * 1024 + 1)
    if len(data) > 150 * 1024 * 1024:
        raise ValueError('Download exceeds size limit')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--seed-dir', type=Path, help='Previously downloaded source files')
    args = parser.parse_args()
    catalog = json.loads(Path(__file__).with_name('catalog.json').read_text())
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    assets = []
    for item in catalog['assets']:
        dest = root / 'sources' / item['id']
        dest.mkdir(parents=True, exist_ok=True)
        if item['format'] == 'zip':
            if args.offline:
                for name, digest in item['extracted_sha256'].items():
                    if sha256((dest / name).read_bytes()) != digest:
                        raise ValueError('Extracted source hash mismatch: ' + name)
                data = None
            else:
                seed = args.seed_dir / item['download_name'] if args.seed_dir else None
                data = seed.read_bytes() if seed and seed.is_file() else download(item['url'])
        else:
            existing = dest / item['download_name']
            seed = args.seed_dir / item['download_name'] if args.seed_dir else None
            if existing.is_file():
                data = existing.read_bytes()
            elif seed and seed.is_file():
                data = seed.read_bytes()
            elif args.offline:
                raise FileNotFoundError(existing)
            else:
                data = download(item['url'])
        if data is not None:
            if sha256(data) != item['sha256']:
                raise ValueError('Source SHA256 mismatch: ' + item['id'])
            if item['format'] == 'zip':
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for name, digest in item['extracted_sha256'].items():
                        p = PurePosixPath(name)
                        if p.is_absolute() or '..' in p.parts:
                            raise ValueError('Unsafe ZIP path')
                        payload = archive.read(name)
                        if sha256(payload) != digest:
                            raise ValueError('ZIP member hash mismatch: ' + name)
                        (dest / name).write_bytes(payload)
            else:
                existing.write_bytes(data)
        record = dict(item, source_dir=str(dest))
        if item['format'] == 'glb':
            output, converted = normalize_glb(data)
            normalized = root / 'normalized' / item['id'] / 'model.glb'
            normalized.parent.mkdir(parents=True, exist_ok=True)
            normalized.write_bytes(output)
            record.update(normalized_file=str(normalized), expanded_accessors=converted)
        assets.append(record)
        print(item['id'], flush=True)
    (root / 'inventory.json').write_text(json.dumps({'assets': assets}, ensure_ascii=False, indent=2) + '\n')
    (root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    for support in catalog.get('support_files', []):
        path = root / support['path']
        seed = args.seed_dir / support['download_name'] if args.seed_dir else None
        if path.is_file():
            payload = path.read_bytes()
        elif seed and seed.is_file():
            payload = seed.read_bytes()
        elif args.offline:
            raise FileNotFoundError(path)
        else:
            payload = download(support['url'])
        if sha256(payload) != support['sha256']:
            raise ValueError('License file hash mismatch')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


if __name__ == '__main__':
    main()
