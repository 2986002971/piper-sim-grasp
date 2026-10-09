#!/usr/bin/env python3
"""Replay continuous Isaac RGB-D frames, dropout, noise and radius-prior groups."""
import argparse
import glob
import json
import os
from pathlib import Path
import sys
import numpy as np
import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get('GRASP_GEOMETRY_ROOT', str(HERE.parent)))
from nankai_robocup_grasp_geometry.depth import depth_cloud
from nankai_robocup_grasp_geometry.local_bottle import LocalBottleConfig, bottle_band, detection_cloud
from nankai_robocup_grasp_geometry.table_anchored import AnchoredConfig, TableAnchoredBottle
from nankai_robocup_grasp_geometry.tabletop import FitConfig, FitRejected
from fit_samples import CameraInfo, Depth


def run(paths, output, noise_mm=0., holes=0., radius_bias_mm=0., initial_offset_mm=0.,
        drop_frames=(), grasp_height=.115, bottle_height=.2302, stride=2, seed=12):
    rng = np.random.default_rng(seed)
    estimator = None
    samples, failures, legacy_z = [], [], []
    truth = np.array([0., 0., grasp_height])
    for frame_index, path in enumerate(paths):
        with np.load(path, allow_pickle=False) as data:
            config = LocalBottleConfig(**json.loads(str(data['fit_config'].item())))
            image = np.asarray(data['depth_m'], dtype=np.float32).copy()
            valid = np.isfinite(image) & (image >= .08) & (image <= 1.5)
            if noise_mm:
                image[valid] += rng.normal(0., noise_mm/1000., int(valid.sum())).astype(np.float32)
            if holes:
                image[valid & (rng.random(image.shape) < holes)] = 0.
            dropped = any(lo <= frame_index <= hi for lo, hi in drop_frames)
            if dropped:
                image.fill(0.)
            depth, info = Depth(image), CameraInfo(data)
            scene = depth_cloud(depth.data, depth.width, depth.height, depth.step, depth.encoding,
                depth.is_bigendian, info.p, data['base_T_camera'], data['base_roi'], stride=stride,
                intrinsics=info.k, distortion=info.d)
            box = np.asarray(data['bbox_xyxy'])
            detections = (detection_cloud(depth, info, box, data['base_T_camera'], data['base_roi'],
                                          stride=stride) if box.size and not dropped else np.empty((0,3)))
            if estimator is None:
                estimator = TableAnchoredBottle(data['bottle_axis_base'],
                    AnchoredConfig(bottle_height, grasp_height, config.radius+radius_bias_mm/1000.),
                    FitConfig())
            estimate, observed = estimator.update(scene, detections, data['camera_position_base'],
                float(np.asarray(data.get('source_time_s', frame_index/30.)).item()),
                table_scene=scene)
            if estimate is not None and frame_index == 0 and initial_offset_mm:
                estimator.centre += np.array([initial_offset_mm/1000., -initial_offset_mm/1000.])
                estimator.covariance = np.eye(2)*estimator.config.initial_std**2
            if estimate is None:
                failures.append({'frame':frame_index,'reason':estimator.diagnostics['reason']})
                continue
            delta = estimate.waist_center-truth
            legacy_axial = None
            samples.append({'frame':frame_index,'pose_id':str(data['pose_id']),
                'visible_fraction':float(data['visible_fraction']), 'observed':observed,
                'held':not observed,'target_xyz_m':estimate.waist_center.tolist(),
                'error_xyz_mm':(delta*1000).tolist(),
                'horizontal_error_mm':float(np.linalg.norm(delta[:2])*1000),
                'axial_error_mm':float(delta[2]*1000),
                'legacy_axial_error_mm':legacy_axial,
                'covariance_2d':estimator.covariance.tolist(),
                'source_time_s':float(data.get('source_time_s',frame_index/30.)),
                'dropout':bool(dropped)})
            if box.size and not dropped:
                try:
                    band, _ = bottle_band(depth, info, box, data['base_T_camera'],
                                           data['base_roi'], config, stride=stride)
                    old, _ = __import__('nankai_robocup_grasp_geometry.local_bottle',
                        fromlist=['fit_local_bottle']).fit_local_bottle(
                            band, data['bottle_axis_base'], data['camera_position_base'], config)
                    legacy_axial = float((old.waist_center[2]-truth[2])*1000)
                    legacy_z.append(legacy_axial)
                    samples[-1]['legacy_axial_error_mm'] = legacy_axial
                except (ValueError, FitRejected):
                    pass
    def stats(values):
        return {'median_abs_mm':float(np.median(np.abs(values))),
                'p95_abs_mm':float(np.quantile(np.abs(values),.95)),
                'max_abs_mm':float(np.max(np.abs(values)))} if values else None
    centres = np.asarray([s['target_xyz_m'] for s in samples])
    median_centre = np.median(centres, axis=0) if len(centres) else np.zeros(3)
    jitter = (centres-median_centre)*1000 if len(centres) else np.empty((0,3))
    report = {'frame_count':len(paths),'estimated_frames':len(samples),'fit_failures':failures,
        'observed_frames':sum(s['observed'] for s in samples),
        'held_frames':sum(s['held'] for s in samples),
        'horizontal_error_mm':stats([s['horizontal_error_mm'] for s in samples if s['observed']]),
        'axial_error_mm':stats([s['axial_error_mm'] for s in samples if s['observed']]),
        'target_jitter_mm':({'rms_from_median':float(np.sqrt(np.mean(np.sum(jitter*jitter,axis=1)))),
            'horizontal_rms_from_median':float(np.sqrt(np.mean(np.sum(jitter[:,:2]**2,axis=1)))),
            'peak_to_peak_3d':float(np.linalg.norm(np.ptp(centres,axis=0))*1000)} if len(centres) else None),
        'legacy_variable_band_axial_error_mm':stats(legacy_z),
        'frames':samples}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n')
    panels=[]
    for index in sorted(set([0,len(paths)//2,len(paths)-1])):
        with np.load(paths[index],allow_pickle=False) as frame:
            rgb=np.asarray(frame['rgb'])[:,:,:3]
            panel=cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),(320,240))
            box=np.asarray(frame['bbox_xyxy'])
            if box.size:
                x0,y0,x1,y1=np.rint(box*np.array([.5,.5,.5,.5])).astype(int)
                cv2.rectangle(panel,(x0,y0),(x1,y1),(0,255,255),1)
            cv2.putText(panel,f'frame {index} visible={float(frame["visible_fraction"]):.2f}',
                (5,18),cv2.FONT_HERSHEY_SIMPLEX,.45,(40,255,40),1,cv2.LINE_AA)
            panels.append(panel)
    cv2.imwrite(str(output.with_name(output.stem+'-views.png')),np.hstack(panels))
    plot=np.full((420,1200,3),255,dtype=np.uint8)
    left,right,top,bottom=70,1180,25,370
    ymax=max([20.]+[abs(s['horizontal_error_mm']) for s in samples]+
             [abs(s['axial_error_mm']) for s in samples]+
             [abs(v) for v in legacy_z])
    for lo,hi in drop_frames:
        x0=left+int(lo/max(1,len(paths)-1)*(right-left))
        x1=left+int(hi/max(1,len(paths)-1)*(right-left))
        cv2.rectangle(plot,(x0,top),(x1,bottom),(230,230,230),-1)
    cv2.rectangle(plot,(left,top),(right,bottom),(0,0,0),1)
    legacy_by_frame={s['frame']:s['legacy_axial_error_mm'] for s in samples
                     if s['legacy_axial_error_mm'] is not None}
    for key,color,absolute in [('horizontal_error_mm',(220,80,20),False),
        ('axial_error_mm',(30,160,30),True),('legacy_axial_error_mm',(20,20,220),True)]:
        points=[]
        for s in samples:
            value=(legacy_by_frame.get(s['frame']) if key=='legacy_axial_error_mm'
                   else s[key])
            if value is None or (key=='axial_error_mm' and not s['observed']): continue
            value=abs(value) if absolute else value
            points.append((left+int(s['frame']/max(1,len(paths)-1)*(right-left)),
                           bottom-int(value/ymax*(bottom-top))))
        if len(points)>1:
            cv2.polylines(plot,[np.asarray(points,dtype=np.int32)],False,color,2,cv2.LINE_AA)
    cv2.putText(plot,'blue: horizontal mm   green: fixed-point |axial| mm   red: old image-band |axial| mm; grey: depth dropout',
        (left,400),cv2.FONT_HERSHEY_SIMPLEX,.43,(20,20,20),1,cv2.LINE_AA)
    cv2.imwrite(str(output.with_name(output.stem+'-errors.png')),plot)
    print(json.dumps({k:v for k,v in report.items() if k != 'frames' and k != 'fit_failures'}))
    return report


def parse_ranges(values):
    result=[]
    for value in values:
        lo,hi=map(int,value.split(':'))
        if hi < lo: raise ValueError('drop range must be START:END in ascending order')
        result.append((lo,hi))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('samples',nargs='+');parser.add_argument('--output',required=True)
    parser.add_argument('--depth-noise-mm',type=float,default=0.)
    parser.add_argument('--hole-fraction',type=float,default=0.)
    parser.add_argument('--radius-bias-mm',type=float,default=0.)
    parser.add_argument('--initial-offset-mm',type=float,default=0.)
    parser.add_argument('--drop-frames',action='append',default=[])
    parser.add_argument('--grasp-height',type=float,default=.115)
    parser.add_argument('--bottle-height',type=float,default=.2302)
    parser.add_argument('--stride',type=int,default=2)
    args=parser.parse_args()
    paths=sorted({p for pattern in args.samples for p in glob.glob(pattern)})
    if not paths: parser.error('no samples matched')
    run(paths,Path(args.output),args.depth_noise_mm,args.hole_fraction,args.radius_bias_mm,
        args.initial_offset_mm,parse_ranges(args.drop_frames),args.grasp_height,
        args.bottle_height,args.stride)

if __name__=='__main__': main()
