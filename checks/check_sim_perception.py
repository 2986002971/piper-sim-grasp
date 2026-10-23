#!/usr/bin/env python3
"""Run existing YOLO/geometry against simulated devices without starting motion."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import rclpy
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from rclpy.qos import qos_profile_sensor_data
from nankai_robocup_perception_interfaces.msg import ObjectDetection2DArray, PerceptionHealth
from std_msgs.msg import String
import yaml
from collections import Counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--devices-config', type=Path, required=True)
    parser.add_argument('--alignment-config', type=Path, required=True)
    parser.add_argument('--detector-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    children, logs, samples, detections = [], [], [], []
    latest, health, result = {}, {}, {'passed': False, 'motion_started': False}
    rclpy.init()
    node = rclpy.create_node('sim_perception_acceptance')
    def geometry(message):
        nonlocal latest
        latest = json.loads(message.data)
        samples.append(latest)
    node.create_subscription(String, '/grasp_alignment/source/geometry', geometry, 1)
    node.create_subscription(ObjectDetection2DArray, '/perception/wrist/objects/detections',
                             detections.append, qos_profile_sensor_data)
    def receive_health(message):
        if message.component.startswith('object_detector'):
            health[message.component] = {name: getattr(message, name) for name in (
                'model_revision', 'state', 'input_fps', 'output_fps', 'inference_count',
                'error_count', 'last_error_code', 'latency_p50_ms', 'latency_p95_ms')}
    detector = yaml.safe_load(args.detector_config.read_text())
    health_topic = detector['/grasp_detection/object_detector']['ros__parameters'].get(
        'health_topic', '/perception/health/components')
    node.create_subscription(PerceptionHealth, health_topic, receive_health, 10)
    def start(command, name):
        log = (args.output/(name+'.log')).open('w')
        logs.append(log)
        children.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    def wait(predicate, label, seconds=120.):
        print('Waiting: '+label, flush=True)
        deadline, progress = time.monotonic()+seconds, 0.
        while not predicate():
            rclpy.spin_once(node, timeout_sec=.02)
            for child in children:
                if child.poll() is not None: raise RuntimeError(f'Child exited: {child.args}, code={child.returncode}')
            now = time.monotonic()
            if now > deadline: raise TimeoutError(label+': '+str(latest.get('reason', 'waiting')))
            if now-progress > 10.:
                print('Geometry: '+str(latest.get('reason', 'waiting')), flush=True)
                progress = now
    try:
        repo = Path(__file__).resolve().parents[1]
        start(['bash', str(repo/'runtime/isaac/run-isaac-ros.sh'), str(args.devices_config)], 'isaac')
        start(['ros2', 'launch', 'piper_sim_devices', 'devices.launch.py', 'config:='+str(args.devices_config)], 'devices')
        start(['ros2', 'launch', 'nankai_robocup_manipulation', 'grasp_alignment.launch.py',
               'config:='+str(args.alignment_config), 'use_sim_time:=true'], 'alignment')
        start(['ros2', 'run', 'nankai_robocup_object_perception', 'object_detector_node', '--ros-args',
               '-r', '__ns:=/grasp_detection', '-r', '__node:=object_detector',
               '--params-file', str(args.detector_config), '-p', 'use_sim_time:=true'], 'detector')
        client = node.create_client(ChangeState, '/grasp_detection/object_detector/change_state')
        wait(client.service_is_ready, 'YOLO lifecycle service')
        for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
            request = ChangeState.Request()
            request.transition.id = transition
            future = client.call_async(request)
            wait(future.done, 'YOLO lifecycle transition', 30.)
            assert future.result().success, 'YOLO rejected lifecycle transition'
        wait(lambda: any(d.class_name == 'bottle' for m in detections for d in m.detections), 'YOLO bottle')
        wait(lambda: latest.get('fresh', False) and not latest.get('reason'), 'fresh fitted geometry')
        result.update(passed=True, geometry=latest,
                      detection_frames=len(detections),
                      models=sorted(set(m.model_revision for m in detections)),
                      detections=[{'class': d.class_name, 'confidence': float(d.confidence)}
                                  for d in detections[-1].detections])
    except Exception as error:
        result['error'] = repr(error)
        print(result['error'], flush=True)
    finally:
        # Keep inference evidence even when the supplied scene has no visible
        # bottle or fitting fails. Such a run remains failed.
        result.update(health=health, detection_frames=len(detections),
                      models=sorted(set(m.model_revision for m in detections)),
                      detection_statuses=sorted(set((int(m.status), m.error_code) for m in detections)),
                      detected_classes=dict(Counter(d.class_name for m in detections for d in m.detections)))
        for child in reversed(children):
            if child.poll() is None: os.killpg(child.pid, signal.SIGINT)
        for child in reversed(children):
            try: child.wait(timeout=20.)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for log in logs: log.close()
        (args.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (args.output/'geometry.json').write_text(json.dumps(samples, indent=2)+'\n')
        node.destroy_node()
        rclpy.shutdown()
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
