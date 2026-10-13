#!/usr/bin/env python3
"""Inspect collected geometry, dependencies and authored physics, without simulation."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics, UsdUtils

from collect import read_glb


def inspect_visual(item, preview_dir):
    is_glb = item['format'] == 'glb'
    path = Path(item['normalized_file']) if is_glb else next(Path(item['source_dir']).glob('*.obj'))
    scene = trimesh.load(path, force='scene', process=False)
    if not np.isfinite(scene.bounds).all():
        raise ValueError('Non-finite bounds')
    report = {'file': str(path), 'native_bounds': scene.bounds.tolist(),
              'native_dimensions_xyz': scene.extents.tolist(),
              'triangles_unique_geometry': sum(len(m.faces) for m in scene.geometry.values()),
              'geometry_names': list(scene.geometry), 'runtime_validated': False}
    if is_glb:
        doc, _ = read_glb(path.read_bytes())
        report.update(nodes=[{'index': i, 'name': n.get('name'), 'mesh': n.get('mesh'),
                             'children': n.get('children', [])} for i, n in enumerate(doc['nodes'])],
                      animations=[{'name': a.get('name'), 'targets': [c['target'] for c in a['channels']]}
                                  for a in doc.get('animations', [])],
                      images=len(doc.get('images', [])), materials=len(doc.get('materials', [])),
                      dimensions_m=scene.extents.tolist(),
                      physics_joints=0, collision_geometry_authored=False)
    else:
        report.update(unit_scale_to_m=None, physics_joints=0, collision_geometry_authored=False,
                      proposed_unit_scale_to_m=0.01,
                      proposed_dimensions_m=(scene.extents * 0.01).tolist(),
                      scale_status='centimeters inferred from appliance dimensions; not applied')
        # OBJ material textures must resolve from their original relative paths.
        refs = []
        for mtl in Path(item['source_dir']).glob('*.mtl'):
            for line in mtl.read_text(errors='replace').splitlines():
                if line.startswith(('map_Kd ', 'map_Ka ', 'map_Ks ', 'map_Bump ', 'bump ')):
                    target = mtl.parent / line.split(maxsplit=1)[1].strip()
                    if not target.is_file():
                        raise FileNotFoundError(target)
                    refs.append(str(target))
        report['resolved_texture_files'] = sorted(set(refs))
    if preview_dir:
        triangles, colors = [], []
        for node in scene.graph.nodes_geometry:
            transform, geometry = scene.graph[node]
            mesh = scene.geometry[geometry]
            points = trimesh.transform_points(mesh.vertices, transform)
            points = points[:, [0, 2, 1]]  # display Y-up as Z-up
            triangles.append(points[mesh.faces])
            try:
                color = np.asarray(mesh.visual.material.main_color)[:3] / 255
            except AttributeError:
                color = np.array([0.7, 0.7, 0.75])
            colors.append(np.tile(color, (len(mesh.faces), 1)))
        preview_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(preview_dir / (item['id'] + '.npz'),
                            triangles=np.concatenate(triangles), colors=np.concatenate(colors))
    return report


def inspect_usd(item):
    path = Path(item['source_dir']) / item['download_name']
    stage = Usd.Stage.Open(str(path))
    if not stage or not stage.GetDefaultPrim():
        raise ValueError('Missing USD stage/default prim')
    missing = UsdUtils.ComputeAllDependencies(str(path))[2]
    if missing:
        raise ValueError('Unresolved USD assets: ' + str(missing))
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render', 'proxy']).ComputeWorldBound(
        stage.GetDefaultPrim()).ComputeAlignedRange()
    report = {'file': str(path), 'units_m': UsdGeom.GetStageMetersPerUnit(stage),
              'up_axis': str(UsdGeom.GetStageUpAxis(stage)),
              'dimensions_m': list(box.GetSize() * UsdGeom.GetStageMetersPerUnit(stage)),
              'unresolved_dependencies': [], 'runtime_validated': False,
              'joints': [], 'rigid_bodies': [], 'colliders': [], 'physics_materials': []}
    for prim in stage.Traverse():
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            targets = [str(p) for rel in [joint.GetBody0Rel(), joint.GetBody1Rel()] for p in rel.GetTargets()]
            for target in targets:
                if not stage.GetPrimAtPath(target).HasAPI(UsdPhysics.RigidBodyAPI):
                    raise ValueError('Joint body lacks rigid body API: ' + target)
            report['joints'].append({'path': str(prim.GetPath()), 'type': prim.GetTypeName(),
                                    'body_targets': targets,
                                    'lower_limit': prim.GetAttribute('physics:lowerLimit').Get(),
                                    'upper_limit': prim.GetAttribute('physics:upperLimit').Get()})
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            values = {}
            for name in ['mass', 'density', 'diagonalInertia', 'centerOfMass', 'principalAxes']:
                attr = prim.GetAttribute('physics:' + name)
                values[name] = {'authored': bool(attr and attr.HasAuthoredValueOpinion()),
                                'value': str(attr.Get()) if attr else None}
            report['rigid_bodies'].append({'path': str(prim.GetPath()), 'mass_properties': values})
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            api = UsdPhysics.MeshCollisionAPI(prim)
            report['colliders'].append({'path': str(prim.GetPath()),
                                       'approximation': str(api.GetApproximationAttr().Get()) if api else None})
        if prim.HasAPI(UsdPhysics.MaterialAPI):
            report['physics_materials'].append(str(prim.GetPath()))
    report['physics_joints'] = len(report['joints'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--preview-data', type=Path)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    inventory = json.loads((root / 'inventory.json').read_text())
    reports = []
    for item in inventory['assets']:
        report = inspect_usd(item) if item['format'] == 'usdz' else inspect_visual(item, args.preview_data)
        reports.append(dict(id=item['id'], label_zh=item['label_zh'], **report))
        print(item['id'], 'OK', flush=True)
    (root / 'inspection.json').write_text(json.dumps({'assets': reports}, ensure_ascii=False, indent=2) + '\n')
    files = [p for p in root.rglob('*') if p.is_file() and p.name != 'SHA256SUMS.json']
    checksums = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    (root / 'SHA256SUMS.json').write_text(json.dumps(checksums, indent=2) + '\n')
    print('Verified', len(reports), 'assets;', len(files), 'files;', sum(p.stat().st_size for p in files), 'bytes')


if __name__ == '__main__':
    main()
