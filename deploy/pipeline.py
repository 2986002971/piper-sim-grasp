"""Explicit deployment stages. Every reuse is checked against actual inputs."""
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import tempfile
import time

from runtime.config import ROOT, paths, write_json, isaac_command
from deploy.main_project import inspect


def run(command, log, env=None):
    log = Path(log); log.parent.mkdir(parents=True, exist_ok=True)
    print('Running '+str(command[0])+': '+str(log), flush=True)
    with log.open('a') as handle:
        environment = dict(os.environ if env is None else env, PYTHONDONTWRITEBYTECODE='1')
        subprocess.run(list(map(str, command)), stdout=handle, stderr=subprocess.STDOUT, env=environment, check=True)


def deploy(config):
    if os.geteuid() != 0: raise ValueError('Deploy requires root on the AutoDL server')
    if platform.machine() != 'x86_64': raise ValueError('Deployment requires x86_64')
    release = dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID','').strip('"') != 'ubuntu' or release.get('VERSION_ID','').strip('"') != '22.04':
        raise ValueError('Deployment requires Ubuntu 22.04')
    subprocess.run(['nvidia-smi'], check=True)
    p = paths(config)
    for directory in p.values(): directory.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT/'config/versions.lock.json').read_text())
    log = p['logs']/'deploy.log'
    run(['apt-get','update'], log)
    run(['apt-get','install','-y','--no-remove','--no-install-recommends',
         'build-essential','cmake','ccache','curl','ca-certificates','python3-yaml','python3-numpy',
         'python3-scipy','python3-opencv','python3-pytest','ffmpeg','vulkan-tools','libvulkan1','libsm6','libegl1','libxt6','iproute2'], log)
    run(['bash',ROOT/'deploy/install-ros2-humble.sh',p['logs']/'ros-install'],log)
    run(['apt-get','install','-y','--no-remove','ros-humble-tf2-ros','ros-humble-robot-state-publisher',
         'ros-humble-sensor-msgs','ros-humble-launch-ros'],log)
    isaac = Path(config['isaac_env'])
    if not (isaac/'bin/python').exists():
        # AutoDL's noninteractive SSH PATH omits the supplied Conda installation.
        python = shutil.which('python'+lock['isaac_python'])
        autodl_python = Path('/root/miniconda3/bin/python'+lock['isaac_python'])
        if not python and autodl_python.is_file(): python = str(autodl_python)
        if python:
            run([python,'-m','venv',isaac],log)
        elif shutil.which('conda'):
            run(['conda','create','-y','-p',isaac,'python='+lock['isaac_python'],'pip'],log)
        else: raise RuntimeError('Provide Conda or Python 3.12 to create the isolated Isaac environment')
    # The installed distribution is about 30 GB. Avoid retaining a second copy
    # of its wheels alongside temporary downloads on AutoDL's 50 GB data disk.
    with tempfile.TemporaryDirectory(prefix='pip-install-', dir=p['cache']) as temporary:
        env = dict(os.environ, TMPDIR=temporary)
        run([isaac/'bin/python','-m','pip','install','--no-cache-dir',
             f"isaacsim[all,extscache]=={lock['isaac']}",
             '--index-url',config.get('pypi_index_url','https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple'),
             '--extra-index-url','https://pypi.nvidia.com','-r',ROOT/'models/requirements.txt'],log,env)
    vulkan_env = dict(os.environ)
    if config.get('vulkan_icd'): vulkan_env['VK_ICD_FILENAMES'] = config['vulkan_icd']
    run(['vulkaninfo','--summary'],p['logs']/'vulkan.log',vulkan_env)
    if config.get('streaming',{}).get('enabled'):
        run(['apt-get','install','-y','--no-remove','haproxy','libevent-dev','libsqlite3-dev','libssl-dev','pkg-config'],log)
        turn = p['sdk']/'turn/bin/turnserver'
        valid = turn.is_file() and lock['coturn'] in subprocess.check_output([str(turn),'--version'], text=True)
        if not valid: run(['bash',ROOT/'deploy/install-turn.sh',p['sdk']/'turn'],log)
        (p['sdk']/'nvenc').mkdir(exist_ok=True)
        run(['gcc','-shared','-fPIC','-Wall','-Wextra','-O2','-o',p['sdk']/'nvenc/libnvenc_fix.so',
             ROOT/'runtime/streaming/nvenc_ioctl_filter.c','-ldl'],log)
        credentials = Path(config['streaming']['credentials_file'])
        if not credentials.exists():
            write_json(credentials, {'username': secrets.token_hex(16), 'password': secrets.token_hex(32)}, private=True)
    if config.get('main_project'):
        source = Path(config['main_project']); inspect(source)
        nvcc = Path('/usr/local/cuda-'+lock['cuda'])/'bin/nvcc'
        if not nvcc.exists():
            run(['bash',ROOT/'deploy/install-cuda.sh',p['cache']],log)
        run([nvcc,'--version'],log)
        run(['python3',ROOT/'deploy/install-tensorrt.py','--prefix',p['sdk']/'tensorrt'],log)
        run(['bash',ROOT/'runtime/ros2-env.sh','bash',ROOT/'deploy/install-grasp-deps.sh',source,config['data_root']],log)
    installed = subprocess.check_output(['dpkg-query','-W'], text=True)
    (p['reports']/'installed-packages.tsv').write_text(installed)
    python_packages = subprocess.check_output([isaac/'bin/python','-m','pip','list','--format=json'], text=True)
    write_json(p['reports']/'isaac-packages.json', json.loads(python_packages))
    write_json(p['reports']/'deployment.json', {'completed': True, 'versions': lock, 'time': time.time()})
    print('Deployment completed. Prepare the source USD and URDF paths in your profile, then run piper build.', flush=True)


def build_model(config, profile, engine, main_inputs, log):
    """Publish a validated model; a failed build leaves the active model intact."""
    from runtime.scene import model_directory, model_inputs
    source = Path(config['main_project'])
    model_dir = model_directory(config)
    if model_dir.exists() and not model_dir.is_symlink():
        raise RuntimeError('Refusing to replace unmanaged model directory: '+str(model_dir))
    staging = model_dir.parent/('generation-'+str(time.time_ns()))
    pending = model_dir.with_name('.'+staging.name+'-pending')
    inputs = model_inputs(profile)
    published = False
    try:
        run(['bash',ROOT/'runtime/ros2-env.sh','python3',ROOT/'deploy/prepare-grasp-model.py',
             '--main-source',source,'--model-output',staging,'--assembly-urdf',profile['assembly_urdf']],log)
        scene_args = [ROOT/'models/prepare_control_scene.py', '--scene', profile['scene_usd'],
                      '--source-urdf', profile['assembly_urdf'],
                      '--urdf', staging/'assembly.urdf', '--output', staging/'scene.usda']
        if profile.get('bottle_physics'):
            scene_args += ['--bottle-physics', json.dumps(profile['bottle_physics'])]
        run(isaac_command(config, scene_args), log)
        if inputs != model_inputs(profile):
            raise RuntimeError('Robot model inputs changed during build; rerun against stable inputs')
        if main_inputs != inspect(source):
            raise RuntimeError('Main project changed during build; rerun against stable inputs')
        write_json(staging/'build.json', {'model_inputs': inputs, 'main_project': str(source.resolve()),
                                        'engine_directory': str(engine.resolve())})
        pending.symlink_to(staging.name)
        pending.replace(model_dir)
        published = True
    finally:
        pending.unlink(missing_ok=True)
        if not published: shutil.rmtree(staging, ignore_errors=True)


def build(config):
    from runtime.scene import load_profile
    profile = load_profile(config)
    p = paths(config)
    if not Path('/opt/ros/humble/setup.bash').is_file(): raise RuntimeError('Run piper deploy first')
    log = p['logs']/'build.log'
    base = ['bash', ROOT/'runtime/ros2-env.sh']
    if config.get('main_project'):
        source = Path(config['main_project']); before = inspect(source)
        run(base+['bash',ROOT/'deploy/build-grasp.sh',source,config['data_root']],log)
        from deploy.engine import build_engine
        engine = build_engine(config, log)
        build_model(config, profile, engine, before, log)
        write_json(p['reports']/'main-build-inputs.json', before)
    else:
        run(base+['colcon','--log-base',p['logs']/'devices-build','build','--base-paths',ROOT/'ros2/piper_sim_devices',
                  '--build-base',p['build']/'devices','--install-base',p['install']/'devices','--symlink-install',
                  '--cmake-args','-DNANKAI_MAIN_SOURCE='],log)
    print(f"Build completed for profile {config['profile']}; start/check will prepare fresh runtime configuration.", flush=True)
