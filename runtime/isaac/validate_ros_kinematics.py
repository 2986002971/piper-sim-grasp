"""Check authored USD joints against ROS without moving bodies or joint anchors."""
import math
import xml.etree.ElementTree as ET

import numpy as np
from pxr import Gf, UsdPhysics


def urdf_origin(element):
    pose = np.eye(4)
    origin = element.find('origin')
    if origin is None:
        return pose
    pose[:3, 3] = np.fromstring(origin.get('xyz', '0 0 0'), sep=' ')
    r, p, y = np.fromstring(origin.get('rpy', '0 0 0'), sep=' ')
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    pose[:3, :3] = [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                    [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                    [-sp, cp*sr, cp*cr]]
    return pose


def anchor(joint, side):
    position = getattr(joint, f'GetLocalPos{side}Attr')().Get()
    rotation = getattr(joint, f'GetLocalRot{side}Attr')().Get()
    return np.array(Gf.Matrix4d(Gf.Rotation(Gf.Quatd(rotation)), Gf.Vec3d(position))).T


def validate(stage, root, urdf_file):
    robot = ET.parse(urdf_file).getroot()
    report = []
    for i in range(1, 9):
        name = f'joint{i}'
        definition = robot.find(f"joint[@name='{name}']")
        kind = UsdPhysics.RevoluteJoint if i <= 6 else UsdPhysics.PrismaticJoint
        matches = [p for p in stage.Traverse() if p.GetName() == name and p.IsA(kind)]
        if definition is None or len(matches) != 1:
            raise ValueError(f'{name}: missing or ambiguous ROS/USD joint')
        joint = kind(matches[0])
        for side, tag in ((0, 'parent'), (1, 'child')):
            targets = getattr(joint, f'GetBody{side}Rel')().GetTargets()
            if len(targets) != 1 or targets[0].name != definition.find(tag).get('link'):
                raise ValueError(f'{name}: ROS/USD {tag} differs; rebuild the robot assembly')
        frame0, frame1 = anchor(joint, 0), anchor(joint, 1)
        expected = urdf_origin(definition)
        actual = frame0 @ np.linalg.inv(frame1)
        axis = np.eye(3)[:, 'XYZ'.index(joint.GetAxisAttr().Get())]
        urdf_axis = np.fromstring(definition.find('axis').get('xyz'), sep=' ')
        if (not np.allclose(actual, expected, atol=2e-6, rtol=0) or
                not np.allclose(frame0[:3, :3] @ axis, expected[:3, :3] @ urdf_axis, atol=2e-6, rtol=0)):
            raise ValueError(f'{name}: ROS/USD joint geometry differs; rebuild the robot assembly')
        report.append({'name': name, 'origin_error_m': float(np.linalg.norm(actual[:3, 3]-expected[:3, 3]))})
    return {'urdf_file': str(urdf_file), 'modified_stage': False, 'joints': report}
