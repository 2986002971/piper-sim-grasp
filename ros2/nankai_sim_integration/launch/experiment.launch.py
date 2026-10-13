"""Run existing business nodes against independently running simulated devices."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.events import Shutdown
from launch.event_handlers import OnProcessExit
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def setup(context):
    def arg(name): return LaunchConfiguration(name).perform(context)
    # The upstream launch has a global process-exit guard. Keep its launch
    # context separate so our coordinator's normal exit is not a business fault.
    business = ExecuteProcess(cmd=[
        'ros2', 'launch', 'nankai_robocup_grasp_geometry', 'grasp_alignment.launch.py',
        'config:='+arg('alignment_config'), 'use_sim_time:=true',
        'driver_namespace:='+arg('driver_namespace'),
        'session_namespace:='+arg('session_namespace')], name='grasp_business', output='screen')
    detector = Node(package='nankai_robocup_object_perception', executable='object_detector_node',
                    name='object_detector', namespace='/grasp_detection',
                    parameters=[arg('detector_config'), {'use_sim_time': True}], output='screen')
    coordinator = Node(package='nankai_sim_integration', executable='experiment', name='sim_experiment',
                       parameters=[{'use_sim_time': True}], arguments=[
                           '--session-namespace', arg('session_namespace'),
                           '--output-dir', arg('output_dir')], output='screen')
    def exited(event, ctx):
        if not ctx.is_shutdown:
            if event.returncode:
                raise RuntimeError(f'Experiment component exited ({event.returncode})')
            return [EmitEvent(event=Shutdown(reason=f'Experiment process exited ({event.returncode})'))]
        return []
    return [RegisterEventHandler(OnProcessExit(on_exit=exited)), business, detector, coordinator]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('alignment_config'), DeclareLaunchArgument('detector_config'),
        DeclareLaunchArgument('output_dir'),
        DeclareLaunchArgument('driver_namespace', default_value='/piper_l/standalone'),
        DeclareLaunchArgument('session_namespace', default_value='/grasp_alignment'),
        OpaqueFunction(function=setup)])
