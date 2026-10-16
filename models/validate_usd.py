#!/usr/bin/env python3
"""Fail closed on missing USD layers/assets, units, geometry or D405 frames."""
import argparse
import json
from pathlib import Path



def validate(path):
    from pxr import Usd, UsdGeom, UsdUtils
    layers,assets,unresolved=UsdUtils.ComputeAllDependencies(str(path))
    if unresolved: raise ValueError('USD has unresolved dependencies')
    stage=Usd.Stage.Open(str(path))
    if not stage or not stage.GetDefaultPrim(): raise ValueError('No default scene prim')
    if abs(UsdGeom.GetStageMetersPerUnit(stage)-1)>1e-8: raise ValueError('Expected meter scene units')
    prims=list(Usd.PrimRange.Stage(stage,Usd.TraverseInstanceProxies()))
    names={p.GetName() for p in prims}
    for expected in ['link6','gripper_link1','gripper_link2','d405_color_optical_frame']:
        if expected not in names: raise ValueError('Missing assembly frame: '+expected)
    meshes=sum(p.IsA(UsdGeom.Mesh) for p in prims)
    if not meshes: raise ValueError('No mesh geometry')
    # Validate generated optical pose in USD, in addition to source URDF pose.
    metadata=path.parent/'assembly.json'
    if metadata.exists():
        expected=json.loads(metadata.read_text())['optical_transform']
        wrist=next(p for p in stage.Traverse() if p.GetName()=='link6')
        optical=next(p for p in stage.Traverse() if p.GetName()=='d405_color_optical_frame')
        cache=UsdGeom.XformCache()
        relative=cache.GetLocalToWorldTransform(optical)*cache.GetLocalToWorldTransform(wrist).GetInverse()
        if max(abs(relative[j][i]-expected[i][j]) for i in range(4) for j in range(4))>1e-5:
            raise ValueError('Imported USD optical transform differs from assembly metadata')
    return {'layers':len(layers),'assets':len(assets),'meshes':meshes,'unresolved':0,'geometry_only':True}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('scene',type=Path);args=p.parse_args()
    # Bootstrap Isaac's USD bindings without starting a second rendering app.
    import isaacsim
    print(json.dumps(validate(args.scene.resolve())))
