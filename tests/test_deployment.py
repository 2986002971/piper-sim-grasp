import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

from models.bundle import verify
from deploy.main_project import PACKAGES, TOOLS, RUNTIME_CONFIGS, build_digest, inspect


class DeploymentTests(unittest.TestCase):
    def test_failed_or_interrupted_model_build_preserves_active_model(self):
        from deploy.pipeline import build_model
        for failure in ('generator', 'changed_inputs', 'interrupted'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                models = root/'models'
                active = models/'previous'
                active.mkdir(parents=True)
                (active/'robot.urdf').write_text('previous model')
                (models/'7up').symlink_to(active.name)
                config = {'data_root': str(root), 'profile': '7up',
                          'main_project': str(root/'main'), 'isaac_env': '/isaac'}
                profile = {'scene_usd': '/scene.usda', 'assembly_urdf': '/assembly.urdf'}
                def generate(command, log):
                    if '--model-output' in command:
                        output = Path(command[command.index('--model-output')+1])
                        output.mkdir()
                        (output/'robot.urdf').write_text('partial model')
                        if failure == 'generator': raise RuntimeError('generator failed')
                        if failure == 'interrupted': raise KeyboardInterrupt()
                error = KeyboardInterrupt if failure == 'interrupted' else RuntimeError
                with patch('deploy.pipeline.run', side_effect=generate), \
                        patch('runtime.scene.model_inputs', return_value={}), \
                        patch('deploy.pipeline.inspect', return_value={'changed': True}), \
                        self.assertRaises(error):
                    build_model(config, profile, root/'engine', {}, root/'build.log')
                self.assertEqual((models/'7up').resolve(), active)
                self.assertEqual((active/'robot.urdf').read_text(), 'previous model')
                self.assertEqual(sorted(p.name for p in models.iterdir()), ['7up', 'previous'])

    def test_missing_session_templates_are_reported_before_build_or_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            required = [source/package/'package.xml' for package in PACKAGES]
            required += [source/tool for tool in TOOLS]
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            with self.assertRaisesRegex(ValueError, 'config/alignment.yaml'):
                inspect(source)
            for name in RUNTIME_CONFIGS:
                path = source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
            self.assertTrue(RUNTIME_CONFIGS.issubset(inspect(source)['files']))

    def test_runtime_config_edits_do_not_require_rebuild_but_code_edits_do(self):
        original = {name: 'old' for name in RUNTIME_CONFIGS}
        original['src/manipulation/nankai_robocup_arm_control/src/motion.cpp'] = 'code'
        original['src/manipulation/nankai_robocup_arm_control/config/robot.urdf'] = 'installed-config'
        runtime_edits = dict(original, **{name: 'new' for name in RUNTIME_CONFIGS})
        self.assertEqual(build_digest(original), build_digest(runtime_edits))
        for name in ('src/manipulation/nankai_robocup_arm_control/src/motion.cpp',
                     'src/manipulation/nankai_robocup_arm_control/config/robot.urdf'):
            changed = dict(runtime_edits); changed[name] = 'changed'
            self.assertNotEqual(build_digest(original), build_digest(changed))

    def test_package_paths_cli_preserves_spaces(self):
        with tempfile.TemporaryDirectory(prefix='main source ') as tmp:
            output = subprocess.check_output([sys.executable,
                str(Path(__file__).resolve().parents[1]/'deploy/main_project.py'), tmp], text=True)
            self.assertEqual(output.splitlines(), [str(Path(tmp)/name) for name in PACKAGES])

    def test_export_manifest_detects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {'scene.usda': b'#usda 1.0', 'robot.urdf': b'<robot/>',
                     'assembly.urdf': b'<robot/>', 'scene.json': b'{}'}
            manifest = dict(schema_version=1, scene='scene.usda', robot_urdf='robot.urdf',
                            assembly_urdf='assembly.urdf', scene_metadata='scene.json',
                            files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
            for name,value in files.items(): (root/name).write_bytes(value)
            (root/'bundle.json').write_text(json.dumps(manifest))
            verify(root)
            (root/'scene.usda').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'corrupt'): verify(root)

    def test_export_manifest_rejects_outside_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bundle';root.mkdir()
            outside=Path(tmp)/'outside';outside.write_bytes(b'external')
            (root/'bundle.json').write_text(json.dumps({'schema_version':1,
                'files':{'../outside':hashlib.sha256(b'external').hexdigest()}}))
            with self.assertRaisesRegex(ValueError,'outside'): verify(root)



if __name__=='__main__':unittest.main()
