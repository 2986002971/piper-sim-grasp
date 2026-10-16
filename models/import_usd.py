#!/usr/bin/env python3
"""Isaac Sim 6.1 URDF import, preserving fixed D405 frames and relative USD dependencies."""
import sys
import traceback
import os
import json
from pathlib import Path
from isaacsim.simulation_app import SimulationApp

urdf=Path(sys.argv[1]).resolve()
sys.argv=sys.argv[:1]
app=SimulationApp({'headless':True,'active_gpu':0,'physics_gpu':0,'multi_gpu':False})
exit_code=0
try:
    from isaacsim.core.utils.extensions import enable_extension
    from pxr import Usd, UsdGeom, UsdLux
    enable_extension('isaacsim.asset.importer.urdf')
    app.update()
    from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig
    config=URDFImporterConfig(urdf_path=str(urdf),usd_path=str(urdf.parent/'imported'),
                              merge_fixed_joints=False,merge_mesh=False,fix_base=True)
    imported=Path(URDFImporter(config).import_urdf()).resolve()
    robot=Usd.Stage.Open(str(imported))
    if not robot or not robot.GetDefaultPrim(): raise RuntimeError('No imported default robot prim')
    # Normalize importer references to relative paths before moving the generation directory.
    from pxr import UsdUtils
    def relative(layer, asset):
        if asset.startswith(str(urdf.parent)+'/'):
            return os.path.relpath(asset,Path(layer.realPath).parent)
        return asset
    layers, _, missing=UsdUtils.ComputeAllDependencies(str(imported))
    if missing: raise RuntimeError('Unresolved imported assets')
    for layer in layers:
        UsdUtils.ModifyAssetPaths(layer,lambda asset, layer=layer:relative(layer,asset))
        layer.Save()
    wrapper=Usd.Stage.CreateNew(str(urdf.parent/'robot.usd'))
    UsdGeom.SetStageMetersPerUnit(wrapper,1.0);UsdGeom.SetStageUpAxis(wrapper,UsdGeom.Tokens.z)
    prim=wrapper.DefinePrim('/Robot','Xform')
    prim.GetReferences().AddReference(os.path.relpath(imported,urdf.parent),robot.GetDefaultPrim().GetPath())
    wrapper.SetDefaultPrim(prim);wrapper.GetRootLayer().Save()
    stage=Usd.Stage.CreateNew(str(urdf.parent/'piper_x_d405.usda'))
    UsdGeom.SetStageMetersPerUnit(stage,1.0);UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
    root=stage.DefinePrim('/piper_x_d405_assembly','Xform')
    root.GetReferences().AddReference('./robot.usd','/Robot')
    stage.SetDefaultPrim(root)
    from d405_camera import ensure_d405_camera, ensure_d405_depth_camera, RGB_INTRINSICS
    camera=ensure_d405_camera(stage)
    depth_camera=ensure_d405_depth_camera(stage,camera)
    metadata_path=urdf.parent/'assembly.json'
    metadata=json.loads(metadata_path.read_text())
    metadata['viewport_camera']={'path':str(camera.GetPath()),'depth_path':str(depth_camera.GetPath()),
                                 'intrinsics':RGB_INTRINSICS,
                                 'axes':'USD camera under ROS optical frame: local Rx(pi)'}
    metadata_path.write_text(json.dumps(metadata,indent=2))
    light=UsdLux.DomeLight.Define(stage,'/EnvironmentLight');light.CreateIntensityAttr(1000)
    stage.GetRootLayer().Save()
    print('PIPER_USD_IMPORTED',flush=True)
except BaseException:
    exit_code=1
    traceback.print_exc()
    sys.stderr.flush()
    raise
finally:
    app.close(exit_code=exit_code)
