#!/usr/bin/env python3
"""Measured TCP tracking and gripper Actions against one owned PhysX scene."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from action_msgs.msg import GoalStatus
from nankai_robocup_interfaces.action import MoveGripper
from nankai_robocup_interfaces.msg import TcpReference, TcpTrackingState
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import JointState
from tf2_ros import Buffer, TransformListener
import yaml


class Probe(Node):
    def __init__(self, config, children):
        super().__init__('sim_control_check', parameter_overrides=[rclpy.parameter.Parameter('use_sim_time', value=True)])
        self.children, self.clock, self.state, self.gripper = children, 0, None, None
        self.samples = []
        ns = config['driver_namespace']
        self.create_subscription(Clock, '/clock', lambda m: setattr(self, 'clock', m.clock.sec*10**9+m.clock.nanosec), 1)
        self.create_subscription(TcpTrackingState, ns+'/state', lambda m: setattr(self, 'state', m), 1)
        def grip(message):
            self.gripper = message
            self.samples.append({'stamp_ns': Time.from_msg(message.header.stamp).nanoseconds,
                                 'width_m': message.position[0], 'force_n': message.effort[0]})
        self.create_subscription(JointState, ns+'/gripper_state', grip, 10)
        self.command = self.create_publisher(TcpReference, ns+'/reference', 1)
        self.action = ActionClient(self, MoveGripper, ns+'/move_gripper')
        self.tf = Buffer(); self.listener = TransformListener(self.tf, self)

    def wait(self, predicate, label, timeout=90.):
        print('Waiting: '+label, flush=True)
        until = time.monotonic()+timeout
        while not predicate():
            for child in self.children:
                if child.poll() is not None: raise RuntimeError('Control acceptance child exited; inspect logs')
            rclpy.spin_once(self, timeout_sec=.01)
            if time.monotonic() > until: raise TimeoutError(label)

    def spin_for(self, duration):
        until = time.monotonic()+duration
        while time.monotonic() < until: rclpy.spin_once(self, timeout_sec=.01)

    def grip(self, width, force):
        future = self.action.send_goal_async(MoveGripper.Goal(width_m=width, force_n=force))
        self.wait(future.done, 'gripper acceptance')
        handle = future.result()
        if not handle.accepted: raise RuntimeError('gripper goal rejected')
        return handle, handle.get_result_async()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    repo = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    children, logs, result = [], [], {'passed': False}
    rclpy.init(); probe = Probe(config, children)
    def start(command, name, **kwargs):
        log = (args.output/(name+'.log')).open('w'); logs.append(log)
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, **kwargs)
        children.append(child); return child
    try:
        isaac = start(['bash', str(repo/'runtime/isaac/run-isaac-ros.sh'), str(args.config), '--control-stdin']+
                      (['--stream'] if args.stream else []), 'isaac', stdin=subprocess.PIPE, text=True)
        start(['ros2', 'launch', 'piper_sim_devices', 'devices.launch.py', 'config:='+str(args.config)], 'devices')
        probe.wait(lambda: probe.clock > 0 and probe.gripper is not None and probe.action.server_is_ready(), 'measured gripper and clock', 240.)
        probe.wait(lambda: probe.tf.can_transform('piper_base_link', 'grasp_tcp', Time()), 'measured TCP TF')
        initial = probe.tf.lookup_transform('piper_base_link', 'grasp_tcp', Time()).transform
        target = TcpReference(); target.header.stamp = probe.get_clock().now().to_msg()
        target.header.frame_id = 'piper_base_link'; target.command = TcpReference.TRACK
        target.target_pose.position.x = initial.translation.x
        target.target_pose.position.y = initial.translation.y
        target.target_pose.position.z = initial.translation.z+.005
        target.target_pose.orientation = initial.rotation
        probe.command.publish(target)
        probe.wait(lambda: probe.state is not None and probe.state.reference.header.stamp == target.header.stamp and
                   probe.state.converged and not probe.state.reason, 'actual TCP convergence')
        actual = probe.tf.lookup_transform('piper_base_link', 'grasp_tcp', Time()).transform
        result['tcp'] = {'requested_dz_m': .005, 'measured_dz_m': actual.translation.z-initial.translation.z,
                         'translation_error_m': probe.state.translation_error, 'rotation_error_rad': probe.state.rotation_error}
        assert result['tcp']['measured_dz_m'] > .003
        hold = TcpReference(); hold.command = TcpReference.HOLD; probe.command.publish(hold)
        for width in (0., .1):
            handle, future = probe.grip(width, 5. if width == 0 else 1.)
            probe.wait(future.done, 'stable free-space width')
            response = future.result()
            assert response.status == GoalStatus.STATUS_SUCCEEDED and response.result.completion_type == MoveGripper.Result.WIDTH_REACHED, response
            result['closed' if width == 0 else 'open'] = {'width_m': response.result.final_width_m,
                'force_n': response.result.final_force_n, 'completion_type': response.result.completion_type}
        isaac.stdin.write('pause\n'); isaac.stdin.flush(); probe.spin_for(2.)
        from scipy.spatial.transform import Rotation
        physical_state = json.loads((Path(config['output_dir'])/'device-state.json').read_text())
        physical = np.linalg.inv(np.array(physical_state['world_T_articulation_root'])) @ np.array(physical_state['world_T_optical'])
        measured = probe.tf.lookup_transform('piper_base_link', config['optical_frame'], Time()).transform
        rsp = np.eye(4)
        rsp[:3, 3] = [getattr(measured.translation, a) for a in 'xyz']
        rsp[:3, :3] = Rotation.from_quat([getattr(measured.rotation, a) for a in 'xyzw']).as_matrix()
        result['rsp_physics_agreement'] = {
            'position_error_m': float(np.linalg.norm(physical[:3, 3]-rsp[:3, 3])),
            'rotation_error_rad': float(Rotation.from_matrix(physical[:3, :3] @ rsp[:3, :3].T).magnitude())}
        assert result['rsp_physics_agreement']['position_error_m'] < .002
        assert result['rsp_physics_agreement']['rotation_error_rad'] < .003
        paused = probe.clock; samples = len(probe.samples)
        handle, future = probe.grip(.07, 1.)
        probe.spin_for(1.)
        assert probe.clock == paused and len(probe.samples) == samples and not future.done()
        cancellation = handle.cancel_goal_async(); probe.wait(cancellation.done, 'cancel while paused', 10.)
        probe.wait(future.done, 'canceled terminal result', 10.)
        assert future.result().status == GoalStatus.STATUS_CANCELED
        assert future.result().result.completion_type == MoveGripper.Result.NONE
        result['pause_cancel'] = {'clock_ns': paused, 'new_gripper_samples': len(probe.samples)-samples,
                                  'terminal_status': future.result().status}
        isaac.stdin.write('resume\n'); isaac.stdin.flush()
        probe.wait(lambda: probe.clock > paused and len(probe.samples) > samples, 'physics resume', 30.)
        result['passed'] = True
    except Exception as error:
        result['error'] = repr(error); print(result['error'], flush=True)
    finally:
        for child in reversed(children):
            if child.poll() is None: os.killpg(child.pid, signal.SIGTERM)
        for child in reversed(children):
            try: child.wait(timeout=20.)
            except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL); child.wait()
        for log in logs: log.close()
        (args.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (args.output/'gripper-samples.json').write_text(json.dumps(probe.samples)+'\n')
        probe.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
