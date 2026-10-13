#!/usr/bin/env python3
"""Collect pinned public object meshes; audit inputs without claiming physics readiness."""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def safe_path(root, name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('Unsafe archive/reference path: ' + name)
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes asset directory: ' + name)
    return target


def extract(archive, kind, destination, include_paths=None):
    """Reject traversal, links, devices and oversized payloads before extraction."""
    total = 0
    seen = set()
    if kind == 'zip':
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = safe_path(destination, member.filename)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('Archive contains a symlink')
                if member.is_dir():
                    continue
                if include_paths is not None and member.filename not in include_paths:
                    continue
                total += member.file_size
                if total > MAX_EXPANDED_BYTES or target in seen:
                    raise ValueError('Oversized archive or duplicate member')
                seen.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    elif kind == 'tar.gz':
        with tarfile.open(archive, 'r:gz') as bundle:
            for member in bundle:
                target = safe_path(destination, member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError('Archive contains a link or special file')
                if include_paths is not None and member.name not in include_paths:
                    continue
                total += member.size
                if total > MAX_EXPANDED_BYTES or target in seen:
                    raise ValueError('Oversized archive or duplicate member')
                seen.add(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    else:
        raise ValueError('Unsupported archive: ' + kind)


def obj_audit(path, asset_root):
    minimum = [math.inf] * 3
    maximum = [-math.inf] * 3
    counts = {'vertices': 0, 'faces': 0, 'texture_coordinates': 0}
    libraries = []
    with path.open(errors='strict') as stream:
        for line in stream:
            words = line.split()
            if not words:
                continue
            if words[0] == 'v':
                xyz = [float(x) for x in words[1:4]]
                if len(xyz) != 3 or not all(math.isfinite(x) for x in xyz):
                    raise ValueError('Invalid OBJ vertex: ' + str(path))
                counts['vertices'] += 1
                minimum = [min(a, b) for a, b in zip(minimum, xyz)]
                maximum = [max(a, b) for a, b in zip(maximum, xyz)]
            elif words[0] == 'f':
                if len(words) < 4:
                    raise ValueError('Invalid OBJ face: ' + str(path))
                counts['faces'] += 1
            elif words[0] == 'vt':
                counts['texture_coordinates'] += 1
            elif words[0] == 'mtllib':
                libraries.append(line.strip()[7:].strip())
    if not counts['vertices'] or not counts['faces'] or not libraries:
        raise ValueError('OBJ lacks geometry or material library: ' + str(path))
    textures = []
    for name in libraries:
        # ../ is legitimate for MTL texture references, provided it stays in this asset.
        material = (path.parent / name).resolve()
        if not material.is_relative_to(asset_root.resolve()) or not material.is_file():
            raise ValueError('Missing/escaping OBJ material: ' + name)
        for line in material.read_text().splitlines():
            words = line.split()
            if words and words[0] in ('map_Kd', 'map_Ks', 'map_Bump', 'bump', 'map_d'):
                name = line.strip().split(maxsplit=1)[1].strip()
                texture = (material.parent / name).resolve()
                if not texture.is_relative_to(asset_root.resolve()) or not texture.is_file():
                    raise ValueError('Missing/escaping MTL texture: ' + name)
                textures.append(str(texture.relative_to(asset_root.resolve())))
    return {**counts, 'aabb_mesh_units': {'min': minimum, 'max': maximum},
            'extent_mesh_units': [b - a for a, b in zip(minimum, maximum)],
            'textures': sorted(set(textures)), 'topology_validated': False}


def normalize_visual(row, destination):
    """Fix explicitly catalogued upstream MTL paths in a separate derived copy."""
    remap = row.get('texture_remap', {})
    if not remap:
        return row['visual_mesh'], []
    original = safe_path(destination, row['visual_mesh'])
    normalized = safe_path(destination, 'normalized/' + row['visual_mesh'])
    normalized.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, normalized)
    repairs = []
    libraries = [line.strip()[7:].strip() for line in original.read_text().splitlines()
                 if line.startswith('mtllib ')]
    for name in libraries:
        material = (original.parent / name).resolve()
        derived = (normalized.parent / name).resolve()
        if not all(p.is_relative_to(destination.resolve()) for p in (material, derived)):
            raise ValueError('Material path escapes asset')
        lines = []
        for line in material.read_text().splitlines():
            words = line.split(maxsplit=1)
            if len(words) == 2 and words[0].startswith('map_') and words[1].strip() in remap:
                target = safe_path(destination, remap[words[1].strip()])
                if not target.is_file():
                    raise ValueError('Missing catalogued texture: ' + str(target))
                replacement = os.path.relpath(target, derived.parent)
                repairs.append({'material': str(material.relative_to(destination)),
                                'original_reference': words[1].strip(), 'normalized_reference': replacement})
                line = words[0] + ' ' + replacement
            lines.append(line)
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text('\n'.join(lines) + '\n')
    return str(normalized.relative_to(destination)), repairs


def audit_object(row, destination):
    for name in row['required_files']:
        if not safe_path(destination, name).is_file():
            raise ValueError('Missing required asset file: ' + name)
    visual, repairs = normalize_visual(row, destination)
    audit = obj_audit(safe_path(destination, visual), destination)
    audit['texture_reference_repairs'] = repairs
    audit['extent_m_by_declared_scale'] = [x * row['unit_scale_to_m'] for x in audit['extent_mesh_units']]
    sdf = destination / 'model.sdf'
    if sdf.is_file():
        root = ET.parse(sdf).getroot()
        audit['sdf_inertials'] = [ET.tostring(node, encoding='unicode') for node in root.findall('.//inertial')]
        audit['sdf_collision_meshes'] = [node.text for node in root.findall('.//collision/geometry/mesh/uri')]
        audit['sdf_surface_parameters'] = [ET.tostring(node, encoding='unicode') for node in root.findall('.//surface')]
    return {'id': row['id'], 'label_zh': row['label_zh'], 'category': row['category'],
            'visual_mesh': visual, 'source_visual_mesh': row['visual_mesh'],
            'unit_status': row['unit_status'], 'physics': row['physics'], 'audit': audit,
            'status': 'source_collected_not_simulation_validated'}


def collect_bundle(bundle, data, offline=False):
    source = bundle['source']
    expected = source['sha256']
    if not expected or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Source is not hash-pinned: ' + bundle['id'])
    suffix = '.zip' if source['archive'] == 'zip' else '.tgz'
    archive = data / 'archives' / (bundle['id'] + suffix)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or digest(archive) != expected:
        if offline:
            raise ValueError('Missing/corrupt offline archive: ' + bundle['id'])
        temporary = archive.with_suffix(archive.suffix + '.partial')
        try:
            request = urllib.request.Request(source['url'], headers={'User-Agent': 'PiperSimGrasp-ObjectCollector/1.0'})
            total = 0
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('Archive exceeds download limit')
                    output.write(chunk)
            if digest(temporary) != expected:
                raise ValueError('Source checksum mismatch: ' + bundle['id'])
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    parent = data / 'sources'
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / bundle['id']
    # Re-extract from the checked archive even offline; no stale output is trusted.
    with tempfile.TemporaryDirectory(prefix=bundle['id'] + '-', dir=parent) as staging:
        staging = Path(staging)
        extract(archive, source['archive'], staging, bundle.get('include_paths'))
        objects = [audit_object(row, staging) for row in bundle['objects']]
        files = {str(p.relative_to(staging)): digest(p) for p in sorted(staging.rglob('*')) if p.is_file()}
        provenance = {'id': bundle['id'], 'source': source, 'archive_bytes': archive.stat().st_size,
                      'files': files, 'objects': objects}
        save_json(staging / 'provenance.json', provenance)
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    print('Collected ' + bundle['id'], flush=True)
    return {'bundle_id': bundle['id'], 'source': source, 'archive_bytes': archive.stat().st_size,
            'source_directory': str(destination), 'objects': objects}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--catalog', type=Path, default=HERE / 'catalog.json')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--only', nargs='+', help='Bundle IDs; list them with --list')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    if args.list:
        for bundle in catalog['bundles']:
            print(bundle['id'] + ': ' + ', '.join(row['label_zh'] for row in bundle['objects']))
        return
    if args.data_dir is None:
        parser.error('--data-dir is required unless --list is used')
    data = args.data_dir.expanduser().resolve()
    if data == Path('/') or data.is_relative_to(ROOT) or ROOT.is_relative_to(data):
        parser.error('Use a dedicated data directory outside the source checkout')
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be between 1 and 8')
    bundles = catalog['bundles']
    if args.only:
        unknown = set(args.only) - {b['id'] for b in bundles}
        if unknown:
            parser.error('Unknown bundle IDs: ' + ', '.join(sorted(unknown)))
        bundles = [b for b in bundles if b['id'] in args.only]
    if len({b['id'] for b in bundles}) != len(bundles):
        parser.error('Duplicate bundle IDs')
    for bundle in bundles:
        if not re.fullmatch('[A-Za-z0-9_-]+', bundle['id']):
            parser.error('Unsafe bundle ID')
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'collection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inventory = data / 'inventory.json'
        # A failed run must not leave a previous inventory looking like this run succeeded.
        inventory.unlink(missing_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(lambda b: collect_bundle(b, data, args.offline), bundles))
        save_json(inventory, {'schema_version': 1, 'catalog_sha256': digest(args.catalog),
                             'goal_zh': catalog['goal_zh'], 'pending': catalog['pending'],
                             'bundle_count': len(results),
                             'object_count': sum(len(r['objects']) for r in results),
                             'physics_ready': False, 'bundles': results})
    print('Inventory: ' + str(inventory))


if __name__ == '__main__':
    main()
