#!/usr/bin/env python3
"""Build portable textured USD rigid objects with explicit, auditable physics estimates."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
import trimesh
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def validate_profile(p, materials):
    if not np.isfinite(p['mass_kg']) or p['mass_kg'] <= 0:
        raise ValueError('Mass must be positive and finite')
    if p['source_up_axis'] not in ('Y', 'Z'):
        raise ValueError('Unsupported up axis')
    if p['material'] not in materials:
        raise ValueError('Unknown contact material')
    if p['nominal_size'] and (p['nominal_size']['value_m'] <= 0 or
                             not np.isfinite(p['nominal_size']['value_m'])):
        raise ValueError('Invalid nominal size')
    for material in materials.values():
        ms, md, r = (material[k] for k in ('static_friction', 'dynamic_friction', 'restitution'))
        if not all(np.isfinite([ms, md, r])) or not 0 <= md <= ms or not 0 <= r <= 1:
            raise ValueError('Invalid material coefficients')


def normalize_mesh(mesh, profile):
    rotation = np.eye(3) if profile['source_up_axis'] == 'Z' else np.array(
        [[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
    vertices = mesh.vertices @ rotation.T
    scale = 1.0
    if profile['nominal_size']:
        size = profile['nominal_size']
        dimension = np.ptp(vertices, axis=0)
        extent = dimension[2] if size['dimension'] == 'height' else dimension.max()
        scale = size['value_m'] / extent
    vertices *= scale
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    translation = -np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    transform = np.eye(4)
    transform[:3, :3] = scale * rotation
    transform[:3, 3] = translation
    result = mesh.copy()
    result.apply_transform(transform)
    return result, transform


def geometry_mesh(visual):
    result = trimesh.Trimesh(vertices=visual.vertices.copy(), faces=visual.faces.copy(), process=False)
    result.merge_vertices(merge_tex=True, merge_norm=True)
    result.update_faces(result.nondegenerate_faces())
    result.remove_unreferenced_vertices()
    result.fix_normals(multibody=True)
    if not len(result.faces) or not np.isfinite(result.vertices).all():
        raise ValueError('Invalid geometry')
    return result


def surface_mass_properties(mesh, mass):
    """Exact second moments of a uniform triangular surface lamina, about its COM."""
    triangles = mesh.triangles
    weights = mesh.area_faces / mesh.area
    sums = triangles.sum(axis=1)
    center = (weights[:, None] * sums / 3).sum(axis=0)
    second = (np.einsum('ni,nj->nij', sums, sums) +
              np.einsum('nki,nkj->nij', triangles, triangles)) / 12
    covariance = np.einsum('n,nij->ij', weights, second) - np.outer(center, center)
    inertia = mass * (np.trace(covariance) * np.eye(3) - covariance)
    return center, inertia


def compound_mass_properties(parts, mass):
    """Uniform density in summed convex volumes; overlaps count twice (recorded approximation)."""
    volume = sum(p.volume for p in parts)
    if volume <= 0:
        raise ValueError('No positive collision volume')
    center = sum(p.volume * p.center_mass for p in parts) / volume
    inertia = np.zeros((3, 3))
    for part in parts:
        offset = part.center_mass - center
        inertia += part.moment_inertia + part.volume * (
            np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset))
    return center, inertia * mass / volume


def principal_inertia(inertia):
    inertia = (inertia + inertia.T) / 2
    values, axes = np.linalg.eigh(inertia)
    if not np.isfinite(values).all() or values.min() <= 0 or values[-1] > values[:2].sum() + 1e-12:
        raise ValueError('Inertia is not a physically valid positive tensor')
    if np.linalg.det(axes) < 0:
        axes[:, 0] *= -1
    # scipy is xyzw, USD is wxyz. Columns are principal axes in asset coordinates.
    xyzw = Rotation.from_matrix(axes).as_quat()
    quaternion = np.r_[xyzw[3], xyzw[:3]]
    reconstructed = axes @ np.diag(values) @ axes.T
    if not np.allclose(inertia, reconstructed, atol=1e-12, rtol=1e-7):
        raise ValueError('Principal-axis conversion failed')
    return values, quaternion


def triangle_shell_colliders(mesh, thickness):
    """Thicken connected convex planar patches, preserving concave boundaries and openings."""
    used = set()
    parts = []
    for group in mesh.facets:
        triangles = mesh.triangles[group]
        normal = mesh.face_normals[group[0]]
        vertices = triangles.reshape(-1, 3)
        hull = trimesh.convex.convex_hull(np.concatenate(
            [vertices + normal * thickness / 2, vertices - normal * thickness / 2]))
        # Joining a concave patch would seal holes or fork-tine gaps; keep its triangles separate.
        expected = mesh.area_faces[group].sum() * thickness
        if hull.volume <= expected * 1.001 and len(hull.vertices) <= 128:
            parts.append(hull)
            used.update(group.tolist())
    for i, triangle in enumerate(mesh.triangles):
        if i in used:
            continue
        normal = mesh.face_normals[i]
        points = np.concatenate([triangle + normal * thickness / 2, triangle - normal * thickness / 2])
        parts.append(trimesh.convex.convex_hull(points))
    return parts


def make_colliders(mesh, profile, cache):
    import coacd
    collision_settings = {k: profile[k] for k in ('collision_method', 'concavity_tolerance_m')}
    if profile['collision_method'] == 'triangle_shell':
        collision_settings['shell_thickness_m'] = profile['collision_shell_thickness_m']
    key = hashlib.sha256(mesh.vertices.tobytes() + mesh.faces.tobytes() +
                         json.dumps(collision_settings,
                                    sort_keys=True).encode() + b'coacd-1.0.9-settings-v1').hexdigest()
    path = cache / (key + '.npz')
    if path.exists():
        with np.load(path, allow_pickle=False) as data:
            return [trimesh.Trimesh(data[f'v{i}'], data[f'f{i}'], process=True)
                    for i in range(len(data.files) // 2)]
    method = profile['collision_method']
    if method == 'box':
        part = trimesh.creation.box(extents=mesh.extents)
        part.apply_translation(mesh.bounds.mean(axis=0))
        parts = [part]
    elif method == 'convex_hull':
        parts = [mesh.convex_hull]
    elif method == 'coacd':
        coacd.set_log_level('warn')
        arrays = coacd.run_coacd(coacd.Mesh(mesh.vertices, mesh.faces),
                                threshold=profile['concavity_tolerance_m'], real_metric=True,
                                preprocess_mode='auto', preprocess_resolution=80,
                                resolution=1500, mcts_iterations=60, mcts_nodes=15,
                                mcts_max_depth=3, decimate=True, max_ch_vertex=64, seed=2026)
        parts = [trimesh.Trimesh(v, f, process=True).convex_hull for v, f in arrays]
    elif method == 'triangle_shell':
        parts = triangle_shell_colliders(mesh, profile['collision_shell_thickness_m'])
    else:
        raise ValueError('Unsupported collision method: ' + method)
    # PhysX convex cooking accepts at most 256 vertices; pin a smaller budget ourselves.
    for i, part in enumerate(parts):
        if len(part.vertices) > 128:
            reduced = coacd.run_coacd(coacd.Mesh(part.vertices, part.faces), threshold=1.0,
                                     preprocess_mode='off', decimate=True, max_ch_vertex=128, seed=2026)
            if len(reduced) != 1:
                raise ValueError('Convex vertex reduction unexpectedly split a hull')
            parts[i] = trimesh.Trimesh(*reduced[0], process=True).convex_hull
    if not parts or any(not p.is_volume or len(p.vertices) > 256 for p in parts):
        raise ValueError('Invalid convex collider')
    arrays = {k: a for i, p in enumerate(parts) for k, a in [(f'v{i}', p.vertices), (f'f{i}', p.faces)]}
    with path.with_suffix('.partial').open('wb') as stream:
        np.savez_compressed(stream, **arrays)
    path.with_suffix('.partial').replace(path)
    return parts


def inside_parts(points, parts, tolerance=1e-7):
    covered = np.zeros(len(points), dtype=bool)
    for part in parts:
        equations = ConvexHull(part.vertices).equations
        covered |= (points @ equations[:, :3].T + equations[:, 3] <= tolerance).all(axis=1)
    return covered


def cavity_check(mesh, parts, required):
    if not required:
        return {'required': False}
    # Locate free centerline points in the upper half of the original cavity first.
    # A handful of probes tests central access; it does not prove all openings/handles are preserved.
    xy = mesh.bounds.mean(axis=0)[:2]
    points = np.array([[*xy, z] for z in np.linspace(mesh.bounds[0, 2] + .55 * mesh.extents[2],
                                                    mesh.bounds[0, 2] + .85 * mesh.extents[2], 5)])
    origin = points + [0, 0, 2 * mesh.extents[2]]
    hits, ray_ids, _ = mesh.ray.intersects_location(origin, np.tile([0, 0, -1], (len(points), 1)))
    # A visible inner floor must lie below the probe; avoid trusting contains() on open scans.
    free = np.array([len(hits[ray_ids == i]) > 0 and
                     hits[ray_ids == i, 2].max() < point[2] - .002
                     for i, point in enumerate(points)])
    points = points[free]
    if len(points) < 3:
        raise ValueError('Cannot establish cavity probes from source; requires manual profile')
    blocked = inside_parts(points, parts)
    if blocked.any():
        raise ValueError('Collider blocks central cavity access')
    return {'required': True, 'points_m': points.tolist(), 'blocked_points': 0,
            'scope': 'central_access_only_handles_and_entire_opening_require_review'}


def apply_physx(prim, schema, attributes):
    from pxr import Sdf
    prim.AddAppliedSchema(schema)
    for name, (kind, value) in attributes.items():
        prim.CreateAttribute(name, getattr(Sdf.ValueTypeNames, kind), custom=False).Set(value)


def usd_mesh(stage, path, mesh):
    from pxr import UsdGeom, Vt
    result = UsdGeom.Mesh.Define(stage, path)
    result.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(mesh.vertices.astype(np.float32)))
    result.CreateFaceVertexCountsAttr([3] * len(mesh.faces))
    result.CreateFaceVertexIndicesAttr(mesh.faces.flatten().tolist())
    result.CreateSubdivisionSchemeAttr('none')
    result.CreateExtentAttr(Vt.Vec3fArray.FromNumpy(mesh.bounds.astype(np.float32)))
    return result


def export_usd(directory, visual, parts, center, inertia, profile, materials, solver):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Sdf, Gf, Vt
    path = directory / 'object.usdc'
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    root = UsdGeom.Xform.Define(stage, '/Object').GetPrim()
    stage.SetDefaultPrim(root)
    root.SetCustomDataByKey('physicsStatus', 'estimated_baseline_requires_calibration')
    UsdPhysics.RigidBodyAPI.Apply(root).CreateRigidBodyEnabledAttr(True)
    mass = UsdPhysics.MassAPI.Apply(root)
    values, quaternion = principal_inertia(inertia)
    mass.CreateMassAttr(profile['mass_kg'])
    mass.CreateCenterOfMassAttr(Gf.Vec3f(*center))
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(*values))
    mass.CreatePrincipalAxesAttr(Gf.Quatf(float(quaternion[0]), Gf.Vec3f(*quaternion[1:])))
    apply_physx(root, 'PhysxRigidBodyAPI', {
        'physxRigidBody:enableCCD': ('Bool', solver['enable_ccd']),
        'physxRigidBody:linearDamping': ('Float', solver['linear_damping']),
        'physxRigidBody:angularDamping': ('Float', solver['angular_damping']),
        'physxRigidBody:solverPositionIterationCount': ('Int', solver['solver_position_iterations']),
        'physxRigidBody:solverVelocityIterationCount': ('Int', solver['solver_velocity_iterations']),
        'physxRigidBody:maxDepenetrationVelocity': ('Float', solver['max_depenetration_velocity_m_s'])})
    visual_usd = usd_mesh(stage, '/Object/Visual', visual)
    visual_usd.CreateDoubleSidedAttr(True)
    visual_usd.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(visual.vertex_normals.astype(np.float32)))
    visual_usd.SetNormalsInterpolation('vertex')
    uv = np.asarray(visual.visual.uv)
    if uv.shape != (len(visual.vertices), 2) or not np.isfinite(uv).all():
        raise ValueError('Invalid visual UV coordinates')
    UsdGeom.PrimvarsAPI(visual_usd).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray,
                                               'vertex').Set(Vt.Vec2fArray.FromNumpy(uv.astype(np.float32)))
    (directory / 'textures').mkdir()
    source_material = visual.visual.material
    if not isinstance(source_material, trimesh.visual.material.SimpleMaterial) or source_material.image is None:
        raise ValueError('Expected single textured OBJ material')
    source_material.image.convert('RGB').save(directory / 'textures' / 'albedo.png')
    material = UsdShade.Material.Define(stage, '/Object/Looks/Visual')
    shader = UsdShade.Shader.Define(stage, material.GetPath().AppendChild('Surface'))
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.5)
    texture = UsdShade.Shader.Define(stage, material.GetPath().AppendChild('Texture'))
    texture.CreateIdAttr('UsdUVTexture')
    texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('textures/albedo.png'))
    texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
    reader = UsdShade.Shader.Define(stage, material.GetPath().AppendChild('UV'))
    reader.CreateIdAttr('UsdPrimvarReader_float2')
    reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
    texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
    texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
    shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), 'rgb')
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    UsdShade.MaterialBindingAPI.Apply(visual_usd.GetPrim()).Bind(material)
    contact = UsdShade.Material.Define(stage, '/Object/Looks/Contact')
    contact_settings = materials[profile['material']]
    api = UsdPhysics.MaterialAPI.Apply(contact.GetPrim())
    api.CreateStaticFrictionAttr(contact_settings['static_friction'])
    api.CreateDynamicFrictionAttr(contact_settings['dynamic_friction'])
    api.CreateRestitutionAttr(contact_settings['restitution'])
    apply_physx(contact.GetPrim(), 'PhysxMaterialAPI', {
        'physxMaterial:frictionCombineMode': ('Token', contact_settings['friction_combine_mode']),
        'physxMaterial:restitutionCombineMode': ('Token', contact_settings['restitution_combine_mode'])})
    for i, part in enumerate(parts):
        collider = usd_mesh(stage, f'/Object/Collisions/Part_{i:03}', part)
        collider.CreateVisibilityAttr('invisible')
        collider.CreatePurposeAttr('guide')
        prim = collider.GetPrim()
        UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
        UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr('convexHull')
        apply_physx(prim, 'PhysxCollisionAPI', {
            'physxCollision:contactOffset': ('Float', solver['contact_offset_m']),
            'physxCollision:restOffset': ('Float', solver['rest_offset_m'])})
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(contact, materialPurpose='physics')
    stage.GetRootLayer().Save()


def build_one(task):
    try:
        return _build_one(task)
    except Exception as error:
        print(f"PHYSICS_FAILED {task[0]['id']}: {type(error).__name__}: {error}", flush=True)
        raise


def _build_one(task):
    obj, bundle, profile, settings, cache, output = task
    validate_profile(profile, settings['materials'])
    source_dir = Path(bundle['source_directory'])
    provenance = json.loads((source_dir / 'provenance.json').read_text())
    # Check all upstream files, including textures/MTLs used by the OBJ loader.
    for name, expected in provenance['files'].items():
        if digest(source_dir / name) != expected:
            raise ValueError('Source hash mismatch: ' + name)
    source = source_dir / obj['visual_mesh']
    visual = trimesh.load(source, force='mesh', process=False)
    visual, transform = normalize_mesh(visual, profile)
    mesh = geometry_mesh(visual)
    parts = make_colliders(mesh, profile, Path(cache))
    # USD Mesh stores float32 points. Re-hull at that precision so almost
    # coplanar facets remain convex after serialization, not just in float64.
    parts = [trimesh.Trimesh(p.vertices.astype(np.float32), p.faces, process=True).convex_hull
             for p in parts]
    cavity = cavity_check(mesh, parts, profile['cavity_probe'])
    model = profile['mass_distribution_model']
    if model == 'surface_lamina':
        center, inertia = surface_mass_properties(mesh, profile['mass_kg'])
    elif model == 'uniform_volume' and mesh.is_volume:
        center = mesh.center_mass
        inertia = mesh.moment_inertia * profile['mass_kg'] / mesh.volume
    elif profile['collision_method'] == 'triangle_shell' and model == 'uniform_volume':
        model = 'uniform_visual_convex_hull_mass_proxy'
        center, inertia = compound_mass_properties([mesh.convex_hull], profile['mass_kg'])
    else:
        model = 'summed_convex_volume_overlap_not_subtracted'
        center, inertia = compound_mass_properties(parts, profile['mass_kg'])
    values, quaternion = principal_inertia(inertia)
    directory = Path(output) / obj['id']
    with tempfile.TemporaryDirectory(prefix='.' + obj['id'] + '-', dir=output) as temporary:
        staging = Path(temporary)
        export_usd(staging, visual, parts, center, inertia, profile, settings['materials'], settings['solver'])
        metadata = {
            'id': obj['id'], 'label_zh': obj['label_zh'], 'category': obj['category'],
            'source': bundle['source'], 'source_visual_sha256': digest(source), 'profile': profile,
            'coordinate_frame': 'meters_Z_up_XY_bounds_center_bottom_origin',
            'source_to_asset_transform_column_vectors': transform.tolist(), 'extent_m': mesh.extents.tolist(),
            'mass_kg': profile['mass_kg'], 'center_of_mass_m': center.tolist(),
            'inertia_kg_m2_about_com_asset_axes': inertia.tolist(),
            'principal_inertia_kg_m2': values.tolist(), 'principal_axes_quaternion_wxyz': quaternion.tolist(),
            'mass_distribution_method_used': model, 'contact_material': settings['materials'][profile['material']],
            'solver': settings['solver'], 'source_watertight': mesh.is_watertight,
            'source_is_volume': mesh.is_volume, 'collider_count': len(parts), 'cavity_check': cavity,
            'validation_status': 'offline_validated_runtime_pending', 'physically_calibrated': False}
        write_json(staging / 'physics.json', metadata)
        write_json(staging / 'SOURCE.json', provenance)
        license_path = source_dir / 'License.txt'
        if license_path.exists():
            shutil.copy2(license_path, staging / 'SOURCE-LICENSE.txt')
        from validate_physics import validate_asset
        metadata['offline_validation'] = validate_asset(staging / 'object.usdc', metadata)
        write_json(staging / 'physics.json', metadata)
        write_json(staging / 'complete.json', {str(p.relative_to(staging)): digest(p)
                   for p in staging.rglob('*') if p.is_file()})
        if directory.exists():
            shutil.rmtree(directory)
        staging.rename(directory)
    print(f"PHYSICS_BUILT {obj['id']} colliders={len(parts)}", flush=True)
    return {'id': obj['id'], 'usd': str(directory / 'object.usdc'), 'metadata': str(directory / 'physics.json'),
            'mass_status': profile['mass_status'], 'collider_count': len(parts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, default=HERE / 'physics_profiles.json')
    parser.add_argument('--only', nargs='+')
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    data = args.data_dir.resolve()
    if data == Path('/') or data == REPO or REPO in data.parents or data in REPO.parents:
        raise ValueError('Use a dedicated data directory outside the checkout')
    settings = json.loads(args.profiles.read_text())
    inventory = json.loads((data / 'inventory.json').read_text())
    version = hashlib.sha256(args.profiles.read_bytes() + Path(__file__).read_bytes() +
                             (HERE / 'validate_physics.py').read_bytes() +
                             inventory['catalog_sha256'].encode()).hexdigest()[:16]
    output = data / 'physics' / version
    output.mkdir(parents=True, exist_ok=True)
    cache = data / 'physics-cache'
    cache.mkdir(exist_ok=True)
    os.environ.setdefault('OMP_NUM_THREADS', '2')
    tasks = [(obj, bundle, settings['objects'][obj['id']], settings, str(cache), str(output))
             for bundle in inventory['bundles'] for obj in bundle['objects']
             if not args.only or obj['id'] in args.only]
    if args.only and set(args.only) != {task[0]['id'] for task in tasks}:
        raise ValueError('Unknown requested object id')
    with (data / 'physics-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (output / 'physics_inventory.json').unlink(missing_ok=True)
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(build_one, tasks))
        write_json(output / 'physics_inventory.json', {
            'generation': version, 'goal_zh': settings['goal_zh'], 'object_count': len(results),
            'profiles_sha256': digest(args.profiles), 'objects': results,
            'dependencies': {name: importlib.metadata.version(name) for name in
                             ('numpy', 'scipy', 'trimesh', 'coacd', 'Pillow')},
            'offline_validated': True, 'runtime_validated': False, 'physically_calibrated': False})
        temporary = data / 'physics' / 'latest.partial'
        write_json(temporary, {'generation': version, 'directory': str(output), 'object_count': len(results)})
        temporary.replace(data / 'physics' / 'latest.json')
    print('PHYSICS_INVENTORY ' + str(output / 'physics_inventory.json'), flush=True)


if __name__ == '__main__':
    main()
