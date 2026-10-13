#!/usr/bin/env python3
"""Check appliance authored properties, hinge frames, dependencies, and cavity access."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from scipy.spatial import ConvexHull
from pxr import Usd, UsdGeom, UsdPhysics, UsdUtils, Gf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_physics import usd_geometry


def validate(path):
    stage = Usd.Stage.Open(str(path))
    if not stage or UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != 'Z':
        raise ValueError('Invalid stage units or up axis')
    missing = UsdUtils.ComputeAllDependencies(str(path))[2]
    if missing:
        raise ValueError('Missing dependencies: ' + str(missing))
    cache = UsdGeom.XformCache()
    joints, masses, colliders = [], [], []
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            api = UsdPhysics.MassAPI(prim)
            mass = api.GetMassAttr().Get(); center = np.array(api.GetCenterOfMassAttr().Get())
            inertia = np.array(api.GetDiagonalInertiaAttr().Get())
            if not np.isfinite(np.r_[mass, center, inertia]).all() or mass <= 0 or inertia.min() <= 0:
                raise ValueError('Invalid mass/inertia: ' + str(prim.GetPath()))
            if inertia.max() > inertia.sum() - inertia.max() + 1e-6:
                raise ValueError('Inertia triangle inequality violated')
            masses.append({'path': str(prim.GetPath()), 'mass_kg': mass})
        if prim.IsA(UsdPhysics.Joint):
            api = UsdPhysics.Joint(prim)
            positions, axes = [], []
            axis_name = prim.GetAttribute('physics:axis').Get()
            axis = Gf.Vec3d(*(np.eye(3)['XYZ'.index(axis_name)])) if axis_name else None
            for n in [0, 1]:
                targets = prim.GetRelationship('physics:body' + str(n)).GetTargets()
                matrix = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(targets[0])) if targets else Gf.Matrix4d(1)
                position = prim.GetAttribute('physics:localPos' + str(n)).Get()
                positions.append(np.array(matrix.Transform(Gf.Vec3d(position))))
                if axis is not None:
                    rotation = prim.GetAttribute('physics:localRot' + str(n)).Get()
                    local = Gf.Rotation(Gf.Quatd(rotation)).TransformDir(axis)
                    world = np.array(matrix.TransformDir(local)); axes.append(world / np.linalg.norm(world))
            error = float(np.linalg.norm(positions[0] - positions[1]))
            dot = float(np.dot(*axes)) if axes else 1
            if error > 0.0001 or dot < 0.99999:
                raise ValueError('Misaligned initial hinge frame: ' + str(prim.GetPath()))
            joints.append({'path': str(prim.GetPath()), 'anchor_error_m': error, 'axis_dot': dot,
                           'world_anchor_m': positions[0].tolist(),
                           'world_axis': axes[0].tolist() if axes else None})
        if prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
            if not prim.IsA(UsdGeom.Mesh):
                raise ValueError('Expected explicit collision meshes')
            mesh = usd_geometry(prim, stage.GetDefaultPrim(), cache)
            if not mesh.is_volume or len(mesh.vertices) > 128:
                raise ValueError('Invalid authored convex proxy: ' + str(prim.GetPath()))
            colliders.append((str(prim.GetPath()), mesh))
    if path.name.startswith('washer'):
        probes = np.array([[0, y, 0.42] for y in [-0.5, -0.35, -0.295, -0.2, 0]])
        ignore = ('/Door/', '/Drawer/')
    else:
        probes = np.array([[0, y, z] for y in [-0.4, -0.25, 0, 0.15] for z in [0.30, 0.50, 0.70]])
        ignore = ('/door_pivot_001/', '/button_', '/detergent_lid_')
    blocked = np.zeros(len(probes), dtype=bool)
    for name, mesh in colliders:
        if any(t in name for t in ignore):
            continue
        planes = ConvexHull(mesh.vertices).equations
        blocked |= (probes @ planes[:, :3].T + planes[:, 3] < -1e-7).all(axis=1)
    if blocked.any():
        raise ValueError('Interior access blocked: ' + str(probes[blocked].tolist()))
    return {'file': str(path), 'passed': True, 'rigid_bodies': masses,
            'total_mass_kg': sum(m['mass_kg'] for m in masses), 'joints': joints,
            'max_initial_anchor_error_m': max(j['anchor_error_m'] for j in joints),
            'convex_collider_count': len(colliders), 'cavity_points_m': probes.tolist(),
            'cavity_blocked_points': int(blocked.sum()),
            'scope': 'offline authored geometry and mass checks; door intentionally excluded from cavity probes'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    results = [validate(args.directory / name) for name in ['dishwasher.usda', 'washer.usda']]
    report = {'all_passed': True, 'assets': results}
    (args.directory / 'validation_offline.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps([{'file': r['file'], 'mass_kg': r['total_mass_kg'],
                      'convex_parts': r['convex_collider_count'],
                      'hinge_anchor_error_m': r['max_initial_anchor_error_m']} for r in results]), flush=True)


if __name__ == '__main__':
    main()
