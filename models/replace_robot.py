#!/usr/bin/env python3
"""Replace a scene's robot from a rebuilt assembly, retaining its environment."""
import argparse
import os
from pathlib import Path


def replace_robot(source, robot_file, destination):
    from pxr import Usd, UsdGeom, UsdPhysics
    original = Usd.Stage.Open(str(source))
    old = original.GetPrimAtPath('/World/piper_x_d405_assembly')
    root = next(p for p in Usd.PrimRange(old) if p.HasAPI(UsdPhysics.ArticulationRootAPI))
    transform = UsdGeom.Xformable(old).GetLocalTransformation()
    drives = {}
    for p in Usd.PrimRange(old):
        if p.GetName() not in [f'joint{i}' for i in range(1, 9)]:
            continue
        kind = 'angular' if p.IsA(UsdPhysics.RevoluteJoint) else 'linear'
        names = [f'drive:{kind}:physics:{n}' for n in ('type', 'stiffness', 'damping')]
        drives[p.GetName()] = [(n, p.GetAttribute(n).GetTypeName(), p.GetAttribute(n).Get()) for n in names]
    articulation = [(n, root.GetAttribute(n).GetTypeName(), root.GetAttribute(n).Get()) for n in (
        'physxArticulation:enabledSelfCollisions', 'physxArticulation:solverPositionIterationCount',
        'physxArticulation:solverVelocityIterationCount') if root.GetAttribute(n)]
    if destination.exists() or destination == source:
        raise ValueError('Write a new scene path; keep the source for comparison')
    original.GetRootLayer().Export(str(destination))
    stage = Usd.Stage.Open(str(destination))
    stage.RemovePrim(str(old.GetPath()))
    robot = UsdGeom.Xform.Define(stage, str(old.GetPath()))
    robot.GetPrim().GetReferences().AddReference(os.path.relpath(robot_file, destination.parent),
                                                '/piper_x_d405_assembly')
    robot.AddTransformOp().Set(transform)
    robot.GetPrim().GetVariantSet('Physics').SetVariantSelection('physx')
    for p in Usd.PrimRange(robot.GetPrim()):
        if p.GetName() in drives:
            kind = 'angular' if p.IsA(UsdPhysics.RevoluteJoint) else 'linear'
            UsdPhysics.DriveAPI.Apply(p, kind)
            for name, dtype, value in drives[p.GetName()]:
                p.CreateAttribute(name, dtype, custom=False).Set(value)
        if p.HasAPI(UsdPhysics.ArticulationRootAPI):
            p.AddAppliedSchema('PhysxArticulationAPI')
            for name, dtype, value in articulation:
                p.CreateAttribute(name, dtype, custom=False).Set(value)
    stage.GetRootLayer().Save()
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--robot-usd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import isaacsim
    print(replace_robot(args.scene.resolve(), args.robot_usd.resolve(), args.output.resolve()))
