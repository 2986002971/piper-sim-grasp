import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from runtime import scene, session
from runtime.config import ROOT, load


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root/'repo'
        self.data = self.root/'data'
        self.profiles = self.repo/'config/profiles'
        self.profiles.mkdir(parents=True)
        self.patch = patch('runtime.scene.ROOT', self.repo)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = {'profile': 'first', 'data_root': str(self.data), 'repo_path': str(self.repo),
                       'isaac_env': '/isaac', 'ros_domain_id': 93, 'watchdog_seconds': 0}
        self.profile = yaml.safe_load((ROOT/'config/profiles/7up.yaml').read_text())
        usd = self.root/'source.usda'
        usd.write_text('#usda 1.0')
        mesh = self.root/'mesh.stl'
        mesh.write_text('mesh')
        urdf = self.root/'robot.urdf'
        urdf.write_text('<robot name="test"><link name="base_link"><visual><geometry>'
                        '<mesh filename="mesh.stl"/></geometry></visual></link>'+''.join(
                            f'<joint name="joint{i}"><limit lower="-3.14" upper="3.14"/></joint>'
                            for i in range(1,7))+'</robot>')
        assembly = self.root/'assembly.urdf'
        assembly.write_text('<robot name="assembly"/>')
        self.profile.update(scene_usd=str(usd), robot_urdf=str(urdf), assembly_urdf=str(assembly))
        self.write_profile()
        self.install('devices')

    def install(self, part):
        path = self.data/'install'/part/'local_setup.bash'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def write_profile(self, name='first', profile=None):
        (self.profiles/f'{name}.yaml').write_text(yaml.safe_dump(profile or self.profile))

    def setup_main(self):
        source = self.root/'main'
        self.config['main_project'] = str(source)
        geometry = source/'src/manipulation/nankai_robocup_grasp_geometry/config'
        geometry.mkdir(parents=True)
        (geometry/'alignment.example.yaml').write_text(yaml.safe_dump({'source': {}, 'm': {}, 'w': {}}))
        (geometry/'alignment.yaml').write_text(yaml.safe_dump({
            'source': {'geometry_T_tcp': [0., 0., 0., 0., 0., 1., 0.]},
            'm': {'preview_only': False}, 'feedback': {'backend': 'socketcan'}}))
        (geometry/'d405_yolo.yaml').write_text(yaml.safe_dump({'object_detector': {'ros__parameters': {}}}))
        for part in ('main', 'servo', 'integration'): self.install(part)
        model = scene.model_directory(self.config)
        model.mkdir(parents=True)
        for name in ('robot.urdf', 'robot.srdf', 'joint_limits.yaml'): (model/name).touch()
        engine = self.root/'engine'
        engine.mkdir()
        for name in ('model.engine', 'model.manifest.yaml', 'classes.txt'): (engine/name).touch()
        (model/'build.json').write_text(json.dumps({'model_inputs': scene.model_inputs(self.profile),
            'main_project': str(source), 'engine_directory': str(engine)}))

    def test_machine_requires_explicit_safe_profile_without_local_scene_files(self):
        for name in ('first', 'another-scene_2', None, '../other', '/absolute'):
            with self.subTest(name=name):
                config = dict(self.config)
                config['schema_version'] = 1
                if name is None: config.pop('profile')
                else: config['profile'] = name
                file = self.root/'machine.json'
                file.write_text(json.dumps(config))
                if name in ('first', 'another-scene_2'): self.assertEqual(load(file)['profile'], name)
                else:
                    with self.assertRaisesRegex(ValueError, 'profile'): load(file)

    def test_prepare_uses_live_source_and_snapshot_without_registry(self):
        first = scene.prepare(self.config, self.root/'session1/config')
        original = yaml.safe_load(first['devices'].read_text())
        self.assertEqual(original['scene_usd'], self.profile['scene_usd'])
        self.assertNotIn('scene_boxes', original)
        robot = (first['devices'].parent/'robot.urdf').read_text()
        self.assertIn((self.root/'mesh.stl').as_uri(), robot)
        self.profile['initial_joint_positions'][-1] = 0
        self.write_profile()
        second = scene.prepare(self.config, self.root/'session2/config')
        self.assertEqual(yaml.safe_load(second['devices'].read_text())['initial_joint_positions'][-1], 0)
        self.assertEqual(yaml.safe_load(first['devices'].read_text())['initial_joint_positions'][-1], -.7)
        self.assertFalse((self.data/'assets').exists())

    def test_profile_switch_selects_distinct_usd(self):
        other = copy.deepcopy(self.profile)
        usd = self.root/'other.usda';usd.write_text('#usda 1.0')
        other['scene_usd'] = str(usd)
        self.write_profile('second', other)
        first = scene.prepare(self.config, self.root/'one')
        second = scene.prepare(dict(self.config, profile='second'), self.root/'two')
        self.assertNotEqual(first['profile']['scene_usd'], second['profile']['scene_usd'])

    def test_main_configuration_refreshes_boxes_and_priors_without_build(self):
        self.setup_main()
        with patch('deploy.pipeline.build', side_effect=AssertionError('unexpected build')), \
                patch('deploy.engine.build_engine', side_effect=AssertionError('unexpected engine build')):
            prepared = scene.prepare(self.config, self.root/'run')
        alignment = yaml.safe_load(prepared['alignment'].read_text())
        self.assertEqual(alignment['source']['bottle_height'], .235)
        self.assertEqual(alignment['w']['urdf_file'], str(scene.model_directory(self.config)/'robot.urdf'))
        self.assertEqual(yaml.safe_load((self.root/'run/scene.yaml').read_text())['boxes'], self.profile['scene_boxes'])
        detector = yaml.safe_load(prepared['detector'].read_text())['/grasp_detection/object_detector']['ros__parameters']
        self.assertEqual(detector['streams.wrist.input_image_topic'], self.profile['rgb_topic'])
        self.profile['bottle_height_m'] = .25
        self.profile['scene_boxes'][0]['position'][0] = .3
        self.write_profile()
        refreshed = scene.prepare(self.config, self.root/'run2')
        self.assertEqual(yaml.safe_load(refreshed['alignment'].read_text())['source']['bottle_height'], .25)
        self.assertEqual(yaml.safe_load((self.root/'run2/scene.yaml').read_text())['boxes'][0]['position'][0], .3)

    def test_tcp_convention_comes_from_tracked_experiment_without_hardware_settings(self):
        self.setup_main()
        first = scene.prepare(self.config, self.root/'first-run')
        original = yaml.safe_load(first['alignment'].read_text())
        self.assertEqual(original['source']['geometry_T_tcp'], [0., 0., 0., 0., 0., 1., 0.])
        self.assertNotIn('feedback', original)
        self.assertNotIn('preview_only', original['m'])
        config_file = Path(self.config['main_project'])/'src/manipulation/nankai_robocup_grasp_geometry/config/alignment.yaml'
        config_file.write_text(yaml.safe_dump({'source': {
            'geometry_T_tcp': [0., 0., .01, 0., 0., 0., 1.]}}))
        second = scene.prepare(self.config, self.root/'second-run')
        self.assertEqual(yaml.safe_load(second['alignment'].read_text())['source']['geometry_T_tcp'],
                         [0., 0., .01, 0., 0., 0., 1.])
        self.assertEqual(yaml.safe_load(first['alignment'].read_text()), original)

    def test_model_inputs_change_requires_build_and_profiles_are_isolated(self):
        self.setup_main()
        self.write_profile('second')
        with self.assertRaisesRegex(RuntimeError, 'build for profile second'):
            scene.prepare(dict(self.config, profile='second'), self.root/'missing-model')
        Path(self.profile['assembly_urdf']).write_text('<robot name="changed"/>')
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            scene.prepare(self.config, self.root/'changed-model')

    def test_invalid_scene_never_falls_back_to_previous_configuration(self):
        old = self.data/'config/devices.yaml';old.parent.mkdir(parents=True);old.write_text('old scene')
        Path(self.profile['scene_usd']).unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'scene_usd'):
            scene.prepare(self.config, self.root/'bad-run')
        self.assertFalse((self.root/'bad-run').exists())
        self.assertEqual(old.read_text(), 'old scene')

    def test_invalid_initial_positions_and_collision_box(self):
        for field, value in [('initial_joint_positions', [0]*5),
                             ('initial_joint_positions', [0]*5+[4]),
                             ('camera_hz', float('nan')),
                             ('scene_boxes', [{'id': 'bad', 'size': [-1,1,1], 'position': [0,0,0],
                                               'quaternion': [0,0,0,1]}])]:
            with self.subTest(field=field, value=value):
                profile = copy.deepcopy(self.profile);profile[field] = value;self.write_profile(profile=profile)
                with self.assertRaises(ValueError): scene.load_profile(self.config)

    def test_normal_start_prepares_before_spawning_and_never_builds(self):
        event = Mock();event.wait.return_value = True
        child = Mock();child.poll.return_value = None
        with patch('runtime.session.threading.Event', return_value=event), \
                patch('runtime.session.signal.signal'), patch('runtime.session.subprocess.Popen', return_value=child) as spawn, \
                patch('runtime.session.terminate'), patch('deploy.pipeline.build', side_effect=AssertionError('build')):
            session.run(self.config)
        state = session.status(self.config)
        self.assertEqual(state['profile'], 'first')
        self.assertEqual(state['scene_usd'], self.profile['scene_usd'])
        snapshot = Path(state['configuration_directory'])/'devices.yaml'
        self.assertTrue(snapshot.is_file())
        commands = [c.args[0] for c in spawn.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn(str(snapshot), commands[0])
        self.assertFalse(state['manager_active'])


if __name__ == '__main__': unittest.main()
