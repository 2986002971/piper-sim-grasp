"""Inspect installed inputs without starting simulation or changing packages."""
import json
from pathlib import Path
import platform
import subprocess
from runtime.config import paths


def doctor(config):
    p = paths(config)
    issues = []
    if platform.machine() != 'x86_64': issues.append('Server architecture must be x86_64')
    required = [Path(config['isaac_env'])/'bin/python', Path('/opt/ros/humble/setup.bash'),
                p['install']/'devices/local_setup.bash']
    if config.get('main_project'):
        required += [p['install']/'main/local_setup.bash', p['install']/'servo/local_setup.bash',
                     p['install']/'integration/local_setup.bash']
    if config.get('streaming', {}).get('enabled'):
        required += [p['sdk']/'nvenc/libnvenc_fix.so', p['sdk']/'turn/bin/turnserver',
                     Path('/usr/sbin/haproxy'), Path(config['streaming']['credentials_file'])]
    issues += ['Missing '+str(path) for path in required if not path.is_file()]
    versions = None
    python = Path(config['isaac_env'])/'bin/python'
    if python.is_file():
        try:
            output = subprocess.check_output([str(python), '-I', '-c',
                'import importlib.metadata,json,sys;print(json.dumps({'
                '"isaac":importlib.metadata.version("isaacsim"),'
                '"python":".".join(map(str,sys.version_info[:2]))}))'],
                text=True, stderr=subprocess.PIPE, timeout=15)
            versions = json.loads(output)
            from runtime.config import ROOT
            expected = json.loads((ROOT/'config/versions.lock.json').read_text())
            if versions != {'isaac':expected['isaac'],'python':expected['isaac_python']}:
                issues.append('Isaac/Python versions differ from config/versions.lock.json')
        except (OSError, ValueError, subprocess.SubprocessError):
            issues.append('Isaac environment is incomplete; rerun deploy')
    if config.get('main_project'):
        try:
            from deploy.main_project import inspect, build_digest
            inputs = inspect(config['main_project'])
            built = json.loads((p['reports']/'main-build-inputs.json').read_text())
            if build_digest(inputs['files']) != build_digest(built['files']):
                issues.append('Main integration inputs changed since build; rebuild before starting an experiment')
        except (OSError, ValueError, KeyError) as error:
            issues.append('Main project: '+str(error))
    try:
        from runtime.scene import load_profile, validate_prepared
        profile = load_profile(config)
        validate_prepared(config, profile)
    except (OSError, ValueError, KeyError, RuntimeError) as error: issues.append('Scene: '+str(error))
    try:
        gpu = subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        gpu = None; issues.append('nvidia-smi failed')
    return {'passed': not issues, 'issues': issues, 'gpu': gpu, 'isaac_environment': versions, 'data_root': config['data_root'],
            'main_project': config.get('main_project'), 'profile': config['profile'],
            'streaming': config.get('streaming', {}).get('enabled', False)}
