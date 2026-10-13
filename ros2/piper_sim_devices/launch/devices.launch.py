"""ROS adapters only; Isaac is owned by runtime, RSP by experiments/checks."""
from pathlib import Path
import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.events import Shutdown
from launch.event_handlers import OnProcessExit
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def setup(context):
    config_path = Path(LaunchConfiguration('config').perform(context)).resolve()
    config = yaml.safe_load(config_path.read_text())
    urdf = Path(config['urdf_file'])
    if not urdf.is_absolute(): urdf = config_path.parent / urdf
    relay = Node(package='piper_sim_devices', executable='isaac_arm_driver',
                 namespace=config.get('driver_namespace', '/piper_l/standalone'),
                 parameters=[{'use_sim_time': True, 'urdf_file': str(urdf),
                              'bridge_command_topic': config.get('bridge_command_topic', '/isaac/piper/joint_command'),
                              'bridge_feedback_topic': config.get('bridge_feedback_topic', '/isaac/piper/joint_states')}],
                 output='screen')
    mount = Node(package='tf2_ros', executable='static_transform_publisher',
                 name='sim_camera_mount', parameters=[{'use_sim_time': True}],
                 arguments=[value for name, value in zip(
                     ('--x', '--y', '--z', '--qx', '--qy', '--qz', '--qw'),
                     map(str, config['camera_mount'])) for value in (name, value)] + [
                     '--frame-id', 'link6', '--child-frame-id', config['optical_frame']], output='screen')
    def exited(event, ctx):
        if not ctx.is_shutdown:
            return [EmitEvent(event=Shutdown(reason=f'Device process exited ({event.returncode})'))]
        return []
    return [RegisterEventHandler(OnProcessExit(on_exit=exited)), relay, mount]


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('config', description='Simulation device YAML'),
                              OpaqueFunction(function=setup)])
