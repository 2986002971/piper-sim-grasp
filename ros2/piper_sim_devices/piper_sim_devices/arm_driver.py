"""Relay the existing arm topics to standard Isaac Bridge messages."""
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory

from .arm_contract import ARM_JOINTS, feedback_indices, joint_limits, position_target


class IsaacArmDriver(Node):
    def __init__(self):
        super().__init__('isaac_arm_driver')
        urdf = self.declare_parameter('urdf_file', '').value
        command = self.declare_parameter('bridge_command_topic', '/isaac/piper/joint_command').value
        feedback = self.declare_parameter('bridge_feedback_topic', '/isaac/piper/joint_states').value
        self.limits = joint_limits(urdf)
        self.feedback_received = False
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.target_pub = self.create_publisher(JointState, command, qos)
        self.state_pub = self.create_publisher(JointState, 'joint_states', qos)
        self.create_subscription(JointTrajectory, 'command', self.on_command, qos)
        self.create_subscription(JointState, feedback, self.on_feedback, qos)

    def on_command(self, message):
        if not self.feedback_received:
            self.get_logger().warning('Waiting for measured Isaac joint feedback', throttle_duration_sec=2.)
            return
        try:
            positions = position_target(message, self.limits)
        except ValueError as error:
            self.get_logger().error(str(error), throttle_duration_sec=2.)
            return
        target = JointState()
        target.header = message.header
        target.name, target.position = list(ARM_JOINTS), positions
        self.target_pub.publish(target)

    def on_feedback(self, message):
        try:
            indices = feedback_indices(message)
        except ValueError as error:
            self.get_logger().error(str(error), throttle_duration_sec=2.)
            return
        measured = JointState()
        measured.header = message.header
        measured.name = [message.name[i] for i in indices]
        measured.position = [message.position[i] for i in indices]
        for field in ('velocity', 'effort'):
            values = getattr(message, field)
            if len(values) == len(message.name):
                setattr(measured, field, [values[i] for i in indices])
        self.feedback_received = True
        self.state_pub.publish(measured)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = IsaacArmDriver()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
