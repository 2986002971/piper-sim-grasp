#!/usr/bin/env python3
"""Exercise only owned simulated devices, with system Humble subscribers."""
import argparse
from collections import OrderedDict
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, CameraInfo, JointState
from tf2_ros import Buffer, TransformListener
import yaml


def stamp(message):
    return message.header.stamp.sec*10**9+message.header.stamp.nanosec


class Probe(Node):
    def __init__(self, config):
        super().__init__('sim_device_acceptance', parameter_overrides=[
            rclpy.parameter.Parameter('use_sim_time', value=True)])
        self.frames = {key: OrderedDict() for key in ('rgb', 'depth', 'info')}
        self.clock = 0
        self.joints = None
        self.states = []
        self.config = config
        for key, msg_type, topic in [('rgb', Image, config['rgb_topic']),
                                      ('depth', Image, config['depth_topic']),
                                      ('info', CameraInfo, config['camera_info_topic'])]:
            def receive(message, key=key):
                values = self.frames[key]
                values[stamp(message)] = message
                if len(values) > 30: values.popitem(last=False)
            self.create_subscription(msg_type, topic, receive, qos_profile_sensor_data)
        def clock(message): self.clock = message.clock.sec*10**9+message.clock.nanosec
        def joints(message):
            self.joints = message
            self.states.append(dict(zip(message.name, message.position)))
        self.create_subscription(Clock, '/clock', clock, qos_profile_sensor_data)
        self.create_subscription(JointState, config['driver_namespace']+'/joint_states', joints, 10)
        self.command = self.create_publisher(JointState, config['bridge_command_topic'], 1)
        self.tf = Buffer()
        self.listener = TransformListener(self.tf, self)

    def wait(self, predicate, label, timeout=240.):
        print('Waiting: '+label, flush=True)
        end = time.monotonic()+timeout
        while not predicate():
            for child in getattr(self, 'children', []):
                if child.poll() is not None:
                    raise RuntimeError(f'Acceptance child exited ({child.returncode}); inspect its log')
            rclpy.spin_once(self, timeout_sec=.02)
            if time.monotonic() > end: raise TimeoutError(label)

    def spin_for(self, seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end: rclpy.spin_once(self, timeout_sec=.02)

    def matching(self):
        common = set(self.frames['rgb']) & set(self.frames['depth']) & set(self.frames['info'])
        return max(common) if common else None

    def send(self, positions):
        message = JointState()
        message.name = [f'joint{i}' for i in range(1, 7)]
        message.position = list(map(float, positions))
        self.command.publish(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--camera-calibration', action='store_true')
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    repo = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    children, logs = [], []
    result = {'passed': False}
    rclpy.init()
    probe = Probe(config)
    probe.children = children
    def start(command, name, **kwargs):
        log = (args.output/(name+'.log')).open('w')
        logs.append(log)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, **kwargs)
        children.append(process)
        return process
    try:
        isaac = start(['bash', str(repo/'runtime/isaac/run-isaac-ros.sh'), str(args.config), '--control-stdin'] +
                      (['--camera-calibration'] if args.camera_calibration else []) + (['--stream'] if args.stream else []),
                      'isaac', stdin=subprocess.PIPE, text=True)
        start(['ros2', 'launch', 'piper_sim_devices', 'devices.launch.py', 'config:='+str(args.config)], 'devices')
        probe.wait(lambda: probe.matching() and probe.joints is not None and probe.clock > 0, 'RGB-D, joints and clock')
        probe.wait(lambda: probe.matching() and probe.tf.can_transform(
            'base_link', config['optical_frame'], Time(nanoseconds=probe.matching())),
            'TF at acquisition time')
        probe.wait(lambda: probe.matching() and max(probe.frames['rgb'][probe.matching()].data) > 20,
                   'illuminated RGB')
        sample_stamp = probe.matching()
        rgb, depth, info = (probe.frames[key][sample_stamp] for key in ('rgb', 'depth', 'info'))
        assert rgb.encoding == 'rgb8', rgb.encoding
        assert depth.encoding == '32FC1', depth.encoding
        assert rgb.width == depth.width == info.width == 640
        assert rgb.height == depth.height == info.height == 480
        assert all(m.header.frame_id == config['optical_frame'] for m in (rgb, depth, info))
        model = json.loads((Path(config['output_dir'])/'device-model.json').read_text())
        lens = model['lens']
        assert info.distortion_model == 'plumb_bob', info.distortion_model
        np.testing.assert_allclose(info.k, [lens['fx'], 0., lens['cx'], 0., lens['fy'], lens['cy'], 0., 0., 1.], atol=1e-5)
        np.testing.assert_allclose(info.d, [lens[n] for n in ('k1', 'k2', 'p1', 'p2', 'k3')], atol=1e-7)
        pixels = np.frombuffer(depth.data, dtype=np.float32).reshape(depth.height, depth.step//4)[:, :depth.width]
        assert np.isfinite(pixels).sum() > 1000
        np.savez_compressed(args.output/'rgbd.npz', rgb=np.frombuffer(rgb.data, dtype=np.uint8).reshape(rgb.height, rgb.step)[:, :rgb.width*3].reshape(rgb.height, rgb.width, 3), depth=pixels, k=info.k, d=info.d)
        if args.camera_calibration:
            import cv2
            colors = np.frombuffer(rgb.data, dtype=np.uint8).reshape(rgb.height, rgb.step)[:, :rgb.width*3].reshape(rgb.height, rgb.width, 3)
            expected, _ = cv2.projectPoints(np.array(model['calibration_points_optical_m']),
                                            np.zeros(3), np.zeros(3), np.array(info.k).reshape(3, 3), np.array(info.d))
            checks = []
            for i, channels in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1))):
                mask = np.ones((rgb.height, rgb.width), dtype=bool)
                for channel, on in enumerate(channels):
                    mask &= colors[:, :, channel] > 170 if on else colors[:, :, channel] < 80
                y, x = np.where(mask)
                assert len(x) > 10, ('calibration marker missing', i, len(x))
                centre = np.array([x.mean(), y.mean()])
                error = float(np.linalg.norm(centre-expected[i, 0]))
                expected_depth = model['calibration_points_optical_m'][i][2]
                foreground = np.abs(pixels[mask]-expected_depth) < .0002
                checks.append(dict(marker=i, expected_pixel=expected[i, 0].tolist(),
                                   measured_pixel=centre.tolist(), reprojection_error_px=error,
                                   aligned_depth_fraction=float(foreground.mean())))
                assert error < 1.5, checks[-1]
                assert foreground.mean() > .8, checks[-1]
            result['projection'] = checks
        transform = probe.tf.lookup_transform('base_link', config['optical_frame'], Time(nanoseconds=sample_stamp))
        result['camera'] = dict(stamp_ns=sample_stamp, rgb_encoding=rgb.encoding, depth_encoding=depth.encoding,
                                frame=rgb.header.frame_id, k=list(info.k), d=list(info.d),
                                finite_depth_pixels=int(np.isfinite(pixels).sum()),
                                tf_translation=[getattr(transform.transform.translation, a) for a in 'xyz'])
        current = dict(zip(probe.joints.name, probe.joints.position))
        initial = np.array(config['initial_joint_positions'])
        target = initial.copy()
        target[0] += .03
        probe.states.clear()
        probe.send(target)
        probe.wait(lambda: abs(dict(zip(probe.joints.name, probe.joints.position))['joint1']-target[0]) < .003, 'measured joint target', 15.)
        result['arm'] = dict(initial_joint1=current['joint1'], target_joint1=float(target[0]),
                             measured_joint1=dict(zip(probe.joints.name, probe.joints.position))['joint1'],
                             feedback_samples=len(probe.states))
        assert len(probe.states) > 1
        probe.wait(lambda: len(probe.states) >= 20 and np.max(np.ptp(
            [[sample[f'joint{i}'] for i in range(1, 7)] for sample in probe.states[-20:]], axis=0)) < .0003,
            'joint settling before paused frame comparison', 30.)
        isaac.stdin.write('pause\n'); isaac.stdin.flush()
        probe.spin_for(1.)
        paused = probe.clock
        paused_stamp = max(probe.frames['rgb'])
        probe.spin_for(2.)
        assert probe.clock == paused, (paused, probe.clock)
        assert max(probe.frames['rgb']) == paused_stamp, 'camera publishes new acquisition stamps while paused'
        result['pause'] = dict(clock_ns=paused, wall_seconds=2.)
        # At rest, compare RSP's joint-based chain against the physical USD
        # transforms. These offline diagnostics never feed business TF.
        from scipy.spatial.transform import Rotation
        state = json.loads((Path(config['output_dir'])/'device-state.json').read_text())
        assert not state['playing']
        physical = np.linalg.inv(np.array(state['world_T_articulation_root'])) @ np.array(state['world_T_optical'])
        measured = probe.tf.lookup_transform('base_link', config['optical_frame'], Time()).transform
        rsp = np.eye(4)
        rsp[:3, 3] = [getattr(measured.translation, a) for a in 'xyz']
        rsp[:3, :3] = Rotation.from_quat([getattr(measured.rotation, a) for a in 'xyzw']).as_matrix()
        position_error = float(np.linalg.norm(physical[:3, 3]-rsp[:3, 3]))
        angle_error = float(Rotation.from_matrix(physical[:3, :3] @ rsp[:3, :3].T).magnitude())
        result['rsp_physics_agreement'] = dict(position_error_m=position_error, rotation_error_rad=angle_error)
        isaac.stdin.write('resume\n'); isaac.stdin.flush()
        probe.wait(lambda: probe.clock > paused and max(probe.frames['rgb']) > paused_stamp, 'resume', 20.)
        assert position_error < .002 and angle_error < .003, result['rsp_physics_agreement']
        result['passed'] = True
    except Exception as error:
        result['error'] = repr(error)
        print(result['error'], flush=True)
    finally:
        for process in reversed(children):
            if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(children):
            try: process.wait(timeout=15.)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in logs: log.close()
        (args.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        probe.destroy_node()
        rclpy.shutdown()
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
