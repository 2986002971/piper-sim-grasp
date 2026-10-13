import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from models.bundle import digest, import_bundle
from deploy.main_project import stage_servo, SERVO


class DeploymentTests(unittest.TestCase):
    def test_bundle_relocation_corruption_and_repeat_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {'scene.usda': b'#usda 1.0', 'robot.urdf': b'<robot/>',
                     'assembly.urdf': b'<robot/>', 'scene.json': b'{}'}
            import hashlib
            manifest = dict(schema_version=1, scene='scene.usda', robot_urdf='robot.urdf',
                            assembly_urdf='assembly.urdf', scene_metadata='scene.json',
                            files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
            files['bundle.json'] = json.dumps(manifest).encode()
            archive = root/'bundle.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                for name,value in files.items():
                    member=tarfile.TarInfo(name);member.size=len(value);tar.addfile(member,io.BytesIO(value))
            first,_=import_bundle(archive,root/'first')
            again,_=import_bundle(archive,root/'first')
            moved,_=import_bundle(archive,root/'different-root')
            self.assertEqual(first,again)
            self.assertEqual(digest(first/'scene.usda'),digest(moved/'scene.usda'))
            (first/'scene.usda').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'corrupt'): import_bundle(archive,root/'first')

    def test_bundle_rejects_symlink_and_parent_escape(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive=Path(tmp)/'bad.tar'
                with tarfile.open(archive,'w') as tar:
                    member=tarfile.TarInfo(name);member.type=kind;member.linkname='/etc/passwd';tar.addfile(member)
                with self.assertRaisesRegex(ValueError,'unsafe'):import_bundle(archive,Path(tmp)/'dest')

    def test_servo_copy_cannot_write_external_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'main';target=source/SERVO;target.mkdir(parents=True)
            (target/'build_overlay.py').write_text('source')
            copy=stage_servo(source,Path(tmp)/'data')
            (copy/SERVO/'build_overlay.py').write_text('changed copy')
            self.assertEqual((target/'build_overlay.py').read_text(),'source')
            self.assertFalse((source/'build').exists())


if __name__=='__main__':unittest.main()
