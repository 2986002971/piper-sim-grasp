import hashlib
import io
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'models'))
import prepare


class ModelTests(unittest.TestCase):
    def test_importer_zero_exit_without_usd_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            handeye=data/'handeye.yaml';handeye.write_text('fixture')
            args=Namespace(scene=None,offline=True,bracket=None,bracket_cookie_file=None,
                           download_only=False,urdf_only=False,camera_placement="mechanical",handeye=handeye,isaac_python=Path(sys.executable))
            def assembly(source,dest,*unused):
                urdf=dest/'piper_x_d405.urdf';urdf.write_text('<robot/>');return urdf
            with patch.object(prepare,'fetch',return_value=data),patch.object(prepare,'fetch_bracket',return_value=None),\
                 patch.object(prepare,'assembly',side_effect=assembly),patch.object(prepare.subprocess,'run'):
                with self.assertRaisesRegex(ValueError,'required output'):
                    prepare.prepare(args,data)
            self.assertFalse((data/'generated').exists())

    def test_first_download_cache_offline_corruption_and_failure(self):
        payload=b'checked upstream fixture'
        lock={'commit':'fixed','files':[{'path':'model/input','sha256':hashlib.sha256(payload).hexdigest()}]}
        with tempfile.TemporaryDirectory() as temp,patch.object(prepare,'LOCK',lock):
            cache=Path(temp)
            with patch('urllib.request.urlopen',return_value=io.BytesIO(payload)) as download:
                source=prepare.fetch(cache);self.assertEqual(download.call_count,1)
            with patch('urllib.request.urlopen',side_effect=AssertionError('cache should avoid network')):
                self.assertEqual(prepare.fetch(cache),source);self.assertEqual(prepare.fetch(cache,True),source)
            (source/'model/input').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):prepare.fetch(cache,True)
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'wrong')):
                with self.assertRaises(ValueError):prepare.fetch(cache)
            self.assertFalse((source/'model/input.partial').exists())
            with patch('urllib.request.urlopen',side_effect=OSError('download failed')):
                with self.assertRaises(OSError):prepare.fetch(cache)

    def test_transform_round_trip(self):
        expected=prepare.transform([-.01,.02,.03],[.04,-1.1,1.5])
        xyz,rpy=prepare.pose(expected);actual=prepare.transform(xyz,rpy)
        for i in range(4):
            for j in range(4):self.assertAlmostEqual(actual[i][j],expected[i][j])
        identity=prepare.multiply(expected,prepare.inverse(expected))
        for i in range(4):
            for j in range(4):self.assertAlmostEqual(identity[i][j],int(i==j))


if __name__=='__main__':unittest.main()
