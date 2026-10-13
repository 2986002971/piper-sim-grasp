"""One preposition experiment using the existing capability services."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.utilities import remove_ros_args
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from std_msgs.msg import Int8, String
from std_srvs.srv import Trigger
from .scene_support import allow_fixed_mount_contact


class ExperimentCancelled(Exception):
    pass


class Experiment(Node):
    def __init__(self, namespace):
        super().__init__('sim_experiment')
        self.namespace = namespace.rstrip('/')
        self.geometry = {}
        self.result = ''
        self.input_status = ''
        self.servo_status = None
        self.fault = ''
        self.running = False
        self.cancelled = False
        self.samples = []
        def cancel(request, response):
            self.cancelled = True
            response.success = True
            response.message = 'Cancellation requested; Source and W cleanup follows'
            return response
        self.create_service(Trigger, '~/cancel', cancel)
        def receive(kind, message):
            value = json.loads(message.data) if kind == 'geometry' else message.data
            setattr(self, kind, value)
            self.samples.append({'sim_time_ns': self.get_clock().now().nanoseconds,
                                 'wall_time_s': time.monotonic(), 'kind': kind, 'value': value})
        for kind, msg_type, topic in [
            ('geometry', String, self.namespace+'/source/geometry'),
            ('result', String, self.namespace+'/source/result'),
            ('fault', String, self.namespace+'/m/fault'),
            ('input_status', String, '/w_servo/input_status'),
            ('servo_status', Int8, '/w_servo/servo_status')]:
            self.create_subscription(msg_type, topic, lambda m, k=kind: receive(k, m), 1)

    def spin(self, check=True):
        rclpy.spin_once(self, timeout_sec=.01)
        if check and self.cancelled:
            raise ExperimentCancelled()
        if check and self.running and (self.fault or self.result.startswith('FAULT')):
            raise RuntimeError(self.fault or self.result)

    def wait(self, predicate, label, timeout=180.):
        self.get_logger().info('Waiting: '+label)
        end = time.monotonic() + timeout
        while not predicate():
            self.spin()
            if time.monotonic() > end:
                raise TimeoutError(label)

    def call(self, topic, request=None, cleanup=False):
        request = request or Trigger.Request()
        srv_type = ChangeState if isinstance(request, ChangeState.Request) else Trigger
        client = self.create_client(srv_type, topic)
        try:
            if cleanup:
                if not client.wait_for_service(timeout_sec=1.): return False
            else:
                self.wait(client.service_is_ready, topic, 30.)
            future = client.call_async(request)
            end = time.monotonic() + (3. if cleanup else 10.)
            while not future.done() and time.monotonic() < end:
                self.spin(check=not cleanup)
            response = future.result() if future.done() else None
            if response is None or not response.success:
                if cleanup: return False
                raise RuntimeError(topic+': '+getattr(response, 'message', 'request failed or timed out'))
            return True
        finally:
            self.destroy_client(client)

    def run(self):
        allow_fixed_mount_contact(self)
        for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            request = ChangeState.Request()
            request.transition.id = transition
            self.call('/grasp_detection/object_detector/change_state', request)
        self.wait(lambda: self.geometry.get('fresh', False), 'fitted geometry')
        self.wait(lambda: self.input_status == 'zero_command', 'W initialized')
        self.call('/w_servo/start_servo')
        self.wait(lambda: self.servo_status == 0 and self.input_status == 'zero_command', 'Servo started')
        self.running = True
        self.call(self.namespace+'/source/start')
        # Source owns the configured simulation-time execution timeout. Pausing
        # must not consume a coordinator wall-clock motion deadline.
        while self.result.split(':', 1)[0] != 'PREPOSITION_REACHED':
            self.spin()
        return {'passed': True, 'reached': self.result, 'gripper_command_sent': False}


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-namespace', default='/grasp_alignment')
    parser.add_argument('--output-dir', type=Path, required=True)
    options = parser.parse_args(remove_ros_args(args=sys.argv)[1:])
    rclpy.init(args=args)
    node = Experiment(options.session_namespace)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(node, 'cancelled', True))
    result = {'passed': False, 'gripper_command_sent': False}
    try:
        result = node.run()
    except ExperimentCancelled:
        result['outcome'] = 'cancelled'
        node.get_logger().info('Operator cancelled the experiment')
    except Exception as error:
        result['error'] = str(error)
        node.get_logger().error(str(error))
    finally:
        result['cancel_accepted'] = node.call(node.namespace+'/source/cancel', cleanup=True)
        result['servo_stop_accepted'] = node.call('/w_servo/stop_servo', cleanup=True)
        options.output_dir.mkdir(parents=True, exist_ok=True)
        (options.output_dir/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (options.output_dir/'samples.json').write_text(json.dumps(node.samples)+'\n')
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
    cleaned_up = result['cancel_accepted'] and result['servo_stop_accepted']
    return 0 if cleaned_up and (result['passed'] or result.get('outcome') == 'cancelled') else 1
