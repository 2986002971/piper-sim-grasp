from pathlib import Path
import tarfile
import tempfile
import unittest

from models.bundle import export_bundle, material_paths, verify

try:
    from pxr import Sdf, Usd, UsdUtils
except ImportError:
    Usd = None


class MaterialTests(unittest.TestCase):
    def test_module_names_and_nested_resources_survive_and_roots_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ('one', 'two'):
                material = root/directory
                (material/'templates').mkdir(parents=True)
                (material/'glass.mdl').write_text('import .::templates::helper::*;')
                (material/'templates/helper.mdl').write_text('mdl 1.6;')
                (material/'label.png').write_bytes(b'texture')
            names = material_paths([root/'one/glass.mdl', root/'one/templates/helper.mdl',
                                    root/'two/glass.mdl'], root/'output.tar.gz')
            self.assertEqual(len(names), 6)
            for directory in ('one', 'two'):
                parent = Path(names[root/directory/'glass.mdl']).parent
                self.assertEqual(names[root/directory/'templates/helper.mdl'], str(parent/'templates/helper.mdl'))
                self.assertEqual(names[root/directory/'label.png'], str(parent/'label.png'))
            self.assertNotEqual(names[root/'one/glass.mdl'], names[root/'two/glass.mdl'])
            with self.assertRaisesRegex(ValueError, 'outside custom MDL'):
                material_paths([root/'one/glass.mdl'], root/'one/export.tar.gz')


@unittest.skipIf(Usd is None, 'requires Isaac/OpenUSD environment')
class BundleExportTests(unittest.TestCase):
    def test_export_remains_resolvable_after_original_directory_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; materials = source/'materials'
            (materials/'templates').mkdir(parents=True)
            (materials/'glass.mdl').write_text('mdl 1.6; import .::templates::helper::*;')
            (materials/'templates/helper.mdl').write_text('mdl 1.6;')
            (materials/'label.png').write_bytes(b'texture fixture')
            stage = Usd.Stage.CreateNew(str(source/'scene.usda'))
            stage.DefinePrim('/World').CreateAttribute('material', Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath('materials/glass.mdl'))
            stage.GetRootLayer().Save()
            (source/'mesh.stl').write_bytes(b'mesh fixture')
            (source/'robot.urdf').write_text('<robot><link><visual><geometry>'
                '<mesh filename="mesh.stl"/></geometry></visual></link></robot>')
            (source/'scene.json').write_text('{}')
            archive = root/'bundle.tar.gz'
            export_bundle(source/'scene.usda', source/'robot.urdf', source/'robot.urdf',
                          source/'scene.json', archive)
            source.rename(root/'original-moved')
            unpacked = root/'relocated'; unpacked.mkdir()
            with tarfile.open(archive) as tar: tar.extractall(unpacked, filter='data')
            manifest = verify(unpacked)
            for name in ('glass.mdl', 'templates/helper.mdl', 'label.png'):
                self.assertTrue(any(path.endswith('/'+name) for path in manifest['files']))
            layers, assets, missing = UsdUtils.ComputeAllDependencies(str(unpacked/'scene.usda'))
            self.assertFalse(missing)
            for path in [layer.realPath for layer in layers]+list(assets):
                self.assertTrue(Path(path).resolve().is_relative_to(unpacked))


if __name__ == '__main__': unittest.main()
