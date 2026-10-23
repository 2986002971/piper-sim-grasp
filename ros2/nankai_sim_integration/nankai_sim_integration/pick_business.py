"""Run the upstream Pick node with simulator-owned, orderly ROS shutdown."""
import signal

import rclpy
from rclpy.signals import SignalHandlerOptions
from nankai_robocup_manipulation.pick_node import PickNode


def main():
    # Humble's async signal handler can invalidate the context while a sensor
    # callback is taking a message. Finish that callback before destroying ROS.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    for kind in (signal.SIGINT, signal.SIGTERM): signal.signal(kind, stop)
    node = PickNode()
    try:
        while not stopping and rclpy.ok(): rclpy.spin_once(node, timeout_sec=.1)
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
