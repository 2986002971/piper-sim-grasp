"""Offline USD regression for derived joint frames and inherited physics."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

try:
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
except ImportError:
    Usd = None


@unittest.skipIf(Usd is None, 'requires Isaac/OpenUSD')
class ControlSceneTests(unittest.TestCase):
    def test_derived_layer_preserves_robot_physics_and_enables_dynamic_bottle(self):
        from models.prepare_control_scene import prepare
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.usda'
            stage = Usd.Stage.CreateNew(str(source))
            UsdGeom.SetStageMetersPerUnit(stage, 1.)
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            robot = stage.DefinePrim('/World/Robot', 'Xform')
            UsdPhysics.ArticulationRootAPI.Apply(robot)
            definition = ET.Element('robot')
            for name in ('base_link', *[f'link{i}' for i in range(1,9)]):
                body = stage.DefinePrim('/World/Robot/'+name, 'Xform')
                UsdPhysics.RigidBodyAPI.Apply(body)
                UsdPhysics.MassAPI.Apply(body).CreateMassAttr(1.25)
            for i in range(1,9):
                kind = UsdPhysics.RevoluteJoint if i<=6 else UsdPhysics.PrismaticJoint
                joint = kind.Define(stage, f'/World/Robot/joint{i}')
                parent, child = ('base_link' if i==1 else f'link{i-1}'), f'link{i}'
                joint.CreateBody0Rel().SetTargets(['/World/Robot/'+parent])
                joint.CreateBody1Rel().SetTargets(['/World/Robot/'+child])
                joint.CreateAxisAttr('Z')
                for side in (0,1):
                    getattr(joint, f'CreateLocalPos{side}Attr')(Gf.Vec3f(0))
                    getattr(joint, f'CreateLocalRot{side}Attr')(Gf.Quatf(1))
                drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(),'angular' if i<=6 else 'linear')
                drive.CreateStiffnessAttr(400.); drive.CreateDampingAttr(50.); drive.CreateMaxForceAttr(100.)
                j = ET.SubElement(definition, 'joint', name=f'joint{i}')
                ET.SubElement(j,'parent',link=parent); ET.SubElement(j,'child',link=child)
                ET.SubElement(j,'origin',xyz='0 -.03526 0' if i==6 else '0 0 0')
                ET.SubElement(j,'axis',xyz='0 0 1'); ET.SubElement(j,'limit',lower='-3.141593',upper='3.141593')
            stage.DefinePrim('/World/SevenUP_Bottle', 'Xform')
            cube = UsdGeom.Cube.Define(stage, '/World/SevenUP_Bottle/body')
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
            stage.GetRootLayer().Save()
            before = source.read_bytes()
            urdf = root/'robot.urdf'; ET.ElementTree(definition).write(urdf)
            output = prepare(source, urdf, root/'derived.usda', {'prim':'/World/SevenUP_Bottle',
                'mass_kg':.3,'static_friction':.8,'dynamic_friction':.6,'restitution':0.})
            derived = Usd.Stage.Open(str(output))
            self.assertEqual(source.read_bytes(), before)
            body = derived.GetPrimAtPath('/World/SevenUP_Bottle')
            self.assertTrue(body.HasAPI(UsdPhysics.RigidBodyAPI))
            self.assertFalse(UsdPhysics.RigidBodyAPI(body).GetKinematicEnabledAttr().Get())
            self.assertAlmostEqual(UsdPhysics.MassAPI(body).GetMassAttr().Get(),.3)
            self.assertFalse(stage.GetPrimAtPath('/World/SevenUP_Bottle').HasAPI(UsdPhysics.RigidBodyAPI))
            for i in range(1,9):
                p = derived.GetPrimAtPath(f'/World/Robot/joint{i}')
                drive = UsdPhysics.DriveAPI(p, 'angular' if i<=6 else 'linear')
                self.assertEqual(drive.GetStiffnessAttr().Get(),400.)
                self.assertEqual(drive.GetDampingAttr().Get(),50.)
                self.assertEqual(drive.GetMaxForceAttr().Get(),100.)
                mass = UsdPhysics.MassAPI(derived.GetPrimAtPath('/World/Robot/link'+str(i)))
                self.assertEqual(mass.GetMassAttr().Get(),1.25)
            joint = UsdPhysics.RevoluteJoint(derived.GetPrimAtPath('/World/Robot/joint6'))
            self.assertAlmostEqual(joint.GetLocalPos0Attr().Get()[1],-.03526)
            self.assertAlmostEqual(joint.GetUpperLimitAttr().Get(),180.,places=3)
