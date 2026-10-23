#!/usr/bin/env python3
"""Build the simulation ROS model from the external macro model and D405 assembly."""
import argparse
import copy
from pathlib import Path
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET


def resolve_meshes(robot, directory, main_source):
    prefix = 'package://nankai_robocup_arm_control/meshes/piper_x/'
    resources = main_source/'src/piper/pyagxarm/integration/reference_piper_x/piper_x/meshes'
    for mesh in robot.iter('mesh'):
        value = mesh.get('filename')
        if value.startswith(prefix):
            path = resources/value[len(prefix):]
        elif value.startswith('package://'):
            raise ValueError('Unknown model mesh package: '+value)
        else:
            path = Path(unquote(urlparse(value).path)) if value.startswith('file://') else Path(value)
            if not path.is_absolute(): path = directory/path
        if not path.is_file(): raise FileNotFoundError(path)
        mesh.set('filename', path.resolve().as_uri())


def prepare(main_source, assembly_urdf, output):
    main_source, assembly_urdf, output = map(lambda p: Path(p).resolve(), (main_source, assembly_urdf, output))
    nominal = main_source/'src/manipulation/nankai_robocup_arm_control/config/robot.urdf'
    robot = ET.parse(nominal).getroot()
    resolve_meshes(robot, nominal.parent, main_source)
    # Keep USD's physical body names. The public base has the same origin and
    # orientation, represented as a fixed RSP root rather than a second TF owner.
    robot.find("link[@name='piper_base_link']").set('name', 'base_link')
    robot.find("joint[@name='joint1']/parent").set('link', 'base_link')
    ET.SubElement(robot, 'link', name='piper_base_link')
    root = ET.SubElement(robot, 'joint', name='sim_base_alias', type='fixed')
    ET.SubElement(root, 'parent', link='piper_base_link')
    ET.SubElement(root, 'child', link='base_link')
    assembly = ET.parse(assembly_urdf).getroot()
    resolve_meshes(assembly, assembly_urdf.parent, main_source)
    for i in range(1, 9):
        name = f'joint{i}'
        source = robot.find(f"joint[@name='{name}']")
        target = assembly.find(f"joint[@name='{name}']")
        if source is None or target is None: raise ValueError('Missing articulation joint: '+name)
        for tag in ('parent', 'child'):
            if source.find(tag).attrib != target.find(tag).attrib:
                raise ValueError(f'{name}: assembly {tag} differs from the macro model')
        for tag in ('origin', 'axis', 'limit'):
            existing = target.find(tag)
            if existing is not None: target.remove(existing)
            item = source.find(tag)
            if item is not None: target.append(copy.deepcopy(item))
    # The eight robot joints follow the macro model. Camera/support geometry
    # remains in the imported assembly; the optical TF has its own static owner.
    output.mkdir(parents=True, exist_ok=False)
    for name, definition in (('robot.urdf', robot), ('assembly.urdf', assembly)):
        ET.indent(definition)
        ET.ElementTree(definition).write(output/name, encoding='utf-8', xml_declaration=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-source', type=Path, required=True)
    parser.add_argument('--assembly-urdf', type=Path, required=True)
    parser.add_argument('--model-output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.main_source, args.assembly_urdf, args.model_output))


if __name__ == '__main__': main()
