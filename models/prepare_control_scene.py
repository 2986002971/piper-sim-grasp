#!/usr/bin/env python3
"""Explicitly derive a macro-compatible scene; preserve authored robot physics."""
import argparse
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np


def prepare(scene, urdf, output, bottle_physics=None, source_urdf=None):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime/isaac'))
    from validate_ros_kinematics import anchor, urdf_origin, validate
    scene, urdf, output = map(lambda p: Path(p).resolve(), (scene, urdf, output))
    if output.exists() or output == scene: raise ValueError('Write a new scene path')
    original = Usd.Stage.Open(str(scene))
    if not original: raise ValueError('Cannot open source scene')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(output))
    stage.GetRootLayer().subLayerPaths = [os.path.relpath(scene, output.parent)]
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    # Activate the source's authored physics. All masses, inertias, collision
    # filtering, gains and drive limits remain inherited from this same source.
    for prim in list(stage.Traverse()):
        variant = prim.GetVariantSets().GetVariantSet('Physics')
        if 'physx' in variant.GetVariantNames(): variant.SetVariantSelection('physx')
    roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    if len(roots) != 1: raise ValueError('Expected one robot articulation')
    # Refuse the old mixed CAD/ROS assembly before making the deliberate SDK
    # correction. This uses the same read-only eight-joint startup contract.
    if source_urdf is not None: validate(stage, roots[0], source_urdf)
    robot = ET.parse(urdf).getroot()
    changes = []
    for i in range(1, 9):
        name = f'joint{i}'
        kind = UsdPhysics.RevoluteJoint if i <= 6 else UsdPhysics.PrismaticJoint
        candidates = [p for p in stage.Traverse() if p.GetName() == name and p.IsA(kind)]
        if len(candidates) != 1: raise ValueError('Missing or ambiguous joint '+name)
        joint = kind(candidates[0])
        definition = robot.find(f"joint[@name='{name}']")
        frame = urdf_origin(definition) @ anchor(joint, 1)
        matrix = Gf.Matrix4d(frame.T.tolist())
        quaternion = matrix.ExtractRotationQuat()
        joint.GetLocalPos0Attr().Set(Gf.Vec3f(*frame[:3, 3]))
        joint.GetLocalRot0Attr().Set(Gf.Quatf(float(quaternion.GetReal()), Gf.Vec3f(quaternion.GetImaginary())))
        scale = 180./np.pi if i <= 6 else 1.
        limit = definition.find('limit')
        joint.GetLowerLimitAttr().Set(float(limit.get('lower'))*scale)
        joint.GetUpperLimitAttr().Set(float(limit.get('upper'))*scale)
        changes.append(name)
    if bottle_physics:
        body = stage.GetPrimAtPath(bottle_physics['prim'])
        if not body:
            raise ValueError('Bottle physics prim does not exist')
        if any(p != body and p.HasAPI(UsdPhysics.RigidBodyAPI) for p in Usd.PrimRange(body)):
            raise ValueError('Set bottle_physics.prim to the existing rigid-body owner')
        rigid = UsdPhysics.RigidBodyAPI.Apply(body)
        rigid.CreateRigidBodyEnabledAttr(True)
        rigid.CreateKinematicEnabledAttr(False)
        mass = UsdPhysics.MassAPI.Apply(body)
        mass.CreateMassAttr(float(bottle_physics['mass_kg']))
        mass.CreateDensityAttr(0.)
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.))  # PhysX computes inertia from the collision geometry.
        material = UsdShade.Material.Define(stage, '/PiperSimMaterials/BottleContact')
        physical = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        physical.CreateStaticFrictionAttr(float(bottle_physics['static_friction']))
        physical.CreateDynamicFrictionAttr(float(bottle_physics['dynamic_friction']))
        physical.CreateRestitutionAttr(float(bottle_physics['restitution']))
        for prim in Usd.PrimRange(body):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose='physics')
    report = validate(stage, roots[0], urdf)
    stage.GetRootLayer().Save()
    report.update(source_scene=str(scene), derived_scene=str(output), modified_joints=changes,
                  robot_physics='inherited from source; only joint frames and position limits overridden',
                  bottle_physics=bottle_physics, bottle_inertia='automatic from collision geometry')
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--urdf', type=Path, required=True)
    parser.add_argument('--source-urdf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bottle-physics', type=json.loads)
    args = parser.parse_args()
    import isaacsim
    print(prepare(args.scene, args.urdf, args.output, args.bottle_physics, args.source_urdf))
