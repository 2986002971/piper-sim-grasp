#!/usr/bin/env python3
"""Validate authored mass/inertia, collision topology and portable USD texture dependencies."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
import trimesh


def validate_asset(path, metadata):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, UsdUtils
    path = Path(path).resolve()
    layers, assets, missing = UsdUtils.ComputeAllDependencies(str(path))
    if missing or not assets:
        raise ValueError('Missing USD texture dependency')
    if any(Path(str(p)).resolve().parent != path.parent / 'textures' for p in assets):
        raise ValueError('Asset is not self-contained')
    stage = Usd.Stage.Open(str(path))
    if not stage or UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != 'Z':
        raise ValueError('Expected meters and Z-up')
    if UsdPhysics.GetStageKilogramsPerUnit(stage) != 1:
        raise ValueError('Expected kilogram mass units')
    root = stage.GetDefaultPrim()
    if not root or not root.HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError('Missing dynamic rigid body')
    mass = UsdPhysics.MassAPI(root)
    if not np.isclose(mass.GetMassAttr().Get(), metadata['mass_kg']):
        raise ValueError('Mass differs from profile')
    if not np.allclose(mass.GetCenterOfMassAttr().Get(), metadata['center_of_mass_m'], atol=1e-7):
        raise ValueError('COM differs from estimate')
    diagonal = np.array(mass.GetDiagonalInertiaAttr().Get())
    q = mass.GetPrincipalAxesAttr().Get()
    axes = Rotation.from_quat([*q.GetImaginary(), q.GetReal()]).as_matrix()
    expected_tensor = np.asarray(metadata['inertia_kg_m2_about_com_asset_axes'])
    # MassAPI uses float32 diagonal inertia and quaternion. A tensor-scaled
    # absolute bound handles small off-diagonal entries without hiding large errors.
    precision_bound = max(1e-12, np.linalg.norm(expected_tensor) * 5e-7)
    if min(diagonal) <= 0 or not np.allclose(axes @ np.diag(diagonal) @ axes.T,
        expected_tensor, rtol=1e-5, atol=precision_bound):
        raise ValueError('USD inertia does not reconstruct the computed tensor')
    count = 0
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        count += 1
        mesh = UsdGeom.Mesh(prim)
        if set(mesh.GetFaceVertexCountsAttr().Get()) != {3}:
            raise ValueError('Collider must be triangular')
        geometry = trimesh.Trimesh(np.asarray(mesh.GetPointsAttr().Get()),
                                  np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3), process=True)
        if not geometry.is_volume or not geometry.is_convex:
            raise ValueError('Collider is not a closed convex volume')
        if UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() != 'convexHull':
            raise ValueError('Expected precomputed convex collider')
        if not prim.GetAttribute('physxCollision:contactOffset').Get() > prim.GetAttribute('physxCollision:restOffset').Get():
            raise ValueError('Invalid collision offsets')
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial('physics')
        if not material or not material.GetPrim().HasAPI(UsdPhysics.MaterialAPI):
            raise ValueError('Missing contact material')
    if count != metadata['collider_count'] or count == 0:
        raise ValueError('Collider count mismatch')
    visual = UsdGeom.Mesh(stage.GetPrimAtPath('/Object/Visual'))
    if visual.GetPrim().HasAPI(UsdPhysics.CollisionAPI):
        raise ValueError('Visual unexpectedly participates in collision')
    extent = np.ptp(np.asarray(visual.GetPointsAttr().Get()), axis=0)
    if not np.allclose(extent, metadata['extent_m'], rtol=1e-5, atol=1e-7):
        raise ValueError('Visual extent mismatch')
    return {'usd_layers': len(layers), 'resolved_texture_assets': len(assets), 'convex_colliders': count,
            'inertia_reconstructed': True, 'units_and_contact_bindings': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    for item in inventory['objects']:
        directory = Path(item['usd']).parent
        for name, expected in json.loads((directory / 'complete.json').read_text()).items():
            with (directory / name).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Generated file hash mismatch: ' + name)
        validate_asset(item['usd'], json.loads(Path(item['metadata']).read_text()))
    print(json.dumps({'objects': len(inventory['objects']), 'offline_validation_passed': True}))


if __name__ == '__main__':
    main()
