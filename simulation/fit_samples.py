#!/usr/bin/env python3
"""Run the existing A2 fitter against normalized synthetic depth samples."""
import argparse
import csv
import glob
import json
import os
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
CORE_ROOT = Path(os.environ.get('GRASP_GEOMETRY_ROOT', HERE.parent))
sys.path.insert(0, str(CORE_ROOT))
from nankai_robocup_grasp_geometry.local_bottle import (  # noqa: E402
    LocalBottleConfig, bottle_band, fit_local_bottle)
from nankai_robocup_grasp_geometry.tabletop import FitRejected  # noqa: E402


class Depth:
    width, height, encoding, is_bigendian = 640, 480, '32FC1', False
    def __init__(self, image):
        self.data = np.asarray(image, dtype='<f4').tobytes()
        self.step = self.width * 4


class CameraInfo:
    def __init__(self, sample):
        self.k = sample['K'].reshape(-1).tolist()
        self.d = sample['D'].reshape(-1).tolist()
        self.p = sample['P'].reshape(-1).tolist()


def run(path):
    with np.load(path, allow_pickle=False) as s:
        depth = np.asarray(s['depth_m'], dtype=np.float32)
        if depth.shape != (480, 640):
            raise ValueError('depth_m must be 480x640')
        required = ('K', 'D', 'P', 'bbox_xyxy', 'base_T_camera', 'base_roi',
                    'bottle_axis_base', 'camera_position_base', 'gt_center_base')
        missing = [key for key in required if key not in s]
        if missing:
            raise ValueError('missing fields: ' + ', '.join(missing))
        config = LocalBottleConfig(**json.loads(str(s['fit_config'].item()))) if 'fit_config' in s else LocalBottleConfig()
        pts, band = bottle_band(Depth(depth), CameraInfo(s), s['bbox_xyxy'],
                                s['base_T_camera'], s['base_roi'], config)
        fit, diagnostic = fit_local_bottle(pts, s['bottle_axis_base'],
                                           s['camera_position_base'], config)
        center = np.asarray(s['gt_center_base'], dtype=float)
        error = fit.waist_center - center
        metadata = {name: np.asarray(s[name]).item() for name in
                    ('gripper_opening_mm', 'visible_fraction', 'pose_id') if name in s}
        return {'sample': str(path), 'status': 'fit', 'metadata': metadata, 'band_xyxy': band,
                'fit_center_base': fit.waist_center.tolist(),
                'gt_center_base': center.tolist(), 'error_xyz_m': error.tolist(),
                'error_norm_m': float(np.linalg.norm(error)), 'diagnostic': diagnostic}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('samples', nargs='+')
    p.add_argument('--output', required=True)
    p.add_argument('--csv', help='optional flat summary CSV')
    args = p.parse_args()
    paths = sorted({item for pattern in args.samples for item in glob.glob(pattern)})
    if not paths:
        p.error('no samples matched')
    records = []
    for path in paths:
        try:
            records.append(run(path))
        except (FitRejected, ValueError, KeyError, OSError) as exc:
            records.append({'sample': path, 'status': 'rejected', 'reason': str(exc)})
    Path(args.output).write_text(json.dumps(records, indent=2) + '\n')
    if args.csv:
        columns = ('sample', 'status', 'reason', 'pose_id', 'gripper_opening_mm',
                   'visible_fraction', 'point_count', 'supported_fraction', 'rms',
                   'error_x_m', 'error_y_m', 'error_z_m', 'error_norm_m')
        with open(args.csv, 'w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for record in records:
                diag = record.get('diagnostic', {})
                error = record.get('error_xyz_m', [None, None, None])
                writer.writerow({
                    'sample': record['sample'], 'status': record['status'],
                    'reason': record.get('reason', ''),
                    'pose_id': record.get('metadata', {}).get('pose_id', ''),
                    'gripper_opening_mm': record.get('metadata', {}).get('gripper_opening_mm', ''),
                    'visible_fraction': record.get('metadata', {}).get('visible_fraction', ''),
                    'point_count': diag.get('point_count', ''),
                    'supported_fraction': diag.get('supported_fraction', ''),
                    'rms': diag.get('rms', ''),
                    'error_x_m': error[0], 'error_y_m': error[1], 'error_z_m': error[2],
                    'error_norm_m': record.get('error_norm_m', '')})
    print(json.dumps({'samples': len(records), 'fit': sum(r['status'] == 'fit' for r in records),
                      'rejected': sum(r['status'] != 'fit' for r in records), 'output': args.output}))


if __name__ == '__main__':
    main()
