"""Run upstream preposition or Pick, recording measured simulation evidence."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.msg import GripperCommand
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from nankai_robocup_interfaces.action import Pick
from nankai_robocup_interfaces.msg import TcpTrackingState
from nankai_robocup_perception_interfaces.msg import ObjectDetection2DArray
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.utilities import remove_ros_args
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import Trigger


class ExperimentCancelled(Exception):
    pass


class Experiment(Node):
    def __init__(self, namespace, arm_namespace, task, target_class):
        super().__init__('sim_experiment')
        self.namespace, self.task, self.target_class = namespace.rstrip('/'), task, target_class
        self.geometry, self.result, self.tracking, self.detection = {}, '', None, None
        self.cancelled = self.running = False
        self.samples = []
        self.gripper_command_sent = False
        self.goal_handle = self.goal_future = self.result_future = None
        self.pick = ActionClient(self, Pick, '/manipulation/pick') if task == 'pick' else None

        def cancel(request, response):
            self.cancelled = True
            response.success, response.message = True, 'Cancellation requested'
            return response
        self.create_service(Trigger, '~/cancel', cancel)

        def record(kind, value):
            self.samples.append({'sim_time_ns': self.get_clock().now().nanoseconds,
                                 'wall_time_s': time.monotonic(), 'kind': kind, 'value': value})
        self.record = record
        def gripper_command(message):
            self.gripper_command_sent = True
            record('gripper_command', {'width_m': message.position, 'max_effort_n': message.max_effort})
        self.create_subscription(GripperCommand, '/isaac/piper/gripper_command', gripper_command, 10)

        def text(kind, message):
            value = json.loads(message.data) if kind == 'geometry' else message.data
            setattr(self, kind, value)
            record(kind, value)
        if task == 'preposition':
            for kind in ('geometry', 'result'):
                self.create_subscription(String, self.namespace+'/source/'+kind,
                                         lambda m, k=kind: text(k, m), 1)
        else:
            self.create_subscription(ObjectDetection2DArray, '/perception/wrist/objects/detections',
                                     lambda m: setattr(self, 'detection', m), qos_profile_sensor_data)

        def tracking(message):
            self.tracking = message
            record('tracking', {'health': message.health, 'converged': message.converged,
                               'reason': message.reason, 'translation_error': message.translation_error,
                               'rotation_error': message.rotation_error})
        self.create_subscription(TcpTrackingState, arm_namespace+'/state', tracking, 1)
        def grip(message):
            record('gripper', {'stamp_ns': message.header.stamp.sec*10**9+message.header.stamp.nanosec,
                               'names': list(message.name), 'width_m': list(message.position),
                               'force_n': list(message.effort)})
        self.create_subscription(JointState, arm_namespace+'/gripper_state', grip, 10)

    def spin(self, check=True):
        rclpy.spin_once(self, timeout_sec=.01)
        if check and self.cancelled: raise ExperimentCancelled()
        if check and self.running and self.tracking and self.tracking.health == TcpTrackingState.FAULT:
            raise RuntimeError('arm: '+self.tracking.reason)
        if check and self.running and self.result.startswith('FAULT'): raise RuntimeError(self.result)

    def wait(self, predicate, label, timeout=180., check=True):
        self.get_logger().info('Waiting: '+label)
        end = time.monotonic()+timeout if timeout else None
        while not predicate():
            self.spin(check)
            if end is not None and time.monotonic() > end: raise TimeoutError(label)

    def call(self, topic, request=None, cleanup=False):
        request = request or Trigger.Request()
        kind = ChangeState if isinstance(request, ChangeState.Request) else Trigger
        client = self.create_client(kind, topic)
        try:
            if cleanup and not client.wait_for_service(timeout_sec=1.): return False
            self.wait(client.service_is_ready, topic, 30., check=not cleanup)
            future = client.call_async(request)
            self.wait(future.done, topic+' response', 10., check=not cleanup)
            response = future.result()
            if not response.success and not cleanup:
                raise RuntimeError(topic+': '+getattr(response, 'message', 'request rejected'))
            return response.success
        finally:
            self.destroy_client(client)

    def run(self):
        # Construct the business subscriber before configuring/activating the
        # volatile detector. An Action server alone does not mean it has input.
        if self.task == 'pick': self.wait(self.pick.server_is_ready, 'Pick action')
        for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            request = ChangeState.Request(); request.transition.id = transition
            self.call('/grasp_detection/object_detector/change_state', request)
        self.wait(lambda: self.tracking is not None and
                  (self.tracking.measurement_stamp.sec or self.tracking.measurement_stamp.nanosec),
                  'actual arm feedback')
        if self.task == 'preposition':
            self.wait(lambda: self.geometry.get('fresh', False), 'fitted geometry')
            self.running = True
            self.call(self.namespace+'/source/start')
            self.wait(lambda: self.result.split(':', 1)[0] == 'PREPOSITION_REACHED',
                      'preposition convergence', timeout=None)
            return {'passed': True, 'task': self.task, 'reached': self.result}
        self.wait(lambda: self.detection is not None and self.detection.is_inference_frame and
                  self.detection.status != self.detection.STATUS_ERROR,
                  'first detector inference')
        self.record('detector_input', {'stamp_ns': self.detection.header.stamp.sec*10**9+self.detection.header.stamp.nanosec,
                                      'status': int(self.detection.status), 'error_code': self.detection.error_code})
        self.running = True
        self.goal_future = self.pick.send_goal_async(Pick.Goal(target_class=self.target_class),
            feedback_callback=lambda f: self.record('pick_stage', int(f.feedback.stage)))
        self.wait(self.goal_future.done, 'Pick goal accepted')
        self.goal_handle = self.goal_future.result()
        if not self.goal_handle.accepted: raise RuntimeError('Pick goal rejected')
        self.result_future = self.goal_handle.get_result_async()
        self.wait(self.result_future.done, 'Pick terminal result', timeout=None)
        response = self.result_future.result()
        return {'passed': response.status == GoalStatus.STATUS_SUCCEEDED and response.result.code == Pick.Result.OK,
                'task': self.task, 'action_status': response.status, 'code': response.result.code,
                'message': response.result.message}

    def cleanup(self):
        if self.task == 'preposition': return self.call(self.namespace+'/source/cancel', cleanup=True)
        if self.goal_handle is None and self.goal_future is not None:
            self.wait(self.goal_future.done, 'pending Pick acceptance', 10., check=False)
            self.goal_handle = self.goal_future.result()
        if self.goal_handle is None or not self.goal_handle.accepted: return True
        if self.result_future is None: self.result_future = self.goal_handle.get_result_async()
        if not self.result_future.done():
            future = self.goal_handle.cancel_goal_async()
            self.wait(future.done, 'Pick cancellation accepted', 10., check=False)
            self.wait(self.result_future.done, 'Pick cancellation completed', 10., check=False)
        return True


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-namespace', default='/grasp_alignment')
    parser.add_argument('--driver-namespace', default='/piper/arm')
    parser.add_argument('--task', choices=('preposition', 'pick'), default='preposition')
    parser.add_argument('--target-class', default='bottle')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--device-state', type=Path)
    options = parser.parse_args(remove_ros_args(args=sys.argv)[1:])
    rclpy.init(args=args)
    node = Experiment(options.session_namespace, options.driver_namespace, options.task, options.target_class)
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: setattr(node, 'cancelled', True))
    result = {'passed': False, 'task': options.task}
    def physical_state():
        if options.device_state and options.device_state.is_file():
            return json.loads(options.device_state.read_text())
        return None
    initial = physical_state()
    try:
        result = node.run()
    except ExperimentCancelled:
        result['outcome'] = 'cancelled'
    except Exception as error:
        result['error'] = str(error); node.get_logger().error(str(error))
    finally:
        try: result['cancel_accepted'] = node.cleanup()
        except Exception as error: result.update(cancel_accepted=False, cleanup_error=str(error))
        result['gripper_command_sent'] = node.gripper_command_sent
        result['physical_state_before'], result['physical_state_after'] = initial, physical_state()
        options.output_dir.mkdir(parents=True, exist_ok=True)
        (options.output_dir/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (options.output_dir/'samples.json').write_text(json.dumps(node.samples)+'\n')
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
    return 0 if result['cancel_accepted'] and (result['passed'] or result.get('outcome') == 'cancelled') else 1
