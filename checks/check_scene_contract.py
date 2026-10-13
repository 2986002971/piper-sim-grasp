#!/usr/bin/env python3
"""Check the supplied bottle scene's composed world geometry against its manifest.

Run with Isaac's Python/Usd bindings; no SimulationApp or GPU is started.
This is offline acceptance data, never an input to business perception.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(args.manifest.read_text())
    stage = Usd.Stage.Open(str(args.scene.resolve()))
    bottle = stage.GetPrimAtPath('/World/SevenUP_Bottle')
    bounds = UsdGeom.BBoxCache(0., ['default', 'render']).ComputeWorldBound(bottle).ComputeAlignedBox()
    low, high = np.array(bounds.GetMin()), np.array(bounds.GetMax())
    center, dimensions = (low+high)/2., high-low
    result = dict(scene=str(args.scene), passed=False, center_m=center.tolist(), dimensions_m=dimensions.tolist(),
                  expected_center_m=expected['bottle_center_m'], expected_dimensions_m=expected['bottle_dimensions_m'],
                  physics_scenes=[str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)],
                  articulation_roots=[str(p.GetPath()) for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)])
    try:
        assert UsdGeom.GetStageMetersPerUnit(stage) == 1. and UsdGeom.GetStageUpAxis(stage) == 'Z'
        assert UsdGeom.Imageable(bottle).ComputeVisibility() != 'invisible'
        np.testing.assert_allclose(center, expected['bottle_center_m'], atol=1e-5, rtol=0.)
        np.testing.assert_allclose(dimensions, expected['bottle_dimensions_m'], atol=1e-5, rtol=0.)
        assert abs(low[2]-expected['tabletop_z_m']) < 1e-5
        result['passed'] = True
    except AssertionError as error: result['error'] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
