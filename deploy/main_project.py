"""The Nankai integration boundary: inspect external inputs, never checkout/edit them."""
import hashlib
import json
from pathlib import Path
import subprocess

PACKAGES = (
    'src/nankai_robocup_interfaces', 'src/perception/nankai_robocup_perception_interfaces',
    'src/perception/nankai_robocup_perception_core', 'src/perception/nankai_robocup_object_perception',
    'src/manipulation/nankai_robocup_arm_control',
    'src/manipulation/nankai_robocup_grasp_geometry',
    'src/manipulation/nankai_robocup_manipulation')
TOOLS = ('src/perception/nankai_robocup_perception_bringup/tools/generate_detection_manifest.py',)
# These files are read from source when preparing a session, not from install.
RUNTIME_CONFIGS = {
    'src/manipulation/nankai_robocup_manipulation/config/'+name
    for name in ('alignment.yaml', 'alignment.example.yaml', 'pick.yaml', 'd405_yolo.yaml')}


def build_digest(files):
    return hashlib.sha256(json.dumps({name: value for name, value in files.items()
        if name not in RUNTIME_CONFIGS}, sort_keys=True).encode()).hexdigest()


def inspect(source):
    source = Path(source).resolve()
    required = ([source/p/'package.xml' for p in PACKAGES]+[source/p for p in TOOLS]+
                [source/p for p in sorted(RUNTIME_CONFIGS)])
    missing = [str(p.relative_to(source)) for p in required if not p.is_file()]
    if missing: raise ValueError('Main project lacks required integration inputs: '+', '.join(missing))
    inputs = {}
    for relative in (*PACKAGES, 'src/piper/pyagxarm/integration/reference_piper_x', *TOOLS):
        root = source/relative
        for path in ([root] if root.is_file() else sorted(root.rglob('*'))):
            if path.is_file() and not any(part in ('__pycache__', '.pytest_cache', 'build', 'install', '.git') for part in path.relative_to(source).parts):
                inputs[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    git = {}
    for name, args in [('commit', ['rev-parse', 'HEAD']), ('status', ['status', '--porcelain'])]:
        proc = subprocess.run(['git', '-C', str(source), *args], text=True, capture_output=True)
        git[name] = proc.stdout.strip() if proc.returncode == 0 else None
    return {'directory': str(source), **git, 'files': inputs,
            'input_digest': hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()}


if __name__ == '__main__':
    import sys
    source = Path(sys.argv[1]).resolve()
    print('\n'.join(str(source/package) for package in PACKAGES))
