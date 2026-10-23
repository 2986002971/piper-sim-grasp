"""Server-side ownership of one Isaac/device session and an optional experiment."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time

from runtime.config import ROOT, paths, ros_command, write_json
from runtime.lifecycle import stamp
from runtime.streaming.turn_config import relay_ip, render


def live(record):
    return bool(record.get('start') and stamp(record.get('pid')) == record['start'])


def status(config):
    state = paths(config)['state']/'session.json'
    result = json.loads(state.read_text()) if state.exists() else {}
    result['manager_active'] = live(result)
    if not result['manager_active']:
        result.update(components_started=False, scene_ready=False, ros_devices_ready=False,
                      stream_initialized=False, experiment_running=False)
    return result


def stop(config):
    state = status(config)
    if state['manager_active']:
        os.kill(state['pid'], signal.SIGTERM)
        until = time.monotonic()+90
        while live(state) and time.monotonic() < until: time.sleep(.2)
        if live(state): raise TimeoutError('Session cleanup pending; inspect '+state['log_directory'])


def terminate(proc):
    try: os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError: return
    try: proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait()


def cancel_experiment(config, proc, env):
    if proc is None or proc.poll() is not None: return
    try:
        subprocess.run(ros_command(config, ['ros2','service','call','/sim_experiment/cancel',
                        'std_srvs/srv/Trigger','{}'], main=True), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        proc.wait(timeout=12)
    except (subprocess.SubprocessError, OSError):
        terminate(proc)


def stream_config(config, directory):
    stream = config['streaming']
    credentials = json.loads(Path(stream['credentials_file']).read_text())
    address = relay_ip(stream.get('relay_ip'))
    for kind, port in [(socket.SOCK_STREAM, p) for p in (6006, 6008, 16010)]+[(socket.SOCK_DGRAM, 47998)]:
        with socket.socket(socket.AF_INET, kind) as sock:
            try: sock.bind(('0.0.0.0', port))
            except OSError as error: raise RuntimeError(f'Streaming port {port} unavailable: {error}') from None
    turn = directory/'turn.conf'
    turn.write_text(render(dict(credentials, relay_ip=address, listen_ip=address, listen_port=16010)))
    ingress = directory/'haproxy.cfg'
    ingress.write_text('\n'.join([
        'global', '  maxconn 128', 'defaults', '  mode tcp', '  timeout connect 5s',
        '  timeout client 75m', '  timeout server 75m', 'frontend autodl_turn_tcp',
        '  bind 0.0.0.0:6008', '  default_backend coturn_tcp', 'backend coturn_tcp',
        f'  server coturn {address}:16010 source {address}', '']))
    viewer = {'turn': {'signalingServer': stream['host'], 'signalingPort': stream['signaling_port'],
        'mediaServer': address, 'mediaPort': 47998,
        'iceServerConfiguration': {'iceTransportPolicy': 'relay', 'iceServers': [{
            'urls': f"turn:{stream['host']}:{stream['turn_port']}?transport=tcp",
            'username': credentials['username'], 'credential': credentials['password']}]}}}
    return address, turn, ingress, viewer


def run(config, streaming=False):
    p = paths(config)
    for directory in (p['state'], p['logs']): directory.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    lock = (p['state']/'session.lock').open('a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise RuntimeError('A session or acceptance check is already active') from None
    directory = p['logs']/('session-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    directory.mkdir()
    children, handles = [], []
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: done.set())
    state = dict(pid=os.getpid(), start=stamp(os.getpid()), log_directory=str(directory),
                 streaming=streaming, scene_ready=False, ros_devices_ready=False,
                 stream_initialized=False, experiment_running=False, profile=config['profile'])
    def spawn(name, command, env=None):
        log = (directory/(name+'.log')).open('w'); handles.append(log)
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
        children.append(proc)
        return proc
    env = dict(os.environ, ROS_DOMAIN_ID=str(config['ros_domain_id']))
    if config.get('vulkan_icd'): env['VK_ICD_FILENAMES'] = config['vulkan_icd']
    readiness = p['state']/'devices-ready.json'
    readiness.unlink(missing_ok=True)
    runtime = p['state']/'viewer-runtime.json'
    started = time.monotonic()
    experiment_proc = None
    requests = p['state']/'experiment-request.json'
    requests.unlink(missing_ok=True)
    try:
        from runtime.scene import prepare
        configuration = directory/'config'
        prepared = prepare(config, configuration)
        devices = prepared['devices']
        profile = prepared['profile']
        state.update(scene_usd=prepared['scene_usd'], source_scene_usd=profile['scene_usd'], configuration_directory=str(configuration))
        write_json(p['state']/'session.json', state, private=True)
        print(f"Profile: {config['profile']} | USD: {prepared['scene_usd']}", flush=True)
        print('Runtime configuration: '+str(configuration), flush=True)
        if streaming:
            if not config.get('streaming', {}).get('enabled'):
                raise ValueError('Enable and deploy streaming in machine configuration first')
            address, turn, ingress, viewer = stream_config(config, directory)
            env.update(STREAM_PUBLIC_IP=address, PIPER_NVENC_FILTER=str(p['sdk']/'nvenc/libnvenc_fix.so'))
            spawn('coturn', [str(p['sdk']/'turn/bin/turnserver'), '-c', str(turn)])
            # Wait for TURN to bind before installing its TCP ingress.
            deadline = time.monotonic()+30
            while 'Relay address to use' not in (directory/'coturn.log').read_text(errors='replace'):
                if done.wait(.2) or any(c.poll() is not None for c in children):
                    raise RuntimeError('TURN startup failed; inspect '+str(directory))
                if time.monotonic() > deadline: raise TimeoutError('TURN startup')
            spawn('haproxy', ['/usr/sbin/haproxy', '-db', '-f', str(ingress)])
            write_json(runtime, viewer, private=True)
        spawn('isaac', ['bash', str(ROOT/'runtime/isaac/run-isaac-ros.sh'), str(devices)] +
              (['--stream'] if streaming else []), env)
        spawn('devices', ros_command(config, ['ros2', 'launch', 'piper_sim_devices',
              'devices.launch.py', 'config:='+str(devices)]), env)
        spawn('readiness', ros_command(config, ['python3', ROOT/'checks/readiness.py',
              str(devices), str(readiness)]), env)
        print('Session logs: '+str(directory), flush=True)
        while not done.wait(.5):
            if any(c.poll() is not None for c in children):
                raise RuntimeError('Device or streaming process exited; inspect '+str(directory))
            limit = config['watchdog_seconds']
            if limit and time.monotonic()-started >= limit: break
            log = directory/'isaac.log'
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size-262144)); tail = handle.read()
            state.update(components_started=True,
                         scene_ready=state['scene_ready'] or b'PIPER_ISAAC_READY' in tail,
                         stream_initialized=state['stream_initialized'] or b'PIPER_STREAM_INITIALIZED' in tail,
                         ros_devices_ready=readiness.exists())
            if requests.exists():
                request = json.loads(requests.read_text()); requests.unlink()
                if request['action'] == 'cancel':
                    cancel_experiment(config, experiment_proc, env)
                elif experiment_proc is None or experiment_proc.poll() is not None:
                    if not state['ros_devices_ready'] or not config.get('main_project'):
                        state['experiment_error'] = 'Ready devices and a configured main_project are required'
                        write_json(p['state']/'session.json', state, private=True)
                        continue
                    experiment_name = 'experiment-'+str(time.time_ns())
                    experiment_directory = directory/experiment_name
                    state['experiment_directory'] = str(experiment_directory)
                    state.pop('experiment_error', None)
                    state.pop('experiment_exit_code', None)
                    command = ['ros2', 'launch', 'nankai_sim_integration', 'experiment.launch.py',
                               'task:='+request.get('task', 'preposition'),
                               'alignment_config:='+str(prepared['alignment']),
                               'pick_config:='+str(prepared['pick']),
                               'detector_config:='+str(prepared['detector']),
                               'driver_namespace:='+profile['driver_namespace'],
                               'device_state:='+str(p['state']/'devices/device-state.json'),
                               'output_dir:='+str(experiment_directory)]
                    # Experiments have their own lifetime; their exit does not close devices.
                    experiment_proc = spawn(experiment_name, ros_command(config, command, main=True), env)
                    children.remove(experiment_proc)
            state['experiment_running'] = experiment_proc is not None and experiment_proc.poll() is None
            if experiment_proc is not None and experiment_proc.poll() is not None:
                state['experiment_exit_code'] = experiment_proc.returncode
            write_json(p['state']/'session.json', state, private=True)
    finally:
        cancel_experiment(config, experiment_proc, env)
        for proc in reversed(children): terminate(proc)
        for handle in handles: handle.close()
        for name in ('turn.conf', 'haproxy.cfg'): (directory/name).unlink(missing_ok=True)
        runtime.unlink(missing_ok=True)
        readiness.unlink(missing_ok=True)
        state.update(components_started=False, scene_ready=False, ros_devices_ready=False,
                     stream_initialized=False, experiment_running=False, start=None)
        write_json(p['state']/'session.json', state, private=True)
        lock.close()


def experiment(config, action, task='preposition'):
    state = status(config)
    if not state['manager_active']: raise RuntimeError('Start devices first')
    if action == 'start' and state.get('profile') != config['profile']:
        raise ValueError('Active profile is '+str(state.get('profile'))+'; stop/start the session to switch profiles')
    if action == 'start' and not state.get('ros_devices_ready'): raise RuntimeError('ROS devices are not ready')
    if action == 'start' and not config.get('main_project'): raise ValueError('main_project is not configured')
    write_json(paths(config)['state']/'experiment-request.json', {'action': action, 'task': task}, private=True)
