import unittest
import numpy as np
from runtime.isaac.gripper import drive_target


class GripperTests(unittest.TestCase):
    def test_symmetric_positions_and_force_limit(self):
        positions, caps = drive_target(.08, 5., np.array([10., 3.]))
        np.testing.assert_allclose(positions, [.04, -.04])
        np.testing.assert_allclose(caps, [5., 3.])
        _, zero = drive_target(0., 0., np.array([10., 10.]))
        np.testing.assert_array_equal(zero, [0., 0.])

    def test_invalid_request_never_updates_physx(self):
        for width, effort in ((-.1, 1), (.101, 1), (.05, -1), (float('nan'), 1), (.05, float('inf'))):
            with self.subTest(width=width, effort=effort), self.assertRaises(ValueError):
                drive_target(width, effort, np.array([10.,10.]))
