"""Machine configuration shared by deployment and management (stdlib only)."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(filename):
    if filename == '@environment':
        value = json.loads(os.environ['PIPER_MACHINE_CONFIG_JSON'])
    else:
        value = json.loads(Path(filename).expanduser().read_text())
    if value.get('schema_version') != 1:
        raise ValueError('Expected machine schema_version=1; see config/machine.example.json')
    for key in ('data_root', 'repo_path'):
        path = Path(value[key]).expanduser()
        if not path.is_absolute() or path == Path('/'):
            raise ValueError(f'{key} must be an absolute non-root path')
        value[key] = str(path)
    data = Path(value['data_root'])
    if data == Path(value['repo_path']) or data.is_relative_to(Path(value['repo_path'])):
        raise ValueError('data_root must be outside the checkout')
    value.setdefault('isaac_env', str(data/'envs/isaac'))
    if not isinstance(value.get('profile'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value['profile']):
        raise ValueError('Set profile explicitly to a name from config/profiles (letters, digits, _ or -)')
    value.setdefault('watchdog_seconds', 4500)
    if value['watchdog_seconds'] < 0:
        raise ValueError('watchdog_seconds must be >= 0')
    value.setdefault('ros_domain_id', 0)
    if not 0 <= value['ros_domain_id'] <= 232:
        raise ValueError('ros_domain_id must be in 0..232')
    if value.get('main_project') and not Path(value['main_project']).is_absolute():
        raise ValueError('main_project is an absolute path on the simulation server')
    for key in ('isaac_env', 'vulkan_icd'):
        if value.get(key) and not Path(value[key]).is_absolute():
            raise ValueError(key+' must be an absolute server path')
    return value


def paths(config):
    data = Path(config['data_root'])
    return {name: data/name for name in ('envs', 'sdk', 'assets', 'config', 'build',
                                        'install', 'logs', 'state', 'reports', 'cache')}


def write_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with temporary.open('w') as handle:
        if private:
            os.chmod(temporary, 0o600)
        json.dump(value, handle, indent=2)
        handle.write('\n')
    temporary.replace(path)


def ros_command(config, argv, main=False):
    wrapper = ROOT/'runtime/ros-env.sh'
    return ['bash', str(wrapper), config['data_root'], 'main' if main else 'devices', *map(str, argv)]


def isaac_command(config, argv):
    return ['bash', str(ROOT/'runtime/isaac/python.sh'), config['isaac_env'], *map(str, argv)]
