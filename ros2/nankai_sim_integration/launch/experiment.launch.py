"""Upstream Source or Pick plus detector and the simulation experiment client."""
from pathlib import Path
import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.events import Shutdown
from launch.event_handlers import OnProcessExit
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def setup(context):
    def arg(name): return LaunchConfiguration(name).perform(context)
    task = arg('task')
    if task not in ('preposition', 'pick'): raise ValueError('Unknown experiment task')
    config = arg('pick_config') if task == 'pick' else arg('alignment_config')
    parameters = yaml.safe_load(Path(config).read_text())
    if task == 'pick':
        # Same upstream node and parameters; only process/signal ownership is
        # simulation-side. No business phases or completion tests are copied.
        business = Node(package='nankai_sim_integration', executable='pick_business',
            namespace='/manipulation', parameters=[dict(parameters['source'], **parameters['pick'],
                arm_namespace=arg('driver_namespace'), feedback_tcp_frame='grasp_tcp', use_sim_time=True)],
            additional_env={'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}, output='screen')
    else:
        command = ['ros2', 'launch', 'nankai_robocup_manipulation', 'grasp_alignment.launch.py',
                   'config:='+config, 'use_sim_time:=true', 'driver_namespace:='+arg('driver_namespace'),
                   'session_namespace:='+arg('session_namespace')]
        business = ExecuteProcess(cmd=command, name='grasp_business', output='screen')
    detector = Node(package='nankai_robocup_object_perception', executable='object_detector_node',
                    name='object_detector', namespace='/grasp_detection',
                    parameters=[arg('detector_config'), {'use_sim_time': True}], output='screen')
    arguments = ['--task', task, '--target-class', parameters['source']['target_class'],
                 '--driver-namespace', arg('driver_namespace'),
                 '--session-namespace', arg('session_namespace'), '--output-dir', arg('output_dir')]
    if arg('device_state'): arguments += ['--device-state', arg('device_state')]
    coordinator = Node(package='nankai_sim_integration', executable='experiment', name='sim_experiment',
                       parameters=[{'use_sim_time': True}], arguments=arguments, output='screen')
    def exited(event, ctx):
        if not ctx.is_shutdown:
            if event.returncode: raise RuntimeError(f'Experiment component exited ({event.returncode})')
            return [EmitEvent(event=Shutdown(reason=f'Experiment process exited ({event.returncode})'))]
        return []
    return [RegisterEventHandler(OnProcessExit(on_exit=exited)), business, detector, coordinator]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('alignment_config'), DeclareLaunchArgument('pick_config'),
        DeclareLaunchArgument('detector_config'), DeclareLaunchArgument('output_dir'),
        DeclareLaunchArgument('task', default_value='preposition'),
        DeclareLaunchArgument('driver_namespace', default_value='/piper/arm'),
        DeclareLaunchArgument('session_namespace', default_value='/grasp_alignment'),
        DeclareLaunchArgument('device_state', default_value=''), OpaqueFunction(function=setup)])
