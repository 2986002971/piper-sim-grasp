#!/usr/bin/env python3
"""Isaac Sim articulation/cavity checks with an explicitly separate test actuator."""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path

import numpy as np


def create_scene(directory):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf
    inventory = json.loads((directory / 'inventory.json').read_text())
    path = directory / 'validation_isaac.usda'
    path.unlink(missing_ok=True)
    stage = Usd.Stage.CreateNew(str(path))
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())
    stage.SetTimeCodesPerSecond(240); stage.SetStartTimeCode(0); stage.SetEndTimeCode(7200)
    UsdGeom.SetStageMetersPerUnit(stage, 1); UsdGeom.SetStageUpAxis(stage, 'Z')
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    physics = UsdPhysics.Scene.Define(stage, '/World/Physics')
    physics.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1)); physics.CreateGravityMagnitudeAttr(9.81)
    physics.GetPrim().AddAppliedSchema('PhysxSceneAPI')
    for name, kind, value in [('enableGPUDynamics', 'Bool', False), ('solverType', 'Token', 'TGS'),
                               ('broadphaseType', 'Token', 'MBP'), ('enableCCD', 'Bool', True)]:
        physics.GetPrim().CreateAttribute('physxScene:' + name, getattr(Sdf.ValueTypeNames, kind), custom=False).Set(value)
    floor = UsdGeom.Cube.Define(stage, '/World/Floor')
    floor.CreateSizeAttr(1); floor.AddTranslateOp().Set(Gf.Vec3d(2, 1, -0.05))
    floor.AddScaleOp().Set(Gf.Vec3d(10, 7, 0.1)); UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    cases = []
    factors = [('Low', 0.7, 0.5, 0.7, 0.2, 0.15, 0),
               ('Nominal', 1, 1, 1, 0.35, 0.25, 0.02),
               ('High', 1.3, 2, 1.3, 0.6, 0.5, 0.1)]
    for a_index, asset in enumerate(inventory['assets']):
        for index, (label, mass_factor, damp_factor, spring_factor, mu_s, mu_d, restitution) in enumerate(factors):
            name = asset['id'].title() + label
            root_path = '/World/' + name
            root = UsdGeom.Xform.Define(stage, root_path)
            root.GetPrim().GetReferences().AddReference(asset['usd'])
            offset = np.array([index * 2, a_index * 2, 0.])
            root.AddTranslateOp().Set(Gf.Vec3d(*offset))
            source_root = '/Dishwasher' if asset['id'] == 'dishwasher' else '/Washer'
            door_name = 'door_pivot_001' if asset['id'] == 'dishwasher' else 'Door'
            bodies = [p for p in Usd.PrimRange(root.GetPrim()) if p.HasAPI(UsdPhysics.RigidBodyAPI)]
            for body in bodies:
                if body.GetName() == door_name:
                    m = UsdPhysics.MassAPI(body)
                    m.CreateMassAttr(m.GetMassAttr().Get() * mass_factor)
                    m.CreateDiagonalInertiaAttr(m.GetDiagonalInertiaAttr().Get() * mass_factor)
            # World anchors have no actor frame: relocate them explicitly with the fixture.
            for p in Usd.PrimRange(root.GetPrim()):
                if p.IsA(UsdPhysics.FixedJoint) and not UsdPhysics.Joint(p).GetBody0Rel().GetTargets():
                    UsdPhysics.Joint(p).CreateLocalPos0Attr(Gf.Vec3f(*offset))
            material = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(root_path + '/Contact'))
            material.CreateStaticFrictionAttr(mu_s); material.CreateDynamicFrictionAttr(mu_d)
            material.CreateRestitutionAttr(restitution)
            root_art = next(p for p in Usd.PrimRange(root.GetPrim()) if p.HasAPI(UsdPhysics.ArticulationRootAPI))
            joint_specs = []
            for source in asset['joints']:
                if 'drive' not in source:
                    continue
                joint_path = root_path + source['path'][len(source_root):]
                p = stage.GetPrimAtPath(joint_path)
                angular = p.IsA(UsdPhysics.RevoluteJoint)
                api = UsdPhysics.DriveAPI(p, 'angular' if angular else 'linear')
                p.AddAppliedSchema('PhysxJointAPI')
                p.CreateAttribute('physxJoint:maxJointVelocity', Sdf.ValueTypeNames.Float, custom=False).Set(90 if angular else 0.25)
                active_mass_factor = mass_factor if source['name'] in ('door_pivot', 'DoorHinge') else 1
                equivalent = source.get('inertia_hinge_kg_m2', source.get('effective_mass_kg')) * active_mass_factor
                passive_k = source['drive']['stiffness_si'] * spring_factor
                passive_d = source['drive']['damping_si'] * damp_factor
                scale = math.pi / 180 if angular else 1
                api.CreateStiffnessAttr(passive_k * scale); api.CreateDampingAttr(passive_d * scale)
                joint_specs.append(dict(source, path=joint_path, angular=angular, equivalent=equivalent,
                                         passive_k=passive_k, passive_d=passive_d,
                                         passive_target=float(api.GetTargetPositionAttr().Get()),
                                         lower=float(p.GetAttribute('physics:lowerLimit').Get()),
                                         upper=float(p.GetAttribute('physics:upperLimit').Get())))
            probe_path = '/World/Probes/' + name
            if asset['id'] == 'washer':
                geom = UsdGeom.Sphere.Define(stage, probe_path)
                geom.CreateRadiusAttr(0.03); probe_position = offset + [0, -3, 2]
                probe_mass = 0.05; diagonal = 2 / 5 * probe_mass * 0.03 ** 2
            else:
                geom = UsdGeom.Cube.Define(stage, probe_path)
                geom.CreateSizeAttr(0.08); probe_position = offset + [0, -3, 2]
                probe_mass = 0.2; diagonal = probe_mass * 0.08 ** 2 / 6
            geom.AddTranslateOp().Set(Gf.Vec3d(*probe_position))
            UsdPhysics.CollisionAPI.Apply(geom.GetPrim()); UsdPhysics.RigidBodyAPI.Apply(geom.GetPrim())
            mass = UsdPhysics.MassAPI.Apply(geom.GetPrim())
            mass.CreateMassAttr(probe_mass); mass.CreateDiagonalInertiaAttr(Gf.Vec3f(diagonal))
            geom.GetPrim().AddAppliedSchema('PhysxRigidBodyAPI')
            geom.GetPrim().CreateAttribute('physxRigidBody:disableGravity', Sdf.ValueTypeNames.Bool, custom=False).Set(True)
            geom.GetPrim().CreateAttribute('physxRigidBody:enableCCD', Sdf.ValueTypeNames.Bool, custom=False).Set(True)
            cases.append({'name': name, 'id': asset['id'], 'root': root_path,
                          'articulation': str(root_art.GetPath()), 'offset': offset,
                          'source_root': source_root, 'probe': probe_path, 'joints': joint_specs,
                          'factors': {'door_mass': mass_factor, 'passive_damping': damp_factor,
                                      'passive_spring': spring_factor, 'mu_static': mu_s,
                                      'mu_dynamic': mu_d, 'restitution': restitution}})
    stage.GetRootLayer().Save()
    return path, cases


def run(directory):
    from isaacsim import SimulationApp
    app = SimulationApp({'headless': True, 'active_gpu': 0, 'physics_gpu': 0, 'multi_gpu': False,
                         'limit_cpu_threads': 16, 'renderer': 'RayTracedLighting'})
    import omni.usd
    import omni.physx
    import omni.timeline
    import omni.physics.tensors as tensors
    from pxr import UsdPhysics, UsdGeom, Gf, PhysicsSchemaTools
    from scipy.spatial.transform import Rotation
    from scipy.spatial import ConvexHull

    report = {'backend': 'isaac', 'runtime_version': importlib.metadata.version('isaacsim'),
              'all_passed': False, 'dt_s': 1 / 240,
              'scope': 'passive initial stability, test-actuated articulation, limits, cavity loading, finite state, combined parameter corner cases; no robot grasp or physical calibration',
              'test_actuator': '5 Hz critically damped PD added only in validation scene; source asset retains passive spring/damping',
              'results': []}
    timeline = omni.timeline.get_timeline_interface()
    try:
        path, cases = create_scene(directory)
        context = omni.usd.get_context(); context.open_stage(str(path)); app.update()
        stage = context.get_stage()
        simulation = omni.physx.get_physx_simulation_interface()
        timeline.play(); app.update()
        cooking = omni.physx.get_physx_cooking_interface()
        report['cooked_geometry'] = {}
        for case in cases:
            if not case['name'].endswith('Nominal'):
                continue
            count, max_gap, over_mm = 0, 0, 0
            for prim in stage.Traverse():
                path_string = str(prim.GetPath())
                if not path_string.startswith(case['root'] + '/') or not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                    continue
                if not UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
                    continue
                received, statuses = [], []
                def callback(result, meshes):
                    statuses.append(str(result))
                    received.extend(np.array(m.vertices, dtype=float) for m in meshes)
                cooking.request_convex_collision_representation(context.get_stage_id(),
                    PhysicsSchemaTools.sdfPathToInt(path_string), False, callback)
                if statuses != ['PhysxCollisionRepresentationResult.RESULT_VALID'] or len(received) != 1:
                    raise ValueError('Invalid runtime cooked convex: ' + path_string + ' ' + str(statuses))
                planes = ConvexHull(received[0]).equations
                authored = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get())
                gap = max(0., float((authored @ planes[:, :3].T + planes[:, 3]).max()))
                max_gap = max(max_gap, gap); over_mm += int(gap > 0.001); count += 1
            report['cooked_geometry'][case['id']] = {'verified_proxy_count': count,
                'max_authored_point_plane_gap_m': max_gap, 'proxy_count_gap_over_1mm': over_mm,
                'scope': 'all nominal convex proxies verified in actual CPU PhysX; halfspace diagnostic, not full surface distance'}
            print('COOKED', case['id'], count, max_gap, flush=True)
        tensor_sim = tensors.create_simulation_view('numpy', context.get_stage_id())
        for case in cases:
            view = tensor_sim.create_articulation_view(case['articulation'])
            case['view'] = view
            case['dof_names'] = list(view.shared_metatype.dof_names)
            case['link_names'] = list(view.shared_metatype.link_names)
            case['result'] = {'name': case['name'], 'asset': case['id'], 'factors': case['factors'],
                              'dof_names': case['dof_names'], 'phases': {}, 'max_hinge_anchor_error_m': 0}
            print('ARTICULATION', case['name'], case['dof_names'], flush=True)
        probe_view = tensor_sim.create_rigid_body_view('/World/Probes/*')
        probe_paths = list(probe_view.prim_paths)
        time = 0

        def capture(label):
            for case in cases:
                view = case['view']; q = np.array(view.get_dof_positions()[0]); v = np.array(view.get_dof_velocities()[0])
                links = np.array(view.get_link_transforms()[0])
                velocities = np.array(view.get_link_velocities()[0])
                if not np.isfinite(np.r_[q, v, links.ravel(), velocities.ravel()]).all():
                    raise ValueError('Nonfinite simulation state: ' + case['name'])
                positions = {n: float(value) for n, value in zip(case['dof_names'], q)}
                case['result']['phases'][label] = {'positions_rad_or_m': positions,
                                                    'max_speed_rad_s_or_m_s': float(np.abs(v).max())}
                max_error = 0
                for source in case['joints']:
                    p = stage.GetPrimAtPath(source['path']); world = []; axis0 = None
                    for index in [0, 1]:
                        body_path = p.GetRelationship('physics:body' + str(index)).GetTargets()[0]
                        name = stage.GetPrimAtPath(body_path).GetName()
                        pose = links[case['link_names'].index(name)]
                        rotation = Rotation.from_quat(pose[3:]).as_matrix()
                        local = np.array(p.GetAttribute('physics:localPos' + str(index)).Get())
                        world.append(pose[:3] + rotation @ local)
                        if index == 0:
                            joint_rotation = p.GetAttribute('physics:localRot0').Get()
                            xyz = joint_rotation.GetImaginary()
                            local_r = Rotation.from_quat([*xyz, joint_rotation.GetReal()]).as_matrix()
                            axis_name = p.GetAttribute('physics:axis').Get()
                            axis0 = rotation @ local_r @ np.eye(3)['XYZ'.index(axis_name)]
                    delta = world[1] - world[0]
                    if not source['angular']:
                        delta -= positions[source['name']] * axis0
                    max_error = max(max_error, float(np.linalg.norm(delta)))
                case['result']['max_hinge_anchor_error_m'] = max(case['result']['max_hinge_anchor_error_m'], max_error)
                if max_error > 0.002:
                    raise ValueError('Runtime hinge-frame separation: ' + case['name'] + ' ' + str(max_error))

        def step(seconds, label):
            nonlocal time
            for index in range(round(seconds * 240)):
                simulation.simulate(1 / 240, time); simulation.fetch_results(); time += 1 / 240
                if index % 240 == 239:
                    capture(label + '_progress')
            capture(label)
            print('PHASE', label, time, flush=True)

        def command(phase):
            for case in cases:
                for source in case['joints']:
                    p = stage.GetPrimAtPath(source['path'])
                    api = UsdPhysics.DriveAPI(p, 'angular' if source['angular'] else 'linear')
                    if phase == 'close':
                        target = 0
                    elif source['name'] in ('door_pivot', 'DoorHinge'):
                        target = 85 if source['name'] == 'door_pivot' else 150
                    elif source['name'] == 'lower_rack_pivot':
                        target = 0 if phase in ('open_doors', 'close_racks', 'door_limits') else 0.32
                    elif source['name'] == 'upper_rack_pivot':
                        target = 0.28 if phase == 'upper_rack' else 0
                    elif source['name'] == 'DrawerSlide':
                        target = 0.12
                    elif source['name'] == 'DrumSpin':
                        target = 30
                    else:
                        target = 0
                    if phase in ('door_limits', 'limits') and source['name'] in ('door_pivot', 'DoorHinge'):
                        target = source['upper'] + 30
                    if phase == 'limits' and source['name'] in ('lower_rack_pivot', 'upper_rack_pivot', 'DrawerSlide', 'DrumSpin'):
                        target = source['upper'] + (30 if source['angular'] else 0.08)
                    omega = 2 * math.pi * 5
                    active_k = source['equivalent'] * omega ** 2
                    active_d = 2 * 0.9 * source['equivalent'] * omega
                    total_k = active_k + source['passive_k']
                    scale = math.pi / 180 if source['angular'] else 1
                    api.CreateStiffnessAttr(total_k * scale)
                    api.CreateDampingAttr((active_d + source['passive_d']) * scale)
                    api.CreateTargetPositionAttr((target * active_k + source['passive_target'] * source['passive_k']) / total_k)
                    api.CreateMaxForceAttr(max(2, active_k * (math.pi if source['angular'] else 0.5)))
                    source['command'] = target
                    source['harness_gains_si'] = {'stiffness': active_k, 'damping': active_d}

        step(2, 'passive_initial')
        for case in cases:
            row = case['result']['phases']['passive_initial']
            if max(abs(v) for v in row['positions_rad_or_m'].values()) > 0.02 or row['max_speed_rad_s_or_m_s'] > 0.05:
                raise ValueError('Passive closed asset unstable: ' + case['name'])
        # A rack pushed against a partly closed door is a genuine collision jam.
        # Follow the physical appliance sequence rather than suppressing self-collision.
        command('open_doors'); step(6, 'open_doors')
        command('open'); step(4, 'open')
        for case in cases:
            values = case['result']['phases']['open']['positions_rad_or_m']
            for source in case['joints']:
                target = source['command'] * (math.pi / 180 if source['angular'] else 1)
                tolerance = math.radians(3) if source['angular'] else 0.01
                if abs(values[source['name']] - target) > tolerance:
                    raise ValueError('Joint failed opening target: ' + case['name'] + ' ' + source['name'] + ' ' + str(values[source['name']]))
        # Verify the cooked front aperture, then send a sphere through it from outside.
        query = omni.physx.get_physx_scene_query_interface()
        entry_poses = probe_view.get_transforms().copy()
        entry_poses[:, 3:] = [0, 0, 0, 1]
        entry_velocities = np.zeros((len(entry_poses), 6), dtype=np.float32)
        for case in cases:
            index = probe_paths.index(case['probe'])
            if case['id'] == 'washer':
                rays = []
                for x in [-0.06, 0, 0.06]:
                    for z in [-0.06, 0, 0.06]:
                        origin = case['offset'] + [x, -0.6, 0.42 + z]
                        hit = query.raycast_closest(origin.tolist(), [0, 1, 0], 0.9)
                        if not hit.get('hit') or not str(hit.get('rigidBody')).endswith('/Drum'):
                            raise ValueError('Cooked washer aperture ray blocked: ' + case['name'])
                        rays.append({'origin_m': origin.tolist(), 'distance_m': float(hit['distance'])})
                case['result']['aperture_rays'] = rays
                entry_poses[index, :3] = case['offset'] + [0, -0.35, 0.44]
                entry_velocities[index, 1] = 0.6
            else:
                entry_poses[index, :3] = case['offset'] + [0, -0.4, 0.60]
        indices = np.arange(len(entry_poses), dtype=np.uint32)
        probe_view.set_transforms(entry_poses, indices); probe_view.set_velocities(entry_velocities, indices)
        for case in cases:
            stage.GetPrimAtPath(case['probe']).GetAttribute('physxRigidBody:disableGravity').Set(False)
        step(4, 'loaded')
        poses = np.array(probe_view.get_transforms()); speeds = np.array(probe_view.get_velocities())
        query = omni.physx.get_physx_scene_query_interface()
        for case in cases:
            index = probe_paths.index(case['probe']); pose = poses[index]; relative = pose[:3] - case['offset']
            speed = float(np.linalg.norm(speeds[index, :3]))
            supports = []
            if case['id'] == 'washer':
                origin = pose[:3] - [0, 0, 0.0301]
                hit = query.raycast_closest(origin.tolist(), [0, 0, -1], 0.2)
                if hit.get('hit'): supports.append(str(hit.get('rigidBody')))
                expected = '/Drum'
                contained = abs(relative[0]) < 0.20 and -0.25 < relative[1] < 0.10 and 0.17 < relative[2] < 0.30
            else:
                rotation = Rotation.from_quat(pose[3:]).as_matrix()
                corners = np.array([[x, y, z] for x in [-0.04, 0.04] for y in [-0.04, 0.04] for z in [-0.04, 0.04]])
                low = (corners @ rotation.T + pose[:3])[:, 2].min()
                for x in [-0.025, 0, 0.025]:
                    for y in [-0.025, 0, 0.025]:
                        hit = query.raycast_closest([float(pose[0] + x), float(pose[1] + y), float(low - 0.001)], [0, 0, -1], 0.3)
                        if hit.get('hit'): supports.append(str(hit.get('rigidBody')))
                expected = '/lower_rack_pivot_001'
                contained = abs(relative[0]) < 0.25 and -0.60 < relative[1] < -0.1 and 0.24 < relative[2] < 0.55
            passed = bool(contained and speed < 0.03 and any(p.endswith(expected) for p in supports))
            case['result']['cavity_probe'] = {'final_relative_position_m': relative.tolist(), 'speed_m_s': speed,
                                             'supports': sorted(set(supports)), 'passed': passed}
            if not passed:
                raise ValueError('Cavity loading failed: ' + case['name'] + ' ' + str(case['result']['cavity_probe']))
        # Park probes so the closing check is not a test of trapping an object in the door.
        parked = probe_view.get_transforms().copy()
        parked[:, :3] = np.array([[-5, -5 - i * 0.3, 2] for i in range(len(parked))], dtype=np.float32)
        probe_view.set_transforms(parked, np.arange(len(parked), dtype=np.uint32))
        probe_view.set_velocities(np.zeros((len(parked), 6), dtype=np.float32), np.arange(len(parked), dtype=np.uint32))
        for case in cases:
            stage.GetPrimAtPath(case['probe']).GetAttribute('physxRigidBody:disableGravity').Set(True)
        command('upper_rack'); step(4, 'upper_rack')
        for case in cases:
            if case['id'] == 'dishwasher':
                value = case['result']['phases']['upper_rack']['positions_rad_or_m']['upper_rack_pivot']
                if abs(value - 0.28) > 0.01:
                    raise ValueError('Upper rack failed extension: ' + case['name'])
        command('close_racks'); step(4, 'close_racks')
        command('close'); step(6, 'close')
        for case in cases:
            values = case['result']['phases']['close']['positions_rad_or_m']
            if any(abs(v) > (math.radians(2) if source['angular'] else 0.005)
                   for source in case['joints'] for n, v in values.items() if n == source['name']):
                raise ValueError('Joint failed closing target: ' + case['name'])
        command('door_limits'); step(6, 'door_limits')
        command('limits'); step(6, 'limits')
        for case in cases:
            values = case['result']['phases']['limits']['positions_rad_or_m']
            for source in case['joints']:
                scale = math.pi / 180 if source['angular'] else 1
                value = values[source['name']]
                if value < source['lower'] * scale - 0.001 or value > source['upper'] * scale + 0.001:
                    raise ValueError('Joint limit violated: ' + case['name'] + ' ' + source['name'])
                if source['command'] > source['upper']:
                    tolerance = math.radians(1) if source['angular'] else 0.005
                    if abs(value - source['upper'] * scale) > tolerance:
                        raise ValueError('Joint failed to reach its upper limit: ' + case['name'] + ' ' + source['name'])
            case['result'].update(passed=True, test_actuator_gains_si={s['name']: s['harness_gains_si'] for s in case['joints']})
        report['results'] = [c['result'] for c in cases]
        report.update(all_passed=True, simulation_duration_s=time, combined_corner_case_count=len(cases),
                      sweep_design='joint parameter/contact bounds varied together; not exhaustive independent sensitivities')
    except Exception as error:
        import traceback
        traceback.print_exc()
        report['error'] = str(error)
        report['results'] = [c['result'] for c in locals().get('cases', []) if 'result' in c]
        raise
    finally:
        (directory / 'dynamics_isaac.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        timeline.stop(); app.close()
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    run(parser.parse_args().directory.resolve())
