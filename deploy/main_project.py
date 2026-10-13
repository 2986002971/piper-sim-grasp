"""The Nankai integration boundary: inspect external inputs, never checkout/edit them."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PACKAGES = (
    'src/nankai_robocup_interfaces', 'src/perception/nankai_robocup_perception_interfaces',
    'src/perception/nankai_robocup_perception_core', 'src/perception/nankai_robocup_object_perception',
    'src/nankai_robocup_visp_pbvs', 'src/w_moveit_servo', 'src/piper/piper_x_driver_cpp',
    'src/manipulation/nankai_robocup_grasp_geometry')
SERVO = Path('src/third_party/moveit_servo_reference')
TOOLS = ('src/piper/pyagxarm/checks/prepare_w_model.py',
         'src/perception/nankai_robocup_perception_bringup/tools/generate_detection_manifest.py',
         'scripts/check_visp.py')


def inspect(source):
    source = Path(source).resolve()
    required = [source/p/'package.xml' for p in PACKAGES]+[source/p for p in TOOLS]+[source/SERVO/'build_overlay.py']
    missing = [str(p.relative_to(source)) for p in required if not p.is_file()]
    if missing: raise ValueError('Main project lacks required integration inputs: '+', '.join(missing))
    inputs = {}
    for relative in (*PACKAGES, str(SERVO), 'src/piper/pyagxarm', 'scripts/visp_probe', *TOOLS):
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


def stage_servo(source, data):
    # Preserve the upstream script's expected relative layout without allowing
    # its REPO-derived output paths to write into the supplied checkout.
    root = Path(data)/'build/servo-source'
    target = root/SERVO
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(Path(source)/SERVO, target, ignore=shutil.ignore_patterns('__pycache__'))
    return root
