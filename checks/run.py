"""Acceptance orchestration, separate from normal device process ownership."""
import fcntl
import os
import subprocess
import time
from runtime.config import ROOT, paths, ros_command


def check(config, level, streaming=False):
    p = paths(config)
    if level == 'unit':
        subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s',str(ROOT/'tests')],check=True,cwd=ROOT)
        if config.get('main_project'):
            for name in ('controller_check', 'upstream_gripper_check', 'upstream_motion_check'):
                subprocess.run([p['build']/'devices/piper_sim_devices'/name], check=True)
        return
    if level in ('control','perception') and not config.get('main_project'):
        raise ValueError('This acceptance level requires main_project')
    p['state'].mkdir(parents=True,exist_ok=True)
    with (p['state']/'session.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Stop the device session before GPU acceptance') from None
        output = p['reports']/(level+'-'+str(time.time_ns()))
        from runtime.scene import prepare
        prepared = prepare(config, output/'config', main=level!='devices')
        if level == 'perception':
            command = ['python3',ROOT/'checks/check_sim_perception.py',
                       '--devices-config',prepared['devices'],'--alignment-config',prepared['alignment'],
                       '--detector-config',prepared['detector'],'--output',output]
        elif level == 'control':
            command = ['python3', ROOT/'checks/check_sim_control.py', '--config', prepared['devices'], '--output', output]
        else:
            command = ['python3',ROOT/'checks/check_sim_devices.py','--config',prepared['devices'],
                       '--camera-calibration','--output',output]
        if streaming:
            if level == 'perception': raise ValueError('Streaming check is available for devices/control')
            from runtime.streaming.turn_config import relay_ip
            os.environ['STREAM_PUBLIC_IP'] = relay_ip(config.get('streaming', {}).get('relay_ip'))
            os.environ['PIPER_NVENC_FILTER'] = str(p['sdk']/'nvenc/libnvenc_fix.so')
            command += ['--stream']
        process = subprocess.run(ros_command(config,command,main=level!='devices'))
        print('Acceptance report: '+str(output),flush=True)
        return process.returncode
