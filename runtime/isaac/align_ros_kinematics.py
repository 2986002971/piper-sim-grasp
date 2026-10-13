"""Align this run's six arm joint frames to the existing ROS URDF.

Only USD session transforms and joint anchors change. Existing body coordinates,
inertia, drive gains, effort limits and velocity limits are retained. Intermediate
body orientations keep their CAD basis; link6 uses the ROS wrist basis so its
existing camera mount and TCP definition have the same meaning in ROS and USD.
"""
import math
import xml.etree.ElementTree as ET

import numpy as np
from pxr import Gf, UsdGeom, UsdPhysics


def urdf_origin(joint):
    pose = np.eye(4)
    element = joint.find('origin')
    if element is None:
        return pose
    pose[:3, 3] = np.fromstring(element.get('xyz', '0 0 0'), sep=' ')
    r, p, y = np.fromstring(element.get('rpy', '0 0 0'), sep=' ')
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    pose[:3, :3] = [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                    [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                    [-sp, cp*sr, cp*cr]]
    return pose


def usd_matrix(pose):
    return Gf.Matrix4d(pose.T.tolist())


def set_anchor(joint, side, pose):
    position = Gf.Vec3f(*map(float, pose[:3, 3]))
    rotation = usd_matrix(pose).ExtractRotationQuat()
    quaternion = Gf.Quatf(float(rotation.GetReal()), Gf.Vec3f(rotation.GetImaginary()))
    getattr(joint, f'CreateLocalPos{side}Attr')().Set(position)
    getattr(joint, f'CreateLocalRot{side}Attr')().Set(quaternion)


def align(stage, root, urdf_file):
    if stage.GetEditTarget().GetLayer() != stage.GetSessionLayer():
        raise ValueError('Kinematic alignment requires a USD session edit target')
    robot = ET.parse(urdf_file).getroot()
    urdf = {j.get('name'): j for j in robot.findall('joint')}
    candidates = [p for p in stage.Traverse() if p.IsA(UsdPhysics.RevoluteJoint)]
    names = [f'joint{i}' for i in range(1, 7)]
    joints = {}
    for name in names:
        matches = [p for p in candidates if p.GetName() == name]
        if len(matches) != 1:
            raise ValueError(f'Expected one USD {name}')
        joint = UsdPhysics.RevoluteJoint(matches[0])
        definition = urdf[name]
        if definition.get('type') != 'revolute' or joint.GetAxisAttr().Get() != 'Z':
            raise ValueError(f'{name}: expected bounded Z revolute joint')
        np.testing.assert_allclose(np.fromstring(definition.find('axis').get('xyz'), sep=' '), [0., 0., 1.])
        joints[name] = joint
    cache = UsdGeom.XformCache()
    base_world = np.array(cache.GetLocalToWorldTransform(root)).T
    poses, gauges, bodies = {'base_link': np.eye(4)}, {'base_link': np.eye(4)}, {'base_link': root}
    report = []
    # Validate the complete chain and calculate all edits before authoring.
    for name in names:
        definition, joint = urdf[name], joints[name]
        parent, child = definition.find('parent').get('link'), definition.find('child').get('link')
        body0, body1 = joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()
        if parent not in poses or len(body0) != 1 or len(body1) != 1 or body0[0] != bodies[parent].GetPath():
            raise ValueError(f'{name}: ROS/USD body chain mismatch')
        bodies[child] = stage.GetPrimAtPath(body1[0])
        poses[child] = poses[parent] @ urdf_origin(definition)
        original = np.linalg.inv(base_world) @ np.array(cache.GetLocalToWorldTransform(bodies[child])).T
        gauge = np.eye(4)
        if child != 'link6':
            gauge[:3, :3] = original[:3, :3].T @ poses[child][:3, :3]
        gauges[child] = gauge
        frame0, frame1 = gauges[parent] @ urdf_origin(definition), gauge
        desired = base_world @ poses[child] @ np.linalg.inv(gauge)
        report.append({'name': name, 'body': str(body1[0]),
                       'rest_position_shift_m': (desired[:3, 3]-(base_world @ original)[:3, 3]).tolist(),
                       'local_frame0': frame0.tolist(), 'local_frame1': frame1.tolist()})
    for item in report:
        name = item['name']
        child = urdf[name].find('child').get('link')
        body = bodies[child]
        # Nested USD bodies need transforms relative to their immediate USD
        # parent, which may differ from the rigid parent in the joint relation.
        cache = UsdGeom.XformCache()
        parent_world = np.array(cache.GetLocalToWorldTransform(body.GetParent())).T
        desired = base_world @ poses[child] @ np.linalg.inv(gauges[child])
        transform = UsdGeom.Xformable(body)
        if transform.GetResetXformStack():
            raise ValueError('Unexpected reset transform stack in Piper body')
        transform.ClearXformOpOrder()
        transform.AddTransformOp(opSuffix='rosKinematics').Set(usd_matrix(np.linalg.inv(parent_world) @ desired))
        set_anchor(joints[name], 0, np.array(item['local_frame0']))
        set_anchor(joints[name], 1, np.array(item['local_frame1']))
    return {'urdf_file': str(urdf_file), 'edit_layer': 'session', 'joints': report}
