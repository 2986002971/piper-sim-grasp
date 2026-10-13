#!/usr/bin/env python3
"""Relocatable scene bundles. Export uses Isaac's OpenUSD; import is stdlib-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''): result.update(block)
    return result.hexdigest()


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root/'bundle.json').read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported asset bundle schema')
    for name, expected in manifest['files'].items():
        path = root/name
        if not path.resolve().is_relative_to(root) or not path.is_file() or digest(path) != expected:
            raise ValueError(f'Asset missing, outside bundle, or corrupt: {name}')
    for field in ('scene', 'robot_urdf', 'assembly_urdf', 'scene_metadata'):
        if manifest[field] not in manifest['files']:
            raise ValueError(f'Unverified bundle entry: {field}')
    return manifest


def import_bundle(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    identity = digest(archive)
    target = destination/identity[:20]
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=destination, prefix='.import-') as work:
            work = Path(work)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if not (member.isfile() or member.isdir()) or not (work/member.name).resolve().is_relative_to(work):
                        raise ValueError('Bundle contains an unsafe archive entry')
                tar.extractall(work)
            verify(work)
            work.rename(target)
    manifest = verify(target)
    temporary = destination/'current.json.tmp'
    temporary.write_text(json.dumps({'directory': target.name, 'sha256': identity})+'\n')
    temporary.replace(destination/'current.json')
    return target, manifest


def export_bundle(scene, robot_urdf, assembly_urdf, scene_metadata, output):
    from pxr import Sdf, UsdUtils
    scene, robot_urdf, assembly_urdf, scene_metadata, output = map(
        lambda x: Path(x).resolve(), (scene, robot_urdf, assembly_urdf, scene_metadata, output))
    if output.exists():
        raise FileExistsError(output)
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(scene))
    search = [Path(p) for p in os.environ.get('PXR_AR_DEFAULT_SEARCH_PATH', '').split(':') if p]
    def builtin(path):
        p = Path(path)
        return p.suffix == '.mdl' and any((folder/p.name).is_file() for folder in search)
    missing = [p for p in unresolved if not builtin(p)]
    if missing:
        raise ValueError(f'Unresolved scene assets: {missing}')
    files = {Path(layer.realPath).resolve() for layer in layers}
    # Installed MDL modules belong to the pinned Isaac distribution.
    builtins = {str(p) for p in assets if builtin(p)} | set(unresolved)
    files.update(Path(p).resolve() for p in assets if not builtin(p))
    trees = {}
    for urdf in (robot_urdf, assembly_urdf):
        tree = ET.parse(urdf)
        for mesh in tree.iter('mesh'):
            name = mesh.get('filename')
            if name.startswith('package://'):
                raise ValueError(f'Export needs a resolved URDF mesh: {name}')
            path = Path(unquote(urlparse(name).path) if name.startswith('file://') else name)
            path = (urdf.parent/path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file(): raise FileNotFoundError(path)
            files.add(path)
            mesh.set('filename', str(path))
        trees[urdf] = tree
    names = {path: 'files/'+hashlib.sha256(str(path).encode()).hexdigest()[:16]+'-'+path.name for path in files}
    names[scene] = 'scene.usda'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.export-') as work:
        work = Path(work)
        for source, name in names.items():
            target = work/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for source in layers:
            old = Path(source.realPath).resolve()
            target = Sdf.Layer.FindOrOpen(str(work/names[old]))
            def rewrite(value):
                if not value: return value
                if builtin(value): return Path(value).name
                resolved = Path(Sdf.ComputeAssetPathRelativeToLayer(source, value)).resolve()
                if resolved not in names:
                    raise ValueError(f'Unbundled reference in {old}: {value}')
                return os.path.relpath(work/names[resolved], (work/names[old]).parent)
            UsdUtils.ModifyAssetPaths(target, rewrite)
            target.Save()
        for source, name in ((robot_urdf, 'robot.urdf'), (assembly_urdf, 'assembly.urdf')):
            tree = trees[source]
            for mesh in tree.iter('mesh'):
                mesh.set('filename', names[Path(mesh.get('filename'))])
            tree.write(work/name, encoding='utf-8', xml_declaration=True)
        shutil.copy2(scene_metadata, work/'scene.json')
        notices = Path(__file__).resolve().parents[1]/'THIRD_PARTY_NOTICES.md'
        shutil.copy2(notices, work/notices.name)
        versions = json.loads((Path(__file__).resolve().parents[1]/'config/versions.lock.json').read_text())
        manifest = dict(schema_version=1, scene='scene.usda', robot_urdf='robot.urdf',
                        assembly_urdf='assembly.urdf', scene_metadata='scene.json',
                        isaac=versions['isaac'], installed_materials=sorted(builtins),
                        source_scene_sha256=digest(scene),
                        files={str(p.relative_to(work)): digest(p) for p in sorted(work.rglob('*')) if p.is_file()})
        (work/'bundle.json').write_text(json.dumps(manifest, indent=2)+'\n')
        verify(work)
        with tarfile.open(output, 'w:gz') as tar:
            for path in sorted(work.rglob('*')):
                if path.is_file(): tar.add(path, arcname=str(path.relative_to(work)))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--robot-urdf', required=True)
    parser.add_argument('--assembly-urdf', required=True)
    parser.add_argument('--scene-metadata', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(export_bundle(args.scene, args.robot_urdf, args.assembly_urdf, args.scene_metadata, args.output))


if __name__ == '__main__': main()
