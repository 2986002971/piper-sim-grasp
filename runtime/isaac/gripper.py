"""Isaac-side standard ROS gripper command: position drive with a force limit."""
import math

import numpy as np


def drive_target(width, force, limits):
    if not math.isfinite(width) or not 0 <= width <= .1:
        raise ValueError('gripper width must be between 0 and 0.1 m')
    if not math.isfinite(force) or force < 0:
        raise ValueError('gripper max_effort must be finite and nonnegative')
    return np.array([width/2., -width/2.]), np.minimum(force, limits)


class Gripper:
    def __init__(self, robot, indices, topic):
        import rclpy
        from control_msgs.msg import GripperCommand
        self.rclpy = rclpy
        rclpy.init()
        self.node = rclpy.create_node('isaac_gripper')
        self.controller = robot.get_articulation_controller()
        self.indices = np.asarray(indices)
        self.limits = np.asarray(self.controller.get_max_efforts())[self.indices].copy()
        self.pending = None
        self.state = {'requested_width_m': None, 'requested_force_n': None,
                      'authored_force_limits_n': self.limits.tolist()}

        def receive(message):
            try:
                positions, forces = drive_target(message.position, message.max_effort, self.limits)
            except ValueError as error:
                self.node.get_logger().error(str(error))
                return
            self.pending = positions, forces
            self.state.update(requested_width_m=message.position, requested_force_n=message.max_effort,
                              applied_force_limits_n=forces.tolist())

        self.subscription = self.node.create_subscription(GripperCommand, topic, receive, 1)

    def update(self):
        from isaacsim.core.utils.types import ArticulationAction
        # app.update continues while paused, so a cancel can replace the latched
        # target before physics resumes. Only the application thread touches PhysX.
        self.rclpy.spin_once(self.node, timeout_sec=0.)
        if self.pending is not None:
            positions, forces = self.pending
            self.pending = None
            self.controller.set_max_efforts(forces, joint_indices=self.indices)
            self.controller.apply_action(ArticulationAction(joint_positions=positions,
                                                            joint_indices=self.indices))

    def close(self):
        self.node.destroy_node()
        if self.rclpy.ok(): self.rclpy.shutdown()
