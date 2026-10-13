#!/usr/bin/env python3
"""Exercise generated USD using CPU PhysX, or the installed Isaac Sim runtime."""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def make_scene(inventory, path):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, 'Z')
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    stage.SetTimeCodesPerSecond(240)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(7200)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, '/World').GetPrim())
    physics = UsdPhysics.Scene.Define(stage, '/World/Physics')
    physics.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    physics.CreateGravityMagnitudeAttr(9.81)
    prim = physics.GetPrim()
    prim.AddAppliedSchema('PhysxSceneAPI')
    for name, kind, value in [('enableCCD', 'Bool', True), ('enableGPUDynamics', 'Bool', False),
                               ('broadphaseType', 'Token', 'MBP'), ('solverType', 'Token', 'TGS')]:
        prim.CreateAttribute('physxScene:' + name, getattr(Sdf.ValueTypeNames, kind), custom=False).Set(value)
    floor = UsdGeom.Cube.Define(stage, '/World/Floor')
    floor.CreateSizeAttr(1)
    floor.AddTranslateOp().Set(Gf.Vec3d(8, 4, -.05))
    floor.AddScaleOp().Set(Gf.Vec3d(32, 20, .1))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    contact = UsdShade.Material.Define(stage, '/World/TableContact')
    contact_api = UsdPhysics.MaterialAPI.Apply(contact.GetPrim())
    contact_api.CreateStaticFrictionAttr(.6)
    contact_api.CreateDynamicFrictionAttr(.5)
    contact_api.CreateRestitutionAttr(0)
    contact.GetPrim().AddAppliedSchema('PhysxMaterialAPI')
    for name in ('frictionCombineMode', 'restitutionCombineMode'):
        contact.GetPrim().CreateAttribute('physxMaterial:' + name, Sdf.ValueTypeNames.Token, custom=False).Set('average')
    UsdShade.MaterialBindingAPI.Apply(floor.GetPrim()).Bind(contact, materialPurpose='physics')
    records = {}
    for i, item in enumerate(inventory['objects']):
        name = 'Item_' + item['id']
        root = UsdGeom.Xform.Define(stage, '/World/Objects/' + name)
        root.GetPrim().GetReferences().AddReference(os.path.relpath(item['usd'], path.parent))
        start = np.array([(i % 8) * .65, (i // 8) * .65, .15])
        root.AddTranslateOp().Set(Gf.Vec3d(*start))
        source_stage = Usd.Stage.Open(item['usd'])
        vertices = np.concatenate([np.array(UsdGeom.Mesh(p).GetPointsAttr().Get())
                                   for p in source_stage.Traverse() if p.HasAPI(UsdPhysics.CollisionAPI)])
        metadata = json.loads(Path(item['metadata']).read_text())
        records[str(root.GetPath())] = {'id': item['id'], 'role': 'drop', 'initial_position': start,
                                      'collider_vertices': vertices, 'metadata': metadata}
        if metadata['cavity_check']['required']:
            static = UsdGeom.Xform.Define(stage, '/World/CavityFixtures/' + name)
            static.GetPrim().GetReferences().AddReference(os.path.relpath(item['usd'], path.parent))
            position = np.array([10 + (i % 8) * .65, (i // 8) * .65, 0.])
            static.AddTranslateOp().Set(Gf.Vec3d(*position))
            UsdPhysics.RigidBodyAPI(static.GetPrim()).CreateKinematicEnabledAttr(True)
            static.GetPrim().CreateAttribute('physxRigidBody:enableCCD', Sdf.ValueTypeNames.Bool, custom=False).Set(False)
            point = np.array(metadata['cavity_check']['points_m'][-1])
            ball = UsdGeom.Sphere.Define(stage, '/World/CavityBalls/' + name)
            ball.CreateRadiusAttr(.003)
            ball_position = position + point
            ball_position[2] = metadata['extent_m'][2] + .04
            ball.AddTranslateOp().Set(Gf.Vec3d(*ball_position))
            UsdPhysics.CollisionAPI.Apply(ball.GetPrim())
            UsdPhysics.RigidBodyAPI.Apply(ball.GetPrim())
            UsdPhysics.MassAPI.Apply(ball.GetPrim()).CreateMassAttr(.001)
            ball.GetPrim().AddAppliedSchema('PhysxRigidBodyAPI')
            ball.GetPrim().CreateAttribute('physxRigidBody:enableCCD', Sdf.ValueTypeNames.Bool, custom=False).Set(True)
            records[str(ball.GetPath())] = {'id': item['id'], 'role': 'cavity_ball',
                                          'initial_position': ball_position, 'probe_z': point[2],
                                          'fixture_position': position,
                                          'fixture_path': str(static.GetPath()),
                                          'extent_xy': np.array(metadata['extent_m'][:2])}
    stage.GetRootLayer().Save()
    return records


class OvPhysXBackend:
    def __init__(self, path):
        import ovphysx
        import ovstage
        from ovphysx import PhysX
        ovstage.population.register_usd_schemas([str(ovphysx.codeless_schema_root())])
        PhysX.set_cpu_mode(True)
        self.physics = PhysX()
        self.stage = ovstage.Stage('robocup-object-validation')
        ovstage.population.open_usd(self.stage, str(path), ordinal=1, domains=ovstage.PopulationDomain.PHYSICS)
        self.stage.advance_write_floor(ordinal=1).wait()
        self.physics.attach_ovstage(self.stage, read_ordinal=1)
        self.paths = ovstage.PathDictionary(self.stage)

    def step(self, dt):
        self.physics.step_sync(dt)

    def read(self):
        from ovphysx.types import SimObjectType
        result = {}
        for name in ('position', 'orientation', 'linearVelocity', 'angularVelocity'):
            with self.physics.read(SimObjectType.RIGID_BODY, [name]) as read:
                for group in read.groups:
                    if group.is_delete or group.is_array or not group.tensors:
                        continue
                    paths = self.paths.get_path_strings(group.prim_list)
                    rows = group.tensors[0].numpy()
                    for path, row in zip(paths, rows):
                        result.setdefault(path, {})[name] = np.array(row, dtype=float)
        return result

    def close(self):
        self.paths.destroy()
        self.physics.detach_ovstage()
        self.stage.destroy()
        self.physics.destroy()

    def support(self, position):
        origin = (position - [0, 0, .0031]).tolist()
        hits = self.physics.raycast(origin, [0, 0, -1], .3)
        return str(hits[0]['rigid_body']) if hits else None


class IsaacBackend:
    def __init__(self, path, inventory):
        # Application must be started before importing Kit APIs.
        from isaacsim import SimulationApp
        self.app = SimulationApp({'headless': True, 'active_gpu': 0, 'physics_gpu': 0,
                                  'multi_gpu': False, 'limit_cpu_threads': 16,
                                  'renderer': 'RayTracedLighting'})
        self.records = make_scene(inventory, path)
        import omni.usd
        import omni.physx
        import omni.timeline
        self.context = omni.usd.get_context()
        self.context.open_stage(str(path))
        self.app.update()
        self.stage = self.context.get_stage()
        self.simulation = omni.physx.get_physx_simulation_interface()
        self.physx = omni.physx.get_physx_interface()
        self.timeline = omni.timeline.get_timeline_interface()
        self.timeline.play()
        self.app.update()
        self.load_cooked_colliders()
        self.time = 0.
        self.views = None

    def load_cooked_colliders(self):
        from pxr import PhysicsSchemaTools, UsdGeom, UsdPhysics
        from scipy.spatial import ConvexHull
        import omni.physx
        cooking = omni.physx.get_physx_cooking_interface()
        cooked = {}
        for prim in self.stage.Traverse():
            path = str(prim.GetPath())
            if not path.startswith('/World/Objects/') or not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                continue
            body_path = path.split('/Collisions/')[0]
            authored = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get())
            received = []

            def callback(result, meshes):
                if str(result) != 'PhysxCollisionRepresentationResult.RESULT_VALID':
                    raise ValueError('Invalid runtime collision cooking: ' + path + ': ' + str(result))
                received.extend(np.array(mesh.vertices, dtype=float) for mesh in meshes)

            cooking.request_convex_collision_representation(self.context.get_stage_id(),
                PhysicsSchemaTools.sdfPathToInt(path), False, callback)
            if len(received) != 1:
                raise ValueError('Expected one cooked convex mesh per authored part: ' + path)
            vertices = received[0]
            equations = ConvexHull(vertices).equations
            # Plane violation is a diagnostic lower bound on geometric distance,
            # not a Hausdorff distance or a calibrated accuracy estimate.
            gap = max(0., float((authored @ equations[:, :3].T + equations[:, 3]).max()))
            cooked.setdefault(body_path, []).append(vertices)
            record = self.records[body_path]
            record['cooking_max_plane_gap_m'] = max(record.get('cooking_max_plane_gap_m', 0.), gap)
        for path, record in self.records.items():
            if record['role'] == 'drop':
                if path not in cooked:
                    raise ValueError('No cooked colliders for rigid body: ' + path)
                record['authored_collider_vertices'] = record['collider_vertices']
                record['collider_vertices'] = np.concatenate(cooked[path])
                record['collider_geometry_source'] = 'isaac_physx_cooked'

    def step(self, dt):
        self.simulation.simulate(dt, self.time)
        self.simulation.fetch_results()
        self.time += dt

    def read(self):
        if self.views is None:
            import omni.physics.tensors as tensors
            self.tensor_simulation = tensors.create_simulation_view('numpy', self.context.get_stage_id())
            patterns = ['/World/Objects/*']
            if self.stage.GetPrimAtPath('/World/CavityBalls'):
                patterns.append('/World/CavityBalls/*')
            self.views = [self.tensor_simulation.create_rigid_body_view(pattern) for pattern in patterns]
        result = {}
        for view in self.views:
            transforms = view.get_transforms()
            velocities = view.get_velocities()
            for path, pose, velocity in zip(view.prim_paths, transforms, velocities):
                result[path] = {'position': np.array(pose[:3]), 'orientation': np.array(pose[3:]),
                               'linearVelocity': np.array(velocity[:3]),
                               'angularVelocity': np.array(velocity[3:])}
        return result

    def close(self):
        self.timeline.stop()
        self.app.close()

    def support(self, position):
        import omni.physx
        hit = omni.physx.get_physx_scene_query_interface().raycast_closest(
            (position - [0, 0, .0031]).tolist(), [0, 0, -1], .3)
        return str(hit.get('rigidBody')) if hit.get('hit') else None


def evaluate(records, samples):
    results = []
    for path, record in records.items():
        rows = [s.get(path) for s in samples]
        if any(row is None for row in rows):
            raise ValueError('Runtime did not expose rigid body: ' + path)
        if not all(np.isfinite(v).all() for row in rows for v in row.values()):
            raise ValueError('Nonfinite physics state: ' + path)
        final = rows[-1]
        position = final['position']
        moved_down = record['initial_position'][2] - min(r['position'][2] for r in rows)
        drift = np.linalg.norm(position - rows[-5]['position'])
        speed = np.linalg.norm(final['linearVelocity'])
        passed = moved_down > .02 and drift < .005 and speed < .05
        report = {'id': record['id'], 'role': record['role'], 'drop_distance_m': float(moved_down),
                  'final_position_m': position.tolist(), 'last_second_drift_m': float(drift),
                  'final_speed_m_s': float(speed)}
        if record['role'] == 'drop':
            rotation = Rotation.from_quat(final['orientation']).as_matrix()
            minimum = (record['collider_vertices'] @ rotation.T + position)[:, 2].min()
            passed &= -.003 < minimum < .015
            report['collider_minimum_z_m'] = float(minimum)
            report['collider_geometry_source'] = record.get('collider_geometry_source', 'authored_proxy')
            if 'authored_collider_vertices' in record:
                authored_minimum = (record['authored_collider_vertices'] @ rotation.T + position)[:, 2].min()
                report['authored_collider_minimum_z_m'] = float(authored_minimum)
                report['cooking_max_plane_gap_m'] = record['cooking_max_plane_gap_m']
        else:
            passed &= .002 <= position[2] < record['probe_z']
            # A ball can roll across a curved bowl floor; require it to remain inside
            # the container footprint and actually be supported by the fixture.
            relative = position[:2] - record['fixture_position'][:2]
            passed &= (np.abs(relative) < record['extent_xy'] / 2 - .003).all()
            passed &= record.get('support_body') == record['fixture_path']
            report['upper_cavity_probe_z_m'] = float(record['probe_z'])
            report['support_body'] = record.get('support_body')
        report['passed'] = bool(passed)
        results.append(report)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    parser.add_argument('--backend', choices=['ovphysx', 'isaac'], default='ovphysx')
    parser.add_argument('--seconds', type=float, default=8.)
    args = parser.parse_args()
    if args.seconds < 2 or args.seconds > 30:
        raise ValueError('Use 2–30 seconds so final stability can be checked')
    inventory = json.loads(args.inventory.read_text())
    directory = args.inventory.resolve().parent
    path = directory / ('validation_' + args.backend + '.usda')
    report_path = directory / ('dynamics_' + args.backend + '.json')
    report_path.unlink(missing_ok=True)
    if args.backend == 'isaac':
        backend = IsaacBackend(path, inventory)
        records = backend.records
    else:
        records = make_scene(inventory, path)
        backend = OvPhysXBackend(path)
    samples = []
    dt = 1 / 240
    try:
        for step in range(math.ceil(args.seconds / dt)):
            backend.step(dt)
            if step % 60 == 59:
                samples.append(backend.read())
        for body_path, record in records.items():
            if record['role'] == 'cavity_ball' and body_path in samples[-1]:
                record['support_body'] = backend.support(samples[-1][body_path]['position'])
        results = evaluate(records, samples)
        passed = all(row['passed'] for row in results)
        report = {'backend': args.backend, 'runtime_version': importlib.metadata.version(
                      'ovphysx' if args.backend == 'ovphysx' else 'isaacsim'),
                  'inventory_generation': inventory['generation'], 'dt_s': dt, 'duration_s': args.seconds,
                  'drop_object_count': len(inventory['objects']), 'cavity_ball_count': len(records) - len(inventory['objects']),
                  'all_passed': passed, 'results': results,
                  'scope': 'CPU upright drop, rest stability, central cavity ball entry; not robot grasp or calibrated contacts'}
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        inventory.setdefault('runtime_reports', {})[args.backend] = {
            'report': report_path.name, 'all_passed': passed, 'scope': report['scope'],
            'runtime_version': report['runtime_version']}
        inventory['runtime_validated'] = any(r['all_passed'] for r in inventory['runtime_reports'].values())
        inventory['isaac_validated'] = inventory['runtime_reports'].get('isaac', {}).get('all_passed', False)
        # Per-asset parameters and their hashes remain the creation-time record;
        # the aggregate inventory links the later runtime evidence.
        temporary = args.inventory.with_suffix('.partial')
        temporary.write_text(json.dumps(inventory, indent=2, allow_nan=False) + '\n')
        temporary.replace(args.inventory)
        print(json.dumps({k: v for k, v in report.items() if k not in ('results', 'scope')}), flush=True)
    except Exception as error:
        import traceback
        traceback.print_exc()
        report_path.write_text(json.dumps({'backend': args.backend, 'all_passed': False,
                                          'error': str(error)}, indent=2) + '\n')
        raise
    finally:
        backend.close()
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
