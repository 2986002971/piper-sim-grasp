"""Failure checks for downloaded third-party asset handling (no network or Isaac)."""
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'models/objects/collect.py'
SPEC = importlib.util.spec_from_file_location('object_collect', MODULE_PATH)
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


class ObjectCollectionTests(unittest.TestCase):
    def test_zip_and_tar_reject_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('../escaped', 'payload')
            with self.assertRaises(ValueError):
                collect.extract(archive, 'zip', root / 'out')
            with tarfile.open(root / 'bad.tgz', 'w:gz') as t:
                member = tarfile.TarInfo('link')
                member.type = tarfile.SYMTYPE
                member.linkname = '/tmp'
                t.addfile(member)
            with self.assertRaises(ValueError):
                collect.extract(root / 'bad.tgz', 'tar.gz', root / 'out')
            self.assertFalse((root / 'escaped').exists())

    def test_hash_mismatch_never_publishes_download_or_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = {'id': 'fixture', 'source': {'sha256': '0' * 64, 'archive': 'zip',
                      'url': 'https://example.org/asset.zip'}, 'objects': []}
            with patch.object(collect.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong')):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    collect.collect_bundle(bundle, root)
            self.assertFalse((root / 'archives/fixture.zip').exists())
            self.assertFalse((root / 'archives/fixture.zip.partial').exists())
            self.assertFalse((root / 'sources/fixture').exists())

    def test_offline_archive_rebuilds_assets_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'archives').mkdir()
            archive = root / 'archives/fixture.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('retained.txt', 'checked source')
            bundle = {'id': 'fixture', 'source': {'sha256': collect.digest(archive),
                      'archive': 'zip'}, 'objects': []}
            with patch.object(collect.urllib.request, 'urlopen', side_effect=AssertionError('network')):
                collect.collect_bundle(bundle, root, offline=True)
                path = root / 'sources/fixture/retained.txt'
                path.write_text('corrupt output')
                collect.collect_bundle(bundle, root, offline=True)
                self.assertEqual(path.read_text(), 'checked source')
                archive.write_bytes(b'corrupt archive')
                with self.assertRaisesRegex(ValueError, 'offline'):
                    collect.collect_bundle(bundle, root, offline=True)
            provenance = json.loads((root / 'sources/fixture/provenance.json').read_text())
            self.assertEqual(provenance['files']['retained.txt'],
                             hashlib.sha256(b'checked source').hexdigest())

    def test_gso_texture_repair_preserves_original_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'meshes').mkdir()
            (root / 'materials/textures').mkdir(parents=True)
            (root / 'materials/textures/texture.png').write_bytes(b'fixture texture')
            obj = root / 'meshes/model.obj'
            obj.write_text('mtllib model.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            mtl = root / 'meshes/model.mtl'
            mtl.write_text('newmtl material_0\nmap_Kd texture.png\n')
            original = mtl.read_bytes()
            with self.assertRaisesRegex(ValueError, 'texture'):
                collect.obj_audit(obj, root)
            row = {'visual_mesh': 'meshes/model.obj',
                   'texture_remap': {'texture.png': 'materials/textures/texture.png'}}
            visual, repairs = collect.normalize_visual(row, root)
            audit = collect.obj_audit(root / visual, root)
            self.assertEqual(mtl.read_bytes(), original)
            self.assertEqual(audit['textures'], ['materials/textures/texture.png'])
            self.assertEqual(len(repairs), 1)

    def test_missing_material_and_nonfinite_geometry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            obj = root / 'mesh.obj'
            obj.write_text('mtllib missing.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            with self.assertRaisesRegex(ValueError, 'material'):
                collect.obj_audit(obj, root)
            obj.write_text('mtllib missing.mtl\nv nan 0 0\nf 1 2 3\n')
            with self.assertRaisesRegex(ValueError, 'vertex'):
                collect.obj_audit(obj, root)


if __name__ == '__main__':
    unittest.main()
