#!/usr/bin/env python3
"""Compare A2 image-band centres with the fixed table-anchored grasp centre."""
import argparse
import glob
import json
import os
from pathlib import Path
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get('GRASP_GEOMETRY_ROOT', str(HERE.parent)))
from nankai_robocup_grasp_geometry.depth import depth_cloud
from nankai_robocup_grasp_geometry.local_bottle import LocalBottleConfig, bottle_band, detection_cloud, fit_local_bottle
from nankai_robocup_grasp_geometry.table_anchored import AnchoredConfig, TableAnchoredBottle
from nankai_robocup_grasp_geometry.tabletop import FitConfig, FitRejected
from fit_samples import CameraInfo, Depth


def evaluate(path, grasp_height=.115, bottle_height=.2302, stride=2):
    with np.load(path, allow_pickle=False) as sample:
        config = LocalBottleConfig(**json.loads(str(sample['fit_config'].item())))
        depth = Depth(sample['depth_m'])
        info = CameraInfo(sample)
        scene = depth_cloud(depth.data, depth.width, depth.height, depth.step, depth.encoding,
            depth.is_bigendian, info.p, sample['base_T_camera'], sample['base_roi'],
            stride=stride, intrinsics=info.k, distortion=info.d)
        detected = detection_cloud(depth, info, sample['bbox_xyxy'], sample['base_T_camera'],
                                   sample['base_roi'], stride=stride)
        axis = np.asarray(sample['bottle_axis_base'])
        estimate = TableAnchoredBottle(axis, AnchoredConfig(bottle_height, grasp_height,
                                        config.radius), FitConfig())
        anchored, observed = estimate.update(scene, detected, sample['camera_position_base'],
                                              0., table_scene=scene)
        if not observed:
            raise FitRejected(estimate.diagnostics['reason'])
        truth = np.array([0., 0., grasp_height])
        old_points, _ = bottle_band(depth, info, sample['bbox_xyxy'], sample['base_T_camera'],
                                    sample['base_roi'], config, stride=stride)
        old, _ = fit_local_bottle(old_points, axis, sample['camera_position_base'], config)
        anchored_error = anchored.waist_center-truth
        old_error = old.waist_center-truth
        image_gt = np.asarray(sample['gt_center_base'])
        return {'sample': Path(path).name, 'pose_id': str(sample['pose_id']),
            'visible_fraction': float(sample['visible_fraction']),
            'anchored_xyz_m': anchored.waist_center.tolist(), 'truth_xyz_m': truth.tolist(),
            'anchored_error_xyz_m': anchored_error.tolist(),
            'anchored_error_xy_mm': float(np.linalg.norm(anchored_error[:2])*1000),
            'anchored_error_z_mm': float(anchored_error[2]*1000),
            'old_center_xyz_m': old.waist_center.tolist(),
            'old_error_to_fixed_z_mm': float(old_error[2]*1000),
            'old_image_band_gt_z_m': float(image_gt[2]),
            'fit_diagnostics': estimate.diagnostics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples', nargs='+')
    parser.add_argument('--output', required=True)
    parser.add_argument('--grasp-height', type=float, default=.115)
    parser.add_argument('--bottle-height', type=float, default=.2302)
    parser.add_argument('--stride', type=int, default=2)
    args = parser.parse_args()
    paths = sorted({item for pattern in args.samples for item in glob.glob(pattern)})
    if not paths:
        parser.error('no input samples matched')
    records = []
    for path in paths:
        try:
            records.append({'status': 'fit', **evaluate(path, args.grasp_height,
                                                         args.bottle_height, args.stride)})
        except (FitRejected, ValueError, KeyError, OSError) as exc:
            records.append({'status': 'rejected', 'sample': Path(path).name, 'reason': str(exc)})
    good = [r for r in records if r['status'] == 'fit']
    def quantile(key, q):
        return float(np.quantile([abs(r[key]) for r in good], q)) if good else None
    report = {'samples': len(records), 'fit': len(good), 'rejected': len(records)-len(good),
        'anchored_horizontal_error_mm_p95': quantile('anchored_error_xy_mm', .95),
        'anchored_absolute_axial_error_mm_p95': quantile('anchored_error_z_mm', .95),
        'old_absolute_axial_error_to_fixed_grasp_mm_p95': quantile('old_error_to_fixed_z_mm', .95),
        'records': records}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'records'}))


if __name__ == '__main__':
    main()
