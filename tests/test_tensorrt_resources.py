import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy import tensorrt_resources as resources
from deploy.engine import build_engine


class TensorRTResourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('dpkg-deb'), 'Debian package tools unavailable')
    def test_real_debian_package_acquisition_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root/'package'
            control = package/'DEBIAN'; control.mkdir(parents=True)
            control.chmod(0o755)  # dpkg requires this even under the CLI's private umask.
            (control/'control').write_text('Package: libnvinfer10\nVersion: '+resources.VERSION+
                '\nArchitecture: amd64\nMaintainer: Test <test@example.invalid>\nDescription: Test resource package\n')
            library = package/'usr/lib/x86_64-linux-gnu'; library.mkdir(parents=True)
            (library/resources.RESOURCE).write_bytes(b'linux builder')
            (library/'libnvinfer_builder_resource_win.so.10.9.0').write_bytes(b'windows builder')
            archive = root/'package.deb'
            subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(archive)],
                           check=True, stdout=subprocess.DEVNULL)
            def fetch(package, target): shutil.copy2(archive, target)
            with patch.object(resources, 'download', side_effect=fetch):
                with resources.builder_library(root/'sdk', root/'cache') as directory:
                    self.assertEqual([p.name for p in directory.iterdir()], [resources.RESOURCE])
                    self.assertEqual((directory/resources.RESOURCE).read_bytes(), b'linux builder')
            self.assertEqual(list((root/'cache').iterdir()), [])

    def test_legacy_builder_is_temporary_during_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); sdk = root/'sdk'
            legacy = sdk/'usr/lib/x86_64-linux-gnu'/resources.RESOURCE
            legacy.parent.mkdir(parents=True); legacy.write_bytes(b'builder')
            with patch.object(resources, 'download') as download:
                with resources.builder_library(sdk, root/'cache') as directory:
                    self.assertFalse(legacy.exists())
                    self.assertEqual((directory/resources.RESOURCE).read_bytes(), b'builder')
                download.assert_not_called()
            self.assertFalse(legacy.exists())
            self.assertEqual(list((root/'cache').iterdir()), [])

    def test_runtime_pruning_keeps_inference_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)/'usr/lib/x86_64-linux-gnu'
            library.mkdir(parents=True)
            for name in (resources.RESOURCE, 'libnvinfer_builder_resource_win.so.10.9.0', 'libnvinfer.so.10.9.0'):
                (library/name).write_bytes(b'library')
            resources.prune_builder_resources(temp)
            resources.prune_builder_resources(temp)
            self.assertEqual([p.name for p in library.iterdir()], ['libnvinfer.so.10.9.0'])

    def test_acquired_builder_is_cleaned_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def extract(archive, destination):
                destination.write_bytes(b'linux builder')
            def download(package, archive): archive.write_bytes(b'deb')
            with patch.object(resources, 'download', side_effect=download), \
                 patch.object(resources, 'extract_builder', side_effect=extract):
                with self.assertRaisesRegex(RuntimeError, 'build failed'):
                    with resources.builder_library(root/'sdk', root/'cache') as directory:
                        self.assertTrue((directory/resources.RESOURCE).is_file())
                        self.assertFalse((directory/'libnvinfer.deb').exists())
                        raise RuntimeError('build failed')
            self.assertEqual(list((root/'cache').iterdir()), [])
            self.assertFalse((root/'sdk').exists())

    def test_package_extraction_only_writes_linux_builder(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name in (resources.RESOURCE, 'libnvinfer_builder_resource_win.so.10.9.0', 'libnvinfer.so.10.9.0'):
                member = tarfile.TarInfo('./usr/lib/x86_64-linux-gnu/'+name)
                data = name.encode(); member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        stream.seek(0)
        from unittest.mock import MagicMock
        process = MagicMock(); process.stdout = stream
        process.wait.return_value = 0; process.poll.return_value = 0
        process.__enter__.return_value = process
        with tempfile.TemporaryDirectory() as temp, patch.object(resources.subprocess, 'Popen', return_value=process):
            target = Path(temp)/resources.RESOURCE
            resources.extract_builder('package.deb', target)
            self.assertEqual(target.read_bytes(), resources.RESOURCE.encode())
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_valid_engine_reuse_never_acquires_builder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'main'
            generator = source/'src/perception/nankai_robocup_perception_bringup/tools/generate_detection_manifest.py'
            generator.parent.mkdir(parents=True); generator.write_text('manifest generator')
            detector = {'license_reference': 'reviewed', 'exporter_version': '1'}
            for name in ('onnx', 'source_pt', 'classes'):
                path = root/name; path.write_text(name); detector[name] = str(path)
            config = {'main_project': str(source), 'detector': detector, 'data_root': str(root/'data')}
            class Builder:
                def __enter__(self): return root/'temporary-builder'
                def __exit__(self, *args): pass
            def run(command, log, env=None):
                for argument in map(str, command):
                    if argument.startswith('--saveEngine='):
                        Path(argument.split('=', 1)[1]).write_bytes(b'engine')
                if '--output' in command:
                    Path(command[command.index('--output')+1]).write_text('manifest')
            with patch('deploy.engine.subprocess.check_output', return_value='RTX 4090, 8.9\n'), \
                 patch('deploy.pipeline.run', side_effect=run), \
                 patch('deploy.engine.builder_library', return_value=Builder()) as acquire:
                first = build_engine(config, root/'log')
                self.assertEqual(acquire.call_count, 1)
                acquire.reset_mock()
                self.assertEqual(build_engine(config, root/'log'), first)
                acquire.assert_not_called()
                (first/'model.engine').write_bytes(b'corrupt')
                with self.assertRaisesRegex(ValueError, 'corrupt'):
                    build_engine(config, root/'log')
                acquire.assert_not_called()


if __name__ == '__main__': unittest.main()
