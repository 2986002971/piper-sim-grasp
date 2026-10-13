import importlib.util
from pathlib import Path
import unittest
try:
    import numpy as np
    import trimesh
    import rtree
    from scipy.spatial.transform import Rotation
    from pxr import Usd
except ImportError as exc:
    raise unittest.SkipTest('Requires models/objects/requirements-physics.txt and OpenUSD bindings') from exc

path = Path(__file__).resolve().parents[1] / 'models/appliances/build_physics.py'
spec = importlib.util.spec_from_file_location('appliance_physics', path)
physics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physics)


class AppliancePhysicsTests(unittest.TestCase):
    def test_weighted_boxes_obey_parallel_axis_theorem(self):
        components = [(physics.box([1, 1, 1], [-1, 0, 0]), 2, 'solid'),
                      (physics.box([1, 1, 1], [1, 0, 0]), 2, 'solid')]
        mass, center, tensor = physics.aggregate_properties(components)
        self.assertEqual(mass, 4)
        np.testing.assert_allclose(center, 0, atol=1e-12)
        np.testing.assert_allclose(tensor, np.diag([4 / 6, 4 / 6 + 4, 4 / 6 + 4]), atol=1e-12)

    def test_ring_volume_and_open_center(self):
        parts = physics.ring_segments(0.25, 0.24, 0.3, [0, 0, 0])
        expected = np.pi * (0.25 ** 2 - 0.24 ** 2) * 0.3
        self.assertLess(abs(sum(p.volume for p in parts) / expected - 1), 0.002)
        self.assertTrue(all(p.is_volume for p in parts))
        self.assertTrue(all(not p.contains([[0, 0, 0]])[0] for p in parts))

    def test_rectangular_front_preserves_aperture_and_corners(self):
        parts = physics.rectangular_aperture(0.6, -0.4, 0.4, 0.16, 0.01, np.zeros(3))
        self.assertTrue(all(not p.contains([[0, 0, 0]])[0] for p in parts))
        corners = [[x, 0, z] for x in [-0.299, 0.299] for z in [-0.399, 0.399]]
        covered = np.any([p.contains(corners) for p in parts], axis=0)
        self.assertTrue(covered.all())


if __name__ == '__main__':
    unittest.main()
