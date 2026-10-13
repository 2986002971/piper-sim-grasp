#!/usr/bin/env python3
"""Build two auditable, articulated appliance baselines; estimates are not calibration."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import importlib.util

import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from scipy.spatial import ConvexHull
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf

_helper_path = Path(__file__).resolve().parents[1] / 'objects/build_physics.py'
_helper_spec = importlib.util.spec_from_file_location('object_physics_helpers', _helper_path)
_helpers = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helpers)
principal_inertia = _helpers.principal_inertia
surface_mass_properties = _helpers.surface_mass_properties
triangle_shell_colliders = _helpers.triangle_shell_colliders
usd_mesh = _helpers.usd_mesh
apply_physx = _helpers.apply_physx

STEEL = 7900.0
PLASTIC = 1040.0
GLASS = 2500.0
REFERENCES = {
    'dishwasher': 'https://media3.bosch-home.com/Documents/specsheet/en-GB/SMS88UW06G.pdf',
    'washer': 'https://media3.bosch-home.com/Documents/specsheet/en-GB/WGG245S1GB.pdf',
    'steel_density': 'https://otke-cdn.outokumpu.com/-/media/files/products/core/outokumpu-core-range-datasheet.pdf',
    'drive_units': 'https://docs.isaacsim.omniverse.nvidia.com/latest/robot_setup_tutorials/tutorial_rig_legged_robot.html',
    'abs_density': 'https://eshop.ineos-styrolution.com/Product/Terluran_Terluran-GP-35_SKU300600120829.html',
    'pc_density': 'https://www.sabic.com/en/Images/Polymers-All-Products_tcm1010-42650.pdf',
    'glass_density': 'https://www.agc-yourglass.com/sites/default/files/brochures/original/Sandblasted_InfoSheet_EN_1019.PDF',
}


def box(size, center):
    result = trimesh.creation.box(extents=size)
    result.apply_translation(center)
    return result


def aggregate_properties(components):
    """Combine independently weighted solid or exact triangle-surface components."""
    entries = []
    for mesh, mass, method in components:
        if method == 'surface':
            center, inertia = surface_mass_properties(mesh, mass)
        else:
            if not mesh.is_volume:
                raise ValueError('Solid mass model requires positive watertight geometry')
            center = mesh.center_mass
            inertia = mesh.moment_inertia * mass / mesh.volume
        entries.append((mass, center, inertia))
    mass = sum(row[0] for row in entries)
    center = sum(m * c for m, c, _ in entries) / mass
    inertia = np.zeros((3, 3))
    for m, c, tensor in entries:
        offset = c - center
        inertia += tensor + m * (np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset))
    return mass, center, inertia


def mass_api(prim, components):
    mass, center, tensor = aggregate_properties(components)
    values, quaternion = principal_inertia(tensor)
    api = UsdPhysics.MassAPI.Apply(prim)
    api.CreateDensityAttr(0)  # explicit masses replace upstream automatic density estimates
    api.CreateMassAttr(mass)
    api.CreateCenterOfMassAttr(Gf.Vec3f(*center))
    api.CreateDiagonalInertiaAttr(Gf.Vec3f(*values))
    api.CreatePrincipalAxesAttr(Gf.Quatf(float(quaternion[0]), Gf.Vec3f(*quaternion[1:])))
    apply_physx(prim, 'PhysxRigidBodyAPI', {
        'physxRigidBody:solverPositionIterationCount': ('Int', 32),
        'physxRigidBody:solverVelocityIterationCount': ('Int', 4),
        'physxRigidBody:maxDepenetrationVelocity': ('Float', 1.0),
        'physxRigidBody:linearDamping': ('Float', 0),
        'physxRigidBody:angularDamping': ('Float', 0)})
    return {'mass_kg': mass, 'center_of_mass_body_m': center.tolist(),
            'inertia_body_kg_m2': tensor.tolist(), 'principal_inertia_kg_m2': values.tolist(),
            'principal_axes_wxyz': quaternion.tolist(),
            'components': [{'mass_kg': m, 'distribution': method, 'volume_m3': mesh.volume,
                            'surface_area_m2': mesh.area} for mesh, m, method in components]}


def configure_stage(stage, root):
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, 'Z')
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    stage.SetDefaultPrim(stage.GetPrimAtPath(root))
    stage.SetTimeCodesPerSecond(240)
    stage.SetStartTimeCode(0); stage.SetEndTimeCode(7200)
    material = UsdShade.Material.Define(stage, root + '/Contact')
    api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    api.CreateStaticFrictionAttr(0.35)
    api.CreateDynamicFrictionAttr(0.25)
    api.CreateRestitutionAttr(0.02)
    apply_physx(material.GetPrim(), 'PhysxMaterialAPI', {
        'physxMaterial:frictionCombineMode': ('Token', 'average'),
        'physxMaterial:restitutionCombineMode': ('Token', 'min')})
    return material


def collider(stage, body, mesh, index, material):
    path = str(body.GetPath()) + '/Collision/P' + str(index)
    result = usd_mesh(stage, path, mesh)
    result.CreateVisibilityAttr('invisible')
    result.CreatePurposeAttr('guide')
    UsdPhysics.CollisionAPI.Apply(result.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(result.GetPrim()).CreateApproximationAttr('convexHull')
    UsdShade.MaterialBindingAPI.Apply(result.GetPrim()).Bind(material, materialPurpose='physics')
    apply_physx(result.GetPrim(), 'PhysxCollisionAPI', {
        'physxCollision:contactOffset': ('Float', 0.001),
        'physxCollision:restOffset': ('Float', 0.0)})


def drive(joint, angular, stiffness, damping, effort, role):
    """Inputs in SI radians; USD angular gains require pi/180 conversion."""
    api = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), 'angular' if angular else 'linear')
    api.CreateTypeAttr('force')
    scale = math.pi / 180 if angular else 1
    api.CreateStiffnessAttr(stiffness * scale)
    api.CreateDampingAttr(damping * scale)
    api.CreateMaxForceAttr(effort)
    api.CreateTargetPositionAttr(0)
    api.CreateTargetVelocityAttr(0)
    return {'role': role, 'stiffness_si': stiffness, 'damping_si': damping,
            'usd_stiffness': stiffness * scale, 'usd_damping': damping * scale,
            'max_effort_Nm_or_N': effort, 'gain_status': 'calculated_engineering_estimate_not_measured'}


def usd_geometry(prim, body, cache):
    a = UsdGeom.Mesh(prim)
    indices = np.array(a.GetFaceVertexIndicesAttr().Get())
    faces, offset = [], 0
    for count in a.GetFaceVertexCountsAttr().Get():
        f = indices[offset:offset + count]
        faces.extend([[f[0], f[i], f[i + 1]] for i in range(1, count - 1)])
        offset += count
    transform = cache.GetLocalToWorldTransform(prim) * cache.GetLocalToWorldTransform(body).GetInverse()
    points = np.array([transform.Transform(Gf.Vec3d(p)) for p in a.GetPointsAttr().Get()])
    mesh = trimesh.Trimesh(points, faces, process=True)
    mesh.fix_normals(multibody=True)
    return mesh


def limited_hull(mesh, tolerance=0.0005):
    """Adaptive convex support sampling, with a recorded halfspace-gap bound."""
    vertices = np.asarray(mesh.convex_hull.vertices)
    if len(vertices) <= 128:
        return mesh.convex_hull, 0.0
    directions = np.array([[x, y, z] for x in [-1, 0, 1] for y in [-1, 0, 1]
                            for z in [-1, 0, 1] if x or y or z])
    selected = set(np.argmax(vertices @ directions.T, axis=0).tolist())
    while True:
        indices = sorted(selected)
        hull = ConvexHull(vertices[indices])
        gaps = vertices @ hull.equations[:, :3].T + hull.equations[:, 3]
        row, col = np.unravel_index(np.argmax(gaps), gaps.shape)
        gap = max(0.0, float(gaps[row, col]))
        if gap <= tolerance or len(selected) == 128:
            if gap > tolerance:
                raise ValueError('Convex simplification exceeds its 0.5 mm halfspace-gap bound')
            result = trimesh.Trimesh(vertices[indices], hull.simplices, process=False)
            result.fix_normals()
            return result, gap
        selected.add(int(row))


def dishwasher(data_root, output):
    source = data_root / 'sources/moonlake_dishwasher/moonlake_dishwasher.usdz'
    stage = Usd.Stage.CreateNew(str(output / 'dishwasher.usda'))
    root = UsdGeom.Xform.Define(stage, '/Dishwasher').GetPrim()
    root.GetReferences().AddReference(os.path.relpath(source, output))
    material = configure_stage(stage, '/Dishwasher')
    articulation = stage.GetPrimAtPath('/Dishwasher/dishwasher/base_frame_001')
    apply_physx(articulation, 'PhysxArticulationAPI', {
        'physxArticulation:enabledSelfCollisions': ('Bool', True),
        'physxArticulation:solverPositionIterationCount': ('Int', 32),
        'physxArticulation:solverVelocityIterationCount': ('Int', 4)})
    cache = UsdGeom.XformCache()
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    meshes = {}
    for body in bodies:
        meshes[body.GetName()] = [(p, usd_geometry(p, body, cache)) for p in Usd.PrimRange(body)
                                  if p.IsA(UsdGeom.Mesh)]
    specs, moving_mass = {}, 0
    for body in bodies:
        name = body.GetName()
        if name == 'base_frame_002':
            continue
        components = []
        for prim, mesh in meshes[name]:
            if name == 'door_pivot_001' and prim.GetName() == 'door_001':
                world = mesh.copy()
                world.apply_transform(np.array(cache.GetLocalToWorldTransform(body)).T)
                width, _, height = world.extents
                # Two 0.6 mm steel skins, 1.5 mm polymer liner, 0.5 kg hinge/insulation.
                mass = width * height * (2 * 0.0006 * STEEL + 0.0015 * PLASTIC) + 0.5
                components.append((mesh, mass, 'surface'))
            else:
                density = STEEL if 'rack' in name else PLASTIC
                components.append((mesh, mesh.volume * density, 'solid'))
        specs[name] = mass_api(body, components)
        specs[name]['mass_basis'] = ('thin-sheet bill of materials plus panel/dispenser polymer volume'
                                    if name == 'door_pivot_001' else 'watertight mesh volume times steel/polymer density')
        moving_mass += specs[name]['mass_kg']
    base = next(b for b in bodies if b.GetName() == 'base_frame_002')
    remaining = 55.7 - moving_mass
    # Machinery is assigned to a lower compartment; remaining frame mass uses the source shell.
    cabinet = next(m for p, m in meshes[base.GetName()] if p.GetName() == 'cabinet_001')
    specs[base.GetName()] = mass_api(base, [(cabinet, remaining - 8, 'surface'),
                                         (box([0.4, 0.4, 0.18], [0, 0.02, 0.13]), 8, 'solid')])
    specs[base.GetName()]['mass_basis'] = '55.7 kg reference total minus moving parts; 8 kg lower machinery allocation assumed'
    collider_count = 0
    for body in bodies:
        parts = []
        max_gap = 0
        for prim, mesh in meshes[body.GetName()]:
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
            for component in mesh.split(only_watertight=False):
                hull = component.convex_hull
                # Bent rack wires must retain their U-shaped clearance.
                if component.is_volume and hull.volume <= component.volume * 1.05:
                    proxy, gap = limited_hull(component)
                    parts.append(proxy)
                    max_gap = max(max_gap, gap)
                else:
                    parts.extend(triangle_shell_colliders(component, 0.0005))
        for i, part in enumerate(parts):
            if not part.is_volume:
                # Trimesh can discard tiny hull triangles in thin curved patches.
                # Reconstruct their full convex topology without geometric merging.
                vertices = np.asarray(part.vertices)
                hull = ConvexHull(vertices)
                part = trimesh.Trimesh(vertices, hull.simplices, process=False)
                part.fix_normals()
            if len(part.vertices) > 128 or not part.is_volume:
                raise ValueError(f'Invalid appliance convex proxy: {body.GetName()} part {i}, '
                                 f'vertices={len(part.vertices)}, volume={part.volume}, '
                                 f'watertight={part.is_watertight}, winding={part.is_winding_consistent}')
            collider(stage, body, part, i, material)
        specs[body.GetName()]['convex_collider_count'] = len(parts)
        specs[body.GetName()]['hull_simplification_max_plane_gap_m'] = max_gap
        collider_count += len(parts)
    joints = []
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        name = prim.GetName()
        item = {'name': name, 'path': str(prim.GetPath()), 'type': prim.GetTypeName()}
        if prim.IsA(UsdPhysics.RevoluteJoint):
            body_name = stage.GetPrimAtPath(joint.GetBody1Rel().GetTargets()[0]).GetName()
            spec = specs[body_name]
            center = np.array(spec['center_of_mass_body_m'])
            tensor = np.array(spec['inertia_body_kg_m2'])
            # Source revolute axis is local Z. Include the parallel-axis term.
            inertia_hinge = tensor[2, 2] + spec['mass_kg'] * np.dot(center[:2], center[:2])
            gravity_scale = spec['mass_kg'] * 9.81 * np.linalg.norm(center[:2])
            stiffness = gravity_scale * 1.05 if name == 'door_pivot' else 0
            damping = 2 * 0.35 * math.sqrt(max(stiffness, gravity_scale) * inertia_hinge)
            item.update(inertia_hinge_kg_m2=float(inertia_hinge), gravity_torque_scale_Nm=float(gravity_scale))
            item['drive'] = drive(joint, True, stiffness, damping, max(2 * gravity_scale, 0.1),
                                  'approximate passive spring counterbalance' if stiffness else 'passive viscous hinge')
            if stiffness:
                body = stage.GetPrimAtPath(joint.GetBody1Rel().GetTargets()[0])
                world_transform = cache.GetLocalToWorldTransform(body)
                local_axis = Gf.Rotation(Gf.Quatd(joint.GetLocalRot1Attr().Get())).TransformDir(Gf.Vec3d(0, 0, 1))
                world_axis = np.array(world_transform.TransformDir(local_axis))
                radial = np.array(world_transform.TransformDir(Gf.Vec3d(*center)))
                torque_closed = float(np.dot(world_axis, np.cross(radial, [0, 0, -spec['mass_kg'] * 9.81])))
                preload_angle = -torque_closed / stiffness
                UsdPhysics.DriveAPI(joint.GetPrim(), 'angular').CreateTargetPositionAttr(math.degrees(preload_angle))
                item['drive'].update(preload_torque_closed_Nm=torque_closed,
                                     target_position_native_deg_or_m=math.degrees(preload_angle))
        elif prim.IsA(UsdPhysics.PrismaticJoint):
            body_name = stage.GetPrimAtPath(joint.GetBody1Rel().GetTargets()[0]).GetName()
            mass = specs[body_name]['mass_kg']
            spring = 200 if name.startswith('button') else 0
            damper = 2 * 0.7 * math.sqrt(mass * spring) if spring else mass * 2
            item.update(effective_mass_kg=mass, drive=drive(joint, False, spring, damper,
                        2 if spring else 20, 'button return spring estimate' if spring else 'passive rail viscous damping'))
        joints.append(item)
    stage.GetRootLayer().Save()
    return {'id': 'dishwasher', 'usd': 'dishwasher.usda', 'mass_kg': 55.7, 'bodies': specs,
            'joints': joints, 'convex_collider_count': collider_count,
            'door_limit_deg': [0, 90], 'rack_limit_m': [0, 0.35],
            'geometry_origin': 'MoonlakeAI pinned USDZ; visual materials preserved; collision proxies replaced',
            'spring_model_limit': 'linear conservative closing spring approximates nonlinear counterbalance; no latch model',
            'license': 'Apache-2.0 (upstream repository)', 'reference_product_is_exact_match': False}


def ring_segments(outer, inner, depth, center, count=64):
    """Closed convex annular wedges, axis Y, preserving the central opening."""
    parts = []
    center = np.array(center)
    for i in range(count):
        angles = np.array([i, i + 1]) * 2 * math.pi / count
        points = [[r * math.cos(t), y, r * math.sin(t)]
                  for y in [-depth / 2, depth / 2] for r in [inner, outer] for t in angles]
        part = trimesh.convex.convex_hull(np.array(points) + center)
        parts.append(part)
    return parts


def cylinder(radius, depth, center):
    result = trimesh.creation.cylinder(radius, depth, sections=60)
    result.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    result.apply_translation(center)
    return result


def rectangular_aperture(width, bottom, top, radius, depth, center):
    """Convex wedges tile a rectangular panel around a circular opening."""
    half = width / 2
    angles = list(np.arange(64) * 2 * math.pi / 64)
    angles += [math.atan2(z, x) % (2 * math.pi) for x in [-half, half] for z in [bottom, top]]
    angles = sorted(set(angles))
    parts = []
    for i, t in enumerate(angles):
        pair = [t, angles[(i + 1) % len(angles)]]
        points = []
        for a in pair:
            c, s = math.cos(a), math.sin(a)
            outer = min(half / max(abs(c), 1e-15),
                        (top if s >= 0 else -bottom) / max(abs(s), 1e-15))
            for r in [radius, outer]:
                for y in [-depth / 2, depth / 2]:
                    points.append([r * c, y, r * s])
        parts.append(trimesh.convex.convex_hull(np.array(points) + center))
    return parts


def add_body(stage, name, position, components, parts, material, color):
    body = UsdGeom.Xform.Define(stage, '/Washer/' + name).GetPrim()
    UsdGeom.Xformable(body).AddTranslateOp().Set(Gf.Vec3d(*position))
    UsdPhysics.RigidBodyAPI.Apply(body)
    spec = mass_api(body, components)
    visual = trimesh.util.concatenate([c[0] for c in components])
    geom = usd_mesh(stage, str(body.GetPath()) + '/Visual', visual)
    geom.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    for i, part in enumerate(parts):
        collider(stage, body, part, i, material)
    spec['convex_collider_count'] = len(parts)
    return body, spec


def washer(output):
    stage = Usd.Stage.CreateNew(str(output / 'washer.usda'))
    root = UsdGeom.Xform.Define(stage, '/Washer').GetPrim()
    material = configure_stage(stage, '/Washer')
    UsdPhysics.ArticulationRootAPI.Apply(root)
    apply_physx(root, 'PhysxArticulationAPI', {'physxArticulation:enabledSelfCollisions': ('Bool', True),
                'physxArticulation:solverPositionIterationCount': ('Int', 32),
                'physxArticulation:solverVelocityIterationCount': ('Int', 4)})
    width, height, depth = 0.597, 0.845, 0.588
    opening, door_outer, drum_radius = 0.165, 0.235, 0.25
    drum_length = 0.070 / (math.pi * drum_radius ** 2)  # 70 L manufacturer reference
    drum_center = [0, -depth / 2 + 0.035 + drum_length / 2, 0.42]
    drum_parts = ring_segments(drum_radius + 0.0007, drum_radius, drum_length, drum_center)
    drum_parts.append(cylinder(drum_radius, 0.0007,
                              [0, drum_center[1] + drum_length / 2, drum_center[2]]))
    drum_mesh = trimesh.util.concatenate(drum_parts)
    hub = cylinder(0.07, 0.025, [0, drum_center[1] + drum_length / 2 + 0.0125, 0.42])
    drum_components = [(drum_mesh, drum_mesh.volume * STEEL, 'solid'), (hub, 1.5, 'solid')]
    drum_mass = aggregate_properties(drum_components)[0]
    hinge = np.array([-door_outer, -depth / 2 - 0.021, 0.42])
    door_center = np.array([0, -depth / 2 - 0.027, 0.42]) - hinge
    door_parts = ring_segments(door_outer, opening, 0.032, door_center)
    door_ring = trimesh.util.concatenate(door_parts)
    glass = cylinder(opening - 0.001, 0.004, door_center + [0, -0.018, 0])
    handle = box([0.023, 0.025, 0.09], door_center + [0.213, -0.031, 0])
    door_parts.extend([glass, handle])
    # Polymer ring has ribs/cavities: 35% of its solid envelope. Glass is a flat disk proxy.
    door_components = [(door_ring, door_ring.volume * 1200 * 0.35, 'solid'),
                       (glass, glass.volume * GLASS, 'solid'), (handle, 0.25, 'solid')]
    door_mass = aggregate_properties(door_components)[0]
    drawer_mesh = box([0.18, 0.19, 0.08], [-0.17, -depth / 2 + 0.095, 0.735])
    drawer_mass = drawer_mesh.area * 0.0015 * PLASTIC  # hollow 1.5 mm polymer drawer
    # Keep a genuine front aperture rather than the sealed-box visual candidates.
    front = rectangular_aperture(width - 0.016, -0.38, 0.315, opening + 0.002,
                                 0.008, np.array([0, -depth / 2, 0.42]))
    panels = [box([0.008, depth, height], [sign * (width / 2 - 0.004), 0, height / 2]) for sign in [-1, 1]]
    panels += [box([width - 0.016, depth, 0.012], [0, 0, height - 0.006]),
               box([width - 0.016, depth, 0.04], [0, 0, 0.02]),
               box([width - 0.016, 0.008, height - 0.052], [0, depth / 2 - 0.004, height / 2]),
               box([width - 0.016, 0.008, 0.17], [0, -depth / 2, 0.13]),
               box([width - 0.016, 0.008, 0.11], [0, -depth / 2, 0.79])]
    base_mass = 73.9 - drum_mass - door_mass - drawer_mass
    shell = trimesh.util.concatenate(panels + front)
    base_components = [(shell, base_mass - 22 - 8, 'surface'),
                       (box([0.45, 0.12, 0.12], [0, 0.07, 0.20]), 22, 'solid'),
                       (box([0.16, 0.2, 0.12], [0, 0.1, 0.08]), 8, 'solid')]
    # Hidden ballast/motor geometries supply inertia only, not visual or collision blocks.
    base, base_spec = add_body(stage, 'Base', [0, 0, 0], base_components, panels + front, material, [0.82, 0.84, 0.86])
    stage.RemovePrim('/Washer/Base/Visual')
    usd_mesh(stage, '/Washer/Base/Visual', shell).CreateDisplayColorAttr([Gf.Vec3f(0.82, 0.84, 0.86)])
    drum, drum_spec = add_body(stage, 'Drum', [0, 0, 0], drum_components, drum_parts, material, [0.4, 0.43, 0.46])
    door, door_spec = add_body(stage, 'Door', hinge, door_components, door_parts, material, [0.14, 0.16, 0.18])
    drawer, drawer_spec = add_body(stage, 'Drawer', [0, 0, 0], [(drawer_mesh, drawer_mass, 'surface')],
                                   [drawer_mesh], material, [0.72, 0.74, 0.76])
    fixed = UsdPhysics.FixedJoint.Define(stage, '/Washer/Joints/WorldFixed')
    fixed.CreateBody1Rel().SetTargets([base.GetPath()])
    joints = []
    definitions = [('DoorHinge', door, 'revolute', 'Z', hinge, [0, 0, 0], Gf.Quatf(0, Gf.Vec3f(0, 1, 0)), 160),
                   ('DrumSpin', drum, 'revolute', 'Y', drum_center, drum_center, Gf.Quatf(1), 360),
                   ('DrawerSlide', drawer, 'prismatic', 'Y', [0, 0, 0], [0, 0, 0], Gf.Quatf(0, Gf.Vec3f(1, 0, 0)), 0.17)]
    for name, body, kind, axis, pos0, pos1, rotation, upper in definitions:
        angular = kind == 'revolute'
        cls = UsdPhysics.RevoluteJoint if angular else UsdPhysics.PrismaticJoint
        joint = cls.Define(stage, '/Washer/Joints/' + name)
        joint.CreateBody0Rel().SetTargets([base.GetPath()]); joint.CreateBody1Rel().SetTargets([body.GetPath()])
        joint.CreateAxisAttr(axis); joint.CreateLocalPos0Attr(Gf.Vec3f(*pos0)); joint.CreateLocalPos1Attr(Gf.Vec3f(*pos1))
        joint.CreateLocalRot0Attr(rotation); joint.CreateLocalRot1Attr(rotation)
        joint.CreateLowerLimitAttr(0); joint.CreateUpperLimitAttr(upper)
        joint.CreateCollisionEnabledAttr(False)
        spec = door_spec if name == 'DoorHinge' else drum_spec if name == 'DrumSpin' else drawer_spec
        if angular:
            a = np.array([0, 0, -1]) if axis == 'Z' else np.array([0, 1, 0])
            c = np.array(spec['center_of_mass_body_m']) - np.array(pos1)
            inertia = float(a @ np.array(spec['inertia_body_kg_m2']) @ a + spec['mass_kg'] * (np.dot(c, c) - np.dot(a, c) ** 2))
            damping = 2 * inertia / 0.5  # assumed viscous speed decay time constant 0.5 s
            item = {'inertia_hinge_kg_m2': inertia}
        else:
            damping = spec['mass_kg'] * 2
            item = {'effective_mass_kg': spec['mass_kg']}
        item.update(name=name, path=str(joint.GetPath()), type=joint.GetPrim().GetTypeName(),
                    drive=drive(joint, angular, 0, damping, 10 if angular else 20, 'passive viscous damping'))
        joints.append(item)
    stage.GetRootLayer().Save()
    return {'id': 'washer', 'usd': 'washer.usda', 'mass_kg': 73.9,
            'bodies': {'Base': base_spec, 'Drum': drum_spec, 'Door': door_spec, 'Drawer': drawer_spec},
            'joints': joints, 'door_limit_deg': [0, 160], 'drawer_limit_m': [0, 0.17],
            'drum_radius_m': drum_radius, 'drum_depth_m': drum_length, 'drum_reference_volume_m3': 0.070,
            'opening_radius_m': opening, 'dimensions_body_m': [width, depth, height],
            'convex_collider_count': sum(s['convex_collider_count'] for s in [base_spec, drum_spec, door_spec, drawer_spec]),
            'geometry_origin': 'new parametric generic geometry: reference dimensions and capacity; source visual candidates retained separately',
            'assumptions': ['polymer ring 1200 kg/m3 with 35% fill fraction', 'glass flat disk 2500 kg/m3',
                            '22 kg ballast and 8 kg motor lower-body allocation', '0.7 mm drum steel shell',
                            'opening diameter 330 mm and drawer stroke 170 mm estimated',
                            'dry empty loading configuration; no latch, water, deformables, suspension or spin-cycle dynamics'],
            'license': 'project MIT for authored geometry/code', 'reference_product_is_exact_match': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    output = root / 'physics-v1'
    output.mkdir(exist_ok=True)
    # Never publish a completeness marker before both assets finish successfully.
    (output / 'inventory.json').unlink(missing_ok=True)
    for name in ['dynamics_isaac.json', 'validation_offline.json', 'validation_isaac.usda']:
        (output / name).unlink(missing_ok=True)
    for name in ['dishwasher.usda', 'washer.usda']:
        (output / name).unlink(missing_ok=True)
    assets = [dishwasher(root, output), washer(output)]
    report = {'schema_version': 1, 'status': 'estimated_baseline_not_calibrated',
              'references': REFERENCES, 'runtime_validated': False, 'assets': assets,
              'contact': {'static_friction': 0.35, 'dynamic_friction': 0.25, 'restitution': 0.02,
                          'contact_offset_m': 0.001, 'rest_offset_m': 0,
                          'basis': 'engineering initial estimates; pair/material dependence requires calibration',
                          'sweep': {'static_friction': [0.2, 0.6], 'dynamic_friction': [0.15, 0.5],
                                    'restitution': [0, 0.1]}},
              'uncertainty': {'door_mass_multiplier': [0.7, 1.3], 'hinge_damping_multiplier': [0.5, 2],
                              'counterbalance_stiffness_multiplier': [0.7, 1.3],
                              'sweep_executed': False}}
    (output / 'inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'assets': len(assets), 'convex_parts': [a['convex_collider_count'] for a in assets]}), flush=True)


if __name__ == '__main__':
    main()
