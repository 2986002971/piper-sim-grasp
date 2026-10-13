"""Analytic inertia oracles and failure checks for portable simulation assets."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import numpy as np
    import trimesh
    from scipy.spatial.transform import Rotation
except ImportError as exc:
    raise unittest.SkipTest('Requires models/objects/requirements-physics.txt') from exc

HERE = Path(__file__).resolve().parents[1] / 'models' / 'objects'
sys.path.insert(0, str(HERE))
from build_physics import (surface_mass_properties, compound_mass_properties, principal_inertia,
                           normalize_mesh, validate_profile, cavity_check, export_usd,
                           triangle_shell_colliders, inside_parts)
from validate_physics import validate_asset


class InertiaTest(unittest.TestCase):
    def test_runtime_height_uses_cooked_geometry_and_exposes_authored_gap(self):
        from check_dynamics import evaluate
        records = {'/Object': {'id':'sphere', 'role':'drop', 'initial_position':np.array([0,0,.15]),
                              'collider_vertices':np.array([[0,0,0],[0,0,.1]]),
                              'authored_collider_vertices':np.array([[0,0,-.004],[0,0,.1]]),
                              'collider_geometry_source':'isaac_physx_cooked',
                              'cooking_max_plane_gap_m':.004}}
        state = {'position':np.zeros(3), 'orientation':np.array([0,0,0,1]),
                 'linearVelocity':np.zeros(3), 'angularVelocity':np.zeros(3)}
        result = evaluate(records, [{'/Object':state} for _ in range(5)])[0]
        self.assertTrue(result['passed'])
        self.assertEqual(result['collider_minimum_z_m'],0)
        self.assertEqual(result['authored_collider_minimum_z_m'],-.004)
        self.assertEqual(result['cooking_max_plane_gap_m'],.004)

    def test_surface_extrusion_has_declared_thickness_and_keeps_ring_hole(self):
        triangle = trimesh.Trimesh([[0,0,0],[.1,0,0],[0,.1,0]],[[0,1,2]],process=False)
        parts = triangle_shell_colliders(triangle,.001)
        self.assertEqual(len(parts),1)
        self.assertAlmostEqual(parts[0].volume,.005*.001,places=12)
        self.assertAlmostEqual(parts[0].extents[2],.001,places=12)
        ring = trimesh.creation.annulus(r_min=.03,r_max=.06,height=.005,sections=16)
        parts = triangle_shell_colliders(ring,.001)
        self.assertFalse(inside_parts(np.array([[0.,0.,0.]]),parts)[0])

    def test_triangle_surface_has_analytic_second_moments(self):
        triangle = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], process=False)
        center, tensor = surface_mass_properties(triangle, 2.)
        np.testing.assert_allclose(center, [1/3, 1/3, 0])
        np.testing.assert_allclose(tensor, [[1/9, 1/18, 0], [1/18, 1/9, 0], [0, 0, 2/9]], atol=1e-14)

    def test_rotated_translated_box_uses_com_and_principal_frame(self):
        box = trimesh.creation.box(extents=[.1, .2, .3])
        rotation = Rotation.from_euler('xyz', [.3, .4, .7]).as_matrix()
        transform = np.eye(4); transform[:3, :3] = rotation; transform[:3, 3] = [3, -2, 4]
        box.apply_transform(transform)
        center, tensor = compound_mass_properties([box], 2.)
        expected = 2/12 * np.array([.2**2+.3**2, .1**2+.3**2, .1**2+.2**2])
        np.testing.assert_allclose(center, [3, -2, 4])
        np.testing.assert_allclose(tensor, rotation @ np.diag(expected) @ rotation.T, atol=1e-12)
        values, wxyz = principal_inertia(tensor)
        axes = Rotation.from_quat([*wxyz[1:], wxyz[0]]).as_matrix()
        np.testing.assert_allclose(axes @ np.diag(values) @ axes.T, tensor, atol=1e-12)
        with self.assertRaises(ValueError): principal_inertia(np.diag([1., 1., 3.]))

    def test_normalization_preserves_uniform_scale_and_up_axis(self):
        box = trimesh.creation.box(extents=[1, 3, 2]); box.apply_translation([3, 4, 5])
        normalized, transform = normalize_mesh(box, {'source_up_axis':'Y', 'nominal_size':{'dimension':'height','value_m':.24}})
        np.testing.assert_allclose(normalized.extents, [.08, .16, .24])
        np.testing.assert_allclose(normalized.bounds[0], [-.04, -.08, 0], atol=1e-14)
        np.testing.assert_allclose(normalized.vertices, trimesh.transform_points(box.vertices, transform))

    def test_bad_mass_and_material_fail(self):
        profile = {'mass_kg':-1, 'source_up_axis':'Z','nominal_size':None,'material':'metal'}
        with self.assertRaises(ValueError): validate_profile(profile, {})
        profile['mass_kg']=.1
        bad = {'metal': {'static_friction':.2,'dynamic_friction':.6,'restitution':0}}
        with self.assertRaises(ValueError): validate_profile(profile, bad)

    def test_portable_usd_matches_inertia_and_rejects_missing_texture(self):
        if importlib.util.find_spec('pxr') is None: self.skipTest('Requires OpenUSD bindings')
        from PIL import Image
        box = trimesh.creation.box(extents=[.1, .2, .3]); box.apply_translation([0, 0, .15])
        box.visual = trimesh.visual.TextureVisuals(uv=np.zeros((len(box.vertices), 2)),
                     material=trimesh.visual.material.SimpleMaterial(image=Image.new('RGB', (2,2))))
        center, tensor = compound_mass_properties([box], .2)
        profile = {'mass_kg':.2,'material':'metal'}
        materials = {'metal':{'static_friction':.5,'dynamic_friction':.4,'restitution':0,
                             'friction_combine_mode':'average','restitution_combine_mode':'average'}}
        solver = {'enable_ccd':True,'linear_damping':.01,'angular_damping':.05,
                  'solver_position_iterations':16,'solver_velocity_iterations':4,
                  'max_depenetration_velocity_m_s':1.,'contact_offset_m':.001,'rest_offset_m':0.}
        metadata = {'mass_kg':.2,'center_of_mass_m':center,'inertia_kg_m2_about_com_asset_axes':tensor,
                    'collider_count':1,'extent_m':box.extents}
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            export_usd(directory, box, [box], center, tensor, profile, materials, solver)
            validate_asset(directory/'object.usdc', metadata)
            altered=dict(metadata);altered['mass_kg']=.3
            with self.assertRaises(ValueError):validate_asset(directory/'object.usdc', altered)
            altered=dict(metadata);altered['inertia_kg_m2_about_com_asset_axes']=tensor * 1.05
            with self.assertRaises(ValueError):validate_asset(directory/'object.usdc', altered)
            (directory/'textures/albedo.png').unlink()
            with self.assertRaises(ValueError):validate_asset(directory/'object.usdc', metadata)

    def test_cavity_check_rejects_sealing_hull(self):
        # Analytic open-topped cup built from a floor and four walls.
        parts=[]
        for size, position in [([.1,.1,.005],[0,0,.0025]),([.005,.1,.1],[-.05,0,.05]),
                               ([.005,.1,.1],[.05,0,.05]),([.1,.005,.1],[0,-.05,.05]),
                               ([.1,.005,.1],[0,.05,.05])]:
            part=trimesh.creation.box(extents=size);part.apply_translation(position);parts.append(part)
        mesh=trimesh.util.concatenate(parts)
        self.assertEqual(cavity_check(mesh,parts,True)['blocked_points'],0)
        with self.assertRaises(ValueError):cavity_check(mesh,[mesh.convex_hull],True)


if __name__ == '__main__':
    unittest.main()
