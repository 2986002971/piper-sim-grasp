#!/usr/bin/env python3
"""Optional offline scene export with relative paths and a dependency manifest."""
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


def material_paths(materials, output):
    """Keep custom MDL directories intact: USD cannot enumerate their imports.

    A material directory is one dependency unit, including templates, textures
    and notices. Preserve module filenames and relative paths instead of
    prefixing individual MDL files with hashes.
    """
    parents = {Path(path).resolve().parent for path in materials}
    roots = sorted(p for p in parents if not any(p != q and p.is_relative_to(q) for q in parents))
    names = {}
    for root in roots:
        if Path(output).resolve().is_relative_to(root):
            raise ValueError('Export output must be outside custom MDL directories: '+str(root))
        prefix = 'materials/m_'+hashlib.sha256(str(root).encode()).hexdigest()[:16]
        for path in sorted(root.rglob('*')):
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError('Material dependency escapes its directory: '+str(path))
                names[path.resolve()] = prefix+'/'+str(path.relative_to(root))
    return names


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
        if p.suffix != '.mdl': return False
        if p.is_absolute():
            return any(p.resolve().is_relative_to(folder.resolve()) for folder in search)
        return any((folder/p).is_file() for folder in search)
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
    names.update(material_paths([p for p in files if p.suffix == '.mdl'], output))
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
                resolved = Path(Sdf.ComputeAssetPathRelativeToLayer(source, value)).resolve()
                if resolved in names:
                    return os.path.relpath(work/names[resolved], (work/names[old]).parent)
                if builtin(value): return Path(value).name
                raise ValueError(f'Unbundled reference in {old}: {value}')
            UsdUtils.ModifyAssetPaths(target, rewrite)
            target.Save()
        relocated_layers, relocated_assets, missing = UsdUtils.ComputeAllDependencies(str(work/'scene.usda'))
        if missing:
            raise ValueError(f'Exported scene has unresolved assets: {missing}')
        for path in [layer.realPath for layer in relocated_layers]+list(relocated_assets):
            if not Path(path).resolve().is_relative_to(work) and not builtin(path):
                raise ValueError('Exported scene depends on an external file: '+str(path))
        for tree in trees.values():
            for mesh in tree.iter('mesh'):
                mesh.set('filename', names[Path(mesh.get('filename'))])
        for source, name in ((robot_urdf, 'robot.urdf'), (assembly_urdf, 'assembly.urdf')):
            trees[source].write(work/name, encoding='utf-8', xml_declaration=True)
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
