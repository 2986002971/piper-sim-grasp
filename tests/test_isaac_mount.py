"""Wrist-relative camera mount lookup against real OpenUSD transforms."""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, sentinel

try:
    from pxr import Gf, Usd, UsdGeom
except ImportError as exc:
    raise unittest.SkipTest('Requires Isaac OpenUSD bindings') from exc

from runtime.isaac.run_isaac_ros import create_joint_graph


class IsaacMountTests(unittest.TestCase):
    def test_wrist_name_and_relative_mount(self):
        core = MagicMock()
        core.Controller.edit.return_value = (sentinel.graph, None, None, None)
        omni = types.ModuleType('omni')
        graph = types.ModuleType('omni.graph')
        omni.graph = graph
        graph.core = core
        modules = {'omni': omni, 'omni.graph': graph, 'omni.graph.core': core}
        config = {'bridge_feedback_topic': '/feedback', 'bridge_command_topic': '/command'}
        with patch.dict(sys.modules, modules):
            for name in ('Link6', 'link6', 'other_link'):
                with self.subTest(wrist=name):
                    stage = Usd.Stage.CreateInMemory()
                    root = UsdGeom.Xform.Define(stage, '/Robot')
                    wrist = UsdGeom.Xform.Define(stage, '/Robot/' + name)
                    wrist.AddTranslateOp().Set(Gf.Vec3d(2, 3, 4))
                    optical = UsdGeom.Xform.Define(stage, str(wrist.GetPath()) + '/optical')
                    optical.AddTranslateOp().Set(Gf.Vec3d(0.1, 0.2, 0.3))
                    camera = UsdGeom.Camera.Define(stage, str(optical.GetPath()) + '/D405_RGB')
                    core.Controller.edit.reset_mock()
                    if name != 'link6':
                        with self.assertRaisesRegex(ValueError, 'must belong to Piper link6'):
                            create_joint_graph(root.GetPrim(), camera, config)
                        core.Controller.edit.assert_not_called()
                        continue
                    actual_graph, mount = create_joint_graph(root.GetPrim(), camera, config)
                    self.assertIs(actual_graph, sentinel.graph)
                    for actual, expected in zip(mount['translation'], (0.1, 0.2, 0.3)):
                        self.assertAlmostEqual(actual, expected)
                    self.assertEqual(mount['rotation_xyzw'], [0.0, 0.0, 0.0, 1.0])
                    core.Controller.edit.assert_called_once()
