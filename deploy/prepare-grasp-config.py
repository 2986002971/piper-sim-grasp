#!/usr/bin/env python3
"""Generate simulation-side model and ROS configurations from reviewed inputs."""
import argparse
import copy
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial.transform import Rotation
import yaml


def origin(element):
    value = element.find('origin')
    result = np.eye(4)
    if value is not None:
        result[:3, 3] = np.fromstring(value.get('xyz', '0 0 0'), sep=' ')
        result[:3, :3] = Rotation.from_euler('xyz', np.fromstring(
            value.get('rpy', '0 0 0'), sep=' ')).as_matrix()
    return result


def add_device_collisions(robot, semantic, assembly_urdf):
    assembly = ET.parse(assembly_urdf).getroot()
    parents = {j.find('child').get('link'): j for j in assembly.findall('joint')}
    for name in ('d405_link', 'd405_bracket'):
        pose, link = np.eye(4), name
        while link != 'Link6':
            joint = parents[link]
            if joint.get('type') != 'fixed':
                raise ValueError('Camera assembly must be fixed to Link6')
            pose = origin(joint) @ pose
            link = joint.find('parent').get('link')
        source = assembly.find(f"link[@name='{name}']")
        target_name = 'sim_'+name
        target = ET.SubElement(robot, 'link', name=target_name)
        collisions = source.findall('collision')
        if name == 'd405_bracket' and not collisions:
            collisions = source.findall('visual')
        for collision in collisions:
            item = copy.deepcopy(collision)
            item.tag = 'collision'
            material = item.find('material')
            if material is not None: item.remove(material)
            for mesh in item.iter('mesh'):
                mesh.set('filename', (assembly_urdf.parent/mesh.get('filename')).resolve().as_uri())
            target.append(item)
        joint = ET.SubElement(robot, 'joint', name=target_name+'_joint', type='fixed')
        ET.SubElement(joint, 'parent', link='link6')
        ET.SubElement(joint, 'child', link=target_name)
        ET.SubElement(joint, 'origin', xyz=' '.join(map(str, pose[:3, 3])),
                      rpy=' '.join(map(str, Rotation.from_matrix(pose[:3, :3]).as_euler('xyz'))))
    # Recompute only fixed-assembly and directly adjacent exclusions, using
    # the same topological rule as the main model generator.
    for item in semantic.findall('disable_collisions'): semantic.remove(item)
    clusters = {l.get('name'): {l.get('name')} for l in robot.findall('link')}
    edges = []
    for j in robot.findall('joint'):
        a, b = j.find('parent').get('link'), j.find('child').get('link')
        if j.get('type') == 'fixed':
            merged = clusters[a] | clusters[b]
            for member in merged: clusters[member] = merged
        else: edges.append((a, b))
    exclusions = {}
    for members in clusters.values():
        for pair in combinations(sorted(members), 2): exclusions[pair] = 'FixedAssembly'
    for a, b in edges:
        for left in clusters[a]:
            for right in clusters[b]: exclusions[tuple(sorted((left, right)))] = 'Adjacent'
    for (a, b), reason in sorted(exclusions.items()):
        ET.SubElement(semantic, 'disable_collisions', link1=a, link2=b, reason=reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-source', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--assembly-urdf', type=Path, required=True)
    parser.add_argument('--scene-manifest', type=Path, required=True)
    parser.add_argument('--model-output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--engine-dir', type=Path, required=True)
    args = parser.parse_args()
    profile = yaml.safe_load(args.profile.read_text())
    main_source, data = args.main_source.resolve(), args.data_dir.resolve()
    config = data/'config'
    config.mkdir(parents=True, exist_ok=True)
    pyagx = main_source/'src/piper/pyagxarm'
    driver = yaml.safe_load((pyagx/'config/w_servo.yaml').read_text())
    driver['/**']['ros__parameters']['gripper_max_width_m'] = .1
    driver_file = config/'model-driver.yaml'
    driver_file.write_text(yaml.safe_dump(driver, sort_keys=False))
    model = args.model_output.resolve()
    subprocess.run([sys.executable, str(pyagx/'checks/prepare_w_model.py'),
                    '--driver-file', str(driver_file), '--output', str(model)], check=True)
    robot = ET.parse(model/'robot.urdf').getroot()
    semantic = ET.parse(model/'robot.srdf').getroot()
    add_device_collisions(robot, semantic, args.assembly_urdf.resolve())
    ET.ElementTree(robot).write(model/'robot.urdf', encoding='utf-8', xml_declaration=True)
    ET.ElementTree(semantic).write(model/'robot.srdf', encoding='utf-8', xml_declaration=True)
    scene = json.loads(args.scene_manifest.read_text())
    boxes = profile['scene_boxes']
    (config/'scene.yaml').write_text(yaml.safe_dump({'boxes': boxes}, sort_keys=False))
    geometry = main_source/'src/manipulation/nankai_robocup_grasp_geometry/config'
    alignment = yaml.safe_load((geometry/'alignment.example.yaml').read_text())
    alignment['source'].update(bottle_height=float(scene['bottle_dimensions_m'][2]),
                               bottle_radius=profile['bottle_radius_m'],
                               execution_timeout=profile['execution_timeout_s'])
    alignment['w'].update(urdf_file=str(model/'robot.urdf'), srdf_file=str(model/'robot.srdf'),
                          limits_file=str(model/'joint_limits.yaml'), scene_file=str(config/'scene.yaml'))
    (config/'alignment.yaml').write_text(yaml.safe_dump(alignment, sort_keys=False))
    detector = yaml.safe_load((geometry/'d405_yolo.yaml').read_text())
    parameters = detector['object_detector']['ros__parameters']
    assets = args.engine_dir.resolve()
    parameters.update(engine_path=str(assets/'model.engine'),
                      manifest_path=str(assets/'model.manifest.yaml'),
                      class_labels_path=str(assets/'classes.txt'))
    # Parameter-file node keys must include the launch namespace in Humble.
    detector['/grasp_detection/object_detector'] = detector.pop('object_detector')
    (config/'detector.yaml').write_text(yaml.safe_dump(detector, sort_keys=False))
    print(config)


if __name__ == '__main__': main()
