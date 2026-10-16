"""Resolve a server-side profile and prepare one run's files, without building."""
import copy
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

import yaml

from runtime.config import ROOT


def profile_path(config):
    return ROOT/'config/profiles'/f"{config['profile']}.yaml"


def vector(value, size, label):
    if (not isinstance(value, list) or len(value) != size or
            not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value)):
        raise ValueError(f'{label} requires {size} finite numbers')


def load_profile(config):
    path = profile_path(config)
    try:
        profile = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(f'Invalid profile {path}: {error}') from error
    if not isinstance(profile, dict):
        raise ValueError(f'Profile must be a mapping: {path}')
    for key in ('scene_usd', 'robot_urdf', 'assembly_urdf'):
        value = profile.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f'{path}: {key} must be an absolute server path')
        if not Path(value).is_file():
            raise FileNotFoundError(f'{path}: missing {key}: {value}')
    vector(profile.get('initial_joint_positions'), 6, 'initial_joint_positions')
    vector(profile.get('camera_mount'), 7, 'camera_mount')
    if not math.isclose(sum(x*x for x in profile['camera_mount'][3:]), 1., abs_tol=1e-4):
        raise ValueError('camera_mount requires a unit quaternion')
    for key in ('driver_namespace', 'optical_frame', 'bridge_command_topic', 'bridge_feedback_topic',
                'rgb_topic', 'depth_topic', 'camera_info_topic'):
        if not isinstance(profile.get(key), str) or not profile[key]:
            raise ValueError(f'{path}: {key} requires a nonempty string')
    for key in ('physics_hz', 'camera_hz', 'bottle_height_m', 'bottle_radius_m', 'execution_timeout_s',
                'arm_drive_stiffness', 'arm_drive_damping', 'gripper_drive_stiffness', 'gripper_drive_damping'):
        value = profile.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{path}: {key} must be positive and finite')
    width = profile.get('gripper_width_m')
    if not isinstance(width, (int, float)) or not math.isfinite(width) or not 0 <= width <= .1:
        raise ValueError('gripper_width_m must be between 0 and 0.1 m')
    boxes = profile.get('scene_boxes')
    if not isinstance(boxes, list) or not boxes:
        raise ValueError('scene_boxes requires manually configured collision boxes')
    ids = set()
    for box in boxes:
        if not isinstance(box, dict) or not isinstance(box.get('id'), str) or not box['id'] or box['id'] in ids:
            raise ValueError('scene_boxes requires unique nonempty IDs')
        ids.add(box['id'])
        for key, size in (('size', 3), ('position', 3), ('quaternion', 4)):
            vector(box.get(key), size, f"scene_boxes.{box['id']}.{key}")
        if min(box['size']) <= 0 or not math.isclose(sum(x*x for x in box['quaternion']), 1., abs_tol=1e-4):
            raise ValueError('scene_boxes requires positive sizes and unit quaternions')
    # Validate initial positions against the ROS joint names, signs and limits.
    robot = ET.parse(profile['robot_urdf']).getroot()
    for i, position in enumerate(profile['initial_joint_positions'], 1):
        joint = robot.find(f"joint[@name='joint{i}']")
        limit = joint.find('limit') if joint is not None else None
        if limit is None or not float(limit.get('lower')) <= position <= float(limit.get('upper')):
            raise ValueError(f'initial_joint_positions: joint{i} is missing or outside its URDF limits')
    return profile


def model_inputs(profile):
    return {key: {'path': str(Path(profile[key]).resolve()),
                  'sha256': hashlib.sha256(Path(profile[key]).read_bytes()).hexdigest()}
            for key in ('robot_urdf', 'assembly_urdf')}


def model_directory(config):
    return Path(config['data_root'])/'models'/config['profile']


def validate_prepared(config, profile=None, main=None):
    """Read-only preflight, also used by doctor; no previous run is required."""
    profile = profile if profile is not None else load_profile(config)
    main = bool(config.get('main_project')) if main is None else main
    data = Path(config['data_root'])
    required = [data/'install/devices/local_setup.bash']
    receipt = None
    if main:
        model = model_directory(config)
        required += [data/'install'/part/'local_setup.bash' for part in ('main', 'servo', 'integration')]
        required += [model/name for name in ('robot.urdf', 'robot.srdf', 'joint_limits.yaml', 'build.json')]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError(f"Run piper build for profile {config['profile']}; missing: "+', '.join(missing))
    if main:
        receipt = json.loads((model/'build.json').read_text())
        if (receipt.get('model_inputs') != model_inputs(profile) or
                receipt.get('main_project') != str(Path(config['main_project']).resolve())):
            raise RuntimeError(f"Robot inputs changed; run piper build for profile {config['profile']}")
        engine = Path(receipt['engine_directory'])
        for name in ('model.engine', 'model.manifest.yaml', 'classes.txt'):
            if not (engine/name).is_file():
                raise RuntimeError(f'Missing {engine/name}; run piper build')
    return receipt


def resolved_robot(source, destination):
    """Resolve mesh paths in the supplied ROS model, without changing the input."""
    source = Path(source)
    tree = ET.parse(source)
    for mesh in tree.iter('mesh'):
        value = mesh.get('filename')
        if value.startswith('package://'):
            raise ValueError('robot_urdf must have resolved mesh paths: '+value)
        path = Path(unquote(urlparse(value).path)) if value.startswith('file://') else Path(value)
        path = path if path.is_absolute() else source.parent/path
        if not path.is_file():
            raise FileNotFoundError('ROS model mesh missing: '+str(path))
        mesh.set('filename', path.resolve().as_uri())
    tree.write(destination, encoding='utf-8', xml_declaration=True)


def prepare(config, directory, main=None):
    """Write a fresh configuration snapshot for start/check, never compile."""
    profile = load_profile(config)
    main = bool(config.get('main_project')) if main is None else main
    receipt = validate_prepared(config, profile, main)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    def save(name, value):
        path = directory/name
        path.write_text(yaml.safe_dump(value, sort_keys=False))
        return path
    save('profile.yaml', profile)
    data = Path(config['data_root'])
    if main:
        robot = model_directory(config)/'robot.urdf'
    else:
        robot = directory/'robot.urdf'
        resolved_robot(profile['robot_urdf'], robot)
    devices = {key: copy.deepcopy(value) for key, value in profile.items()
               if key not in ('robot_urdf', 'assembly_urdf', 'scene_boxes',
                              'bottle_height_m', 'bottle_radius_m', 'execution_timeout_s')}
    devices.update(isaac_env=config['isaac_env'], urdf_file=str(robot),
                   output_dir=str(data/'state/devices'),
                   cache_root=str(data/'cache'), gpu_lock='/tmp/piper-sim-gpu0.lock',
                   ros_domain_id=config['ros_domain_id'])
    result = {'devices': save('devices.yaml', devices), 'profile': profile}
    if main:
        model = model_directory(config)
        geometry = Path(config['main_project'])/'src/manipulation/nankai_robocup_grasp_geometry/config'
        alignment = yaml.safe_load((geometry/'alignment.example.yaml').read_text())
        # Share the reviewed geometry-to-TCP convention with the real experiment;
        # its hardware paths and execution settings do not belong to simulation.
        experiment = yaml.safe_load((geometry/'alignment.yaml').read_text())
        geometry_T_tcp = experiment['source']['geometry_T_tcp']
        vector(geometry_T_tcp, 7, 'alignment.yaml source.geometry_T_tcp')
        alignment['source']['geometry_T_tcp'] = geometry_T_tcp
        alignment['source'].update(bottle_height=profile['bottle_height_m'],
                                   bottle_radius=profile['bottle_radius_m'],
                                   execution_timeout=profile['execution_timeout_s'],
                                   optical_frame=profile['optical_frame'],
                                   depth_topic=profile['depth_topic'],
                                   camera_info_topic=profile['camera_info_topic'])
        scene = save('scene.yaml', {'boxes': profile['scene_boxes']})
        alignment['w'].update(urdf_file=str(model/'robot.urdf'), srdf_file=str(model/'robot.srdf'),
                              limits_file=str(model/'joint_limits.yaml'), scene_file=str(scene))
        result['alignment'] = save('alignment.yaml', alignment)
        detector = yaml.safe_load((geometry/'d405_yolo.yaml').read_text())
        parameters = detector['object_detector']['ros__parameters']
        engine = Path(receipt['engine_directory'])
        parameters.update(engine_path=str(engine/'model.engine'),
                          manifest_path=str(engine/'model.manifest.yaml'),
                          class_labels_path=str(engine/'classes.txt'))
        parameters['streams.wrist.input_image_topic'] = profile['rgb_topic']
        detector['/grasp_detection/object_detector'] = detector.pop('object_detector')
        result['detector'] = save('detector.yaml', detector)
    return result
