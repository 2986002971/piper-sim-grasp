#!/usr/bin/env python3
"""Build the simulation-side ROS robot model from the existing main model."""
import argparse
import copy
from itertools import combinations
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

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


def validate_robot_geometry(robot, assembly_urdf):
    """Refuse mixing a ROS arm with a differently assembled USD source."""
    assembly = ET.parse(assembly_urdf).getroot()
    for joint in robot.findall('joint'):
        name = joint.get('name')
        other = assembly.find(f"joint[@name='{name}']")
        if name in ('world_to_base_link', 'grasp_tcp_joint'):
            continue
        if other is None or joint.get('type') != other.get('type'):
            raise ValueError(f'ROS/assembly joint differs: {name}')
        for tag in ('parent', 'child', 'axis'):
            left, right = joint.find(tag), other.find(tag)
            if (left is None) != (right is None) or (left is not None and left.attrib != right.attrib):
                raise ValueError(f'ROS/assembly {name} {tag} differs')
        if not np.allclose(origin(joint), origin(other), atol=1e-9, rtol=0):
            raise ValueError(f'ROS/assembly {name} origin differs')
    for link in robot.findall('link'):
        other = assembly.find(f"link[@name='{link.get('name')}']")
        for tag in ('visual', 'collision'):
            items = link.findall(tag)
            peers = other.findall(tag) if other is not None else []
            if len(items) != len(peers):
                raise ValueError(f'ROS/assembly geometry differs: {link.get("name")}')
            for item, peer in zip(items, peers):
                mesh, counterpart = item.find('geometry/mesh'), peer.find('geometry/mesh')
                if not np.allclose(origin(item), origin(peer), atol=1e-9, rtol=0):
                    raise ValueError('ROS/assembly mesh origin differs')
                if mesh is None or counterpart is None:
                    raise ValueError('Expected official arm mesh geometry')
                if mesh.get('scale', '1 1 1') != counterpart.get('scale', '1 1 1'):
                    raise ValueError('ROS/assembly mesh scale differs')
                source = Path(unquote(urlparse(mesh.get('filename')).path))
                target = assembly_urdf.parent/counterpart.get('filename')
                if source.read_bytes() != target.read_bytes():
                    raise ValueError(f'ROS/assembly mesh differs: {link.get("name")}')


def add_device_collisions(robot, semantic, assembly_urdf):
    assembly = ET.parse(assembly_urdf).getroot()
    parents = {j.find('child').get('link'): j for j in assembly.findall('joint')}
    for name in ('d405_link', 'd405_bracket'):
        pose, link = np.eye(4), name
        while link != 'link6':
            joint = parents[link]
            if joint.get('type') != 'fixed':
                raise ValueError('Camera assembly must be fixed to link6')
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
    # Operator confirmed the real mount's clearance and mechanical constraint:
    # this pair cannot contact during wrist rotation. Keep the bracket geometry
    # and all its other collision checks; this exemption belongs to simulation.
    exclusions[('link4', 'sim_d405_bracket')] = 'Never'
    for (a, b), reason in sorted(exclusions.items()):
        ET.SubElement(semantic, 'disable_collisions', link1=a, link2=b, reason=reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-source', type=Path, required=True)
    parser.add_argument('--assembly-urdf', type=Path, required=True)
    parser.add_argument('--model-output', type=Path, required=True)
    args = parser.parse_args()
    main_source = args.main_source.resolve()
    pyagx = main_source/'src/piper/pyagxarm'
    driver = yaml.safe_load((pyagx/'config/w_servo.yaml').read_text())
    driver['/**']['ros__parameters']['gripper_max_width_m'] = .1
    model = args.model_output.resolve()
    model.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='model-driver-', dir=model.parent) as temporary:
        driver_file = Path(temporary)/'driver.yaml'
        driver_file.write_text(yaml.safe_dump(driver, sort_keys=False))
        subprocess.run([sys.executable, str(pyagx/'checks/prepare_w_model.py'),
                        '--driver-file', str(driver_file), '--output', str(model)], check=True)
    robot = ET.parse(model/'robot.urdf').getroot()
    semantic = ET.parse(model/'robot.srdf').getroot()
    validate_robot_geometry(robot, args.assembly_urdf.resolve())
    add_device_collisions(robot, semantic, args.assembly_urdf.resolve())
    ET.ElementTree(robot).write(model/'robot.urdf', encoding='utf-8', xml_declaration=True)
    ET.ElementTree(semantic).write(model/'robot.srdf', encoding='utf-8', xml_declaration=True)
    print(model)


if __name__ == '__main__': main()
