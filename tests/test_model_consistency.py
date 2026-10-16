"""Regression: reject arm geometry from a different assembly revision."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

try:
    spec = importlib.util.spec_from_file_location('grasp_model', Path(__file__).resolve().parents[1]/'deploy/prepare-grasp-model.py')
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
except ImportError:
    model = None


@unittest.skipIf(model is None, 'requires numpy, scipy and yaml')
class ModelConsistencyTests(unittest.TestCase):
    def test_bracket_exemption_keeps_geometry_and_other_pairs_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'assembly.urdf'
            path.write_text('''<robot>
              <link name="d405_link"><collision><geometry><box size=".02 .04 .04"/></geometry></collision></link>
              <link name="d405_bracket"><visual><geometry><mesh filename="bracket.stl" scale=".001 .001 .001"/></geometry></visual></link>
              <joint name="camera" type="fixed"><parent link="link6"/><child link="d405_link"/></joint>
              <joint name="bracket" type="fixed"><parent link="link6"/><child link="d405_bracket"/></joint>
            </robot>''')
            robot = ET.fromstring('''<robot><link name="link3"/><link name="link4"/><link name="link5"/><link name="link6"/>
              <joint name="joint4" type="revolute"><parent link="link3"/><child link="link4"/></joint>
              <joint name="joint5" type="revolute"><parent link="link4"/><child link="link5"/></joint>
              <joint name="joint6" type="revolute"><parent link="link5"/><child link="link6"/></joint>
            </robot>''')
            semantic = ET.fromstring('<robot/>')
            model.add_device_collisions(robot, semantic, path)
            excluded = {frozenset((e.get('link1'), e.get('link2'))) for e in semantic.findall('disable_collisions')}
            self.assertIn(frozenset(('link4', 'sim_d405_bracket')), excluded)
            self.assertNotIn(frozenset(('link3', 'sim_d405_bracket')), excluded)
            self.assertNotIn(frozenset(('link4', 'sim_d405_link')), excluded)
            self.assertIsNotNone(robot.find("link[@name='sim_d405_bracket']/collision/geometry/mesh"))

    def test_mixed_joint_frame_and_scaled_mesh_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mesh = directory/'body.stl'
            mesh.write_bytes(b'geometry fixture')
            robot = ET.fromstring(f'''<robot>
              <link name="link4"/><link name="link5"><collision><geometry>
                <mesh filename="{mesh.as_uri()}"/>
              </geometry></collision></link>
              <joint name="joint5" type="revolute"><parent link="link4"/>
                <child link="link5"/><origin xyz="0.07466 0 0"/><axis xyz="0 0 1"/>
              </joint></robot>''')
            assembly = ET.fromstring(ET.tostring(robot))
            assembly.find('.//mesh').set('filename', 'body.stl')
            path = directory/'assembly.urdf'
            def check():
                ET.ElementTree(assembly).write(path)
                model.validate_robot_geometry(robot, path)
            check()
            assembly.find('joint/origin').set('xyz', '0.076857 0.00062395 0')
            with self.assertRaisesRegex(ValueError, 'origin differs'): check()
            assembly.find('joint/origin').set('xyz', '0.07466 0 0')
            assembly.find('.//mesh').set('scale', '0.8 0.8 0.8')
            with self.assertRaisesRegex(ValueError, 'scale differs'): check()


if __name__ == '__main__':
    unittest.main()
