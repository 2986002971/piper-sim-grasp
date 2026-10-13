"""Keep a live ROS subscriber in the device session; readiness requires actual data."""
import json
from pathlib import Path
import sys
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo, JointState
from rosgraph_msgs.msg import Clock
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
rclpy.init()
node = rclpy.create_node('piper_device_readiness')
seen = set()
subscriptions = []
for key, kind, topic in [('clock', Clock, '/clock'), ('rgb', Image, config['rgb_topic']),
                         ('depth', Image, config['depth_topic']), ('info', CameraInfo, config['camera_info_topic']),
                         ('joints', JointState, config['driver_namespace']+'/joint_states')]:
    def receive(message, key=key):
        seen.add(key)
        if len(seen) == 5 and not output.exists():
            output.write_text(json.dumps({'received': sorted(seen)})+'\n')
    subscriptions.append(node.create_subscription(kind, topic, receive, qos_profile_sensor_data))
try: rclpy.spin(node)
except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException): pass
finally:
    node.destroy_node()
    if rclpy.ok(): rclpy.shutdown()
