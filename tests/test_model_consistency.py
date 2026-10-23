"""The external model remains read only; simulation derives matching articulation frames."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('grasp_model', Path(__file__).resolve().parents[1]/'deploy/prepare-grasp-model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


class ModelConsistencyTests(unittest.TestCase):
    def test_prepare_uses_macro_geometry_and_keeps_assembly_body_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root/'main'
            config = main/'src/manipulation/nankai_robocup_arm_control/config'
            config.mkdir(parents=True)
            robot = ET.Element('robot', name='test')
            ET.SubElement(robot, 'link', name='piper_base_link')
            for i in range(1,9):
                joint = ET.SubElement(robot, 'joint', name=f'joint{i}', type='revolute' if i<=6 else 'prismatic')
                ET.SubElement(joint, 'parent', link='piper_base_link' if i==1 else f'link{i-1}')
                ET.SubElement(joint, 'child', link=f'link{i}')
                ET.SubElement(joint, 'origin', xyz='0 -.03526 0' if i==6 else '0 0 0')
                ET.SubElement(joint, 'axis', xyz='0 0 1')
                ET.SubElement(joint, 'limit', lower='-3.141593', upper='3.141593')
                ET.SubElement(robot, 'link', name=f'link{i}')
            nominal = config/'robot.urdf'
            ET.ElementTree(robot).write(nominal)
            assembly = ET.fromstring(ET.tostring(robot))
            assembly.find("link[@name='piper_base_link']").set('name','base_link')
            assembly.find("joint[@name='joint1']/parent").set('link','base_link')
            assembly.find("joint[@name='joint6']/origin").set('xyz','0 -.035 0')
            assembly_file = root/'assembly.urdf'
            ET.ElementTree(assembly).write(assembly_file)
            before = nominal.read_bytes(), assembly_file.read_bytes()
            output = model.prepare(main, assembly_file, root/'output')
            derived = ET.parse(output/'robot.urdf').getroot()
            physical = ET.parse(output/'assembly.urdf').getroot()
            self.assertEqual(derived.find("joint[@name='sim_base_alias']/child").get('link'),'base_link')
            self.assertEqual(derived.find("joint[@name='joint1']/parent").get('link'),'base_link')
            self.assertEqual(physical.find("joint[@name='joint6']/origin").get('xyz'),'0 -.03526 0')
            self.assertEqual(physical.find("joint[@name='joint6']/limit").get('upper'),'3.141593')
            self.assertEqual(before, (nominal.read_bytes(),assembly_file.read_bytes()))


if __name__ == '__main__': unittest.main()
