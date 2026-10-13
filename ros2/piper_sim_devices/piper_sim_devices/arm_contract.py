"""The real driver's single-position-target contract, without ROS dependencies."""
import math
import xml.etree.ElementTree as ET

ARM_JOINTS = tuple(f'joint{i}' for i in range(1, 7))
FEEDBACK_JOINTS = ARM_JOINTS + ('joint7', 'joint8')


def joint_limits(urdf_file):
    joints = {j.attrib['name']: j for j in ET.parse(urdf_file).getroot().findall('joint')}
    return {name: (float(joints[name].find('limit').attrib['lower']),
                   float(joints[name].find('limit').attrib['upper'])) for name in ARM_JOINTS}


def position_target(message, limits):
    if len(message.points) != 1:
        raise ValueError('command requires exactly one trajectory point')
    if len(message.joint_names) != 6 or set(message.joint_names) != set(ARM_JOINTS):
        raise ValueError('command requires six unique arm joint names')
    point = message.points[0]
    if len(point.positions) != 6 or point.velocities or point.accelerations or point.effort:
        raise ValueError('command accepts six positions only')
    by_name = dict(zip(message.joint_names, point.positions))
    positions = [float(by_name[name]) for name in ARM_JOINTS]
    for name, position in zip(ARM_JOINTS, positions):
        low, high = limits[name]
        if not math.isfinite(position) or not low <= position <= high:
            raise ValueError(f'{name}: target outside finite URDF limits')
    # Header and time_from_start do not create an execution queue in the real driver.
    return positions


def feedback_indices(message):
    if len(message.name) != len(message.position) or len(set(message.name)) != len(message.name):
        raise ValueError('malformed simulator joint feedback')
    if not set(ARM_JOINTS) <= set(message.name):
        raise ValueError('simulator feedback is missing an arm joint')
    indices = [message.name.index(name) for name in FEEDBACK_JOINTS if name in message.name]
    if not all(math.isfinite(message.position[i]) for i in indices):
        raise ValueError('non-finite measured joint position')
    return indices
