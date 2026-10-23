import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime import session
from runtime.config import load, write_json
from runtime.streaming.turn_config import render
from runtime.lifecycle import exclusive_operation, stamp

class RuntimeTests(unittest.TestCase):
    def test_mutation_cannot_overlap_session_or_build(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'data_root':temp}
            with exclusive_operation(config):
                with self.assertRaisesRegex(RuntimeError,'active session'):
                    with exclusive_operation(config): self.fail('Acquired overlapping lock')
            with exclusive_operation(config): pass

    def test_preserve_tcp_streaming_topology(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            credentials=directory/'credentials.json'
            credentials.write_text(json.dumps({'username':'a'*32,'password':'b'*64}))
            config={'streaming':{'credentials_file':str(credentials),'host':'stream.example',
                                'signaling_port':50239,'turn_port':27645}}
            with patch('runtime.session.relay_ip',return_value='172.17.0.6'), \
                 patch('runtime.session.socket.socket'):
                address,turn,ingress,viewer=session.stream_config(config,directory)
            self.assertEqual(address,'172.17.0.6')
            self.assertIn('bind 0.0.0.0:6008',ingress.read_text())
            self.assertIn('server coturn 172.17.0.6:16010 source 172.17.0.6',ingress.read_text())
            for line in ('listening-ip=172.17.0.6','listening-port=16010','relay-ip=172.17.0.6',
                         'no-udp','no-tcp-relay','min-port=49152','max-port=49215'):
                self.assertIn(line,turn.read_text().splitlines())
            stream=viewer['turn']
            self.assertEqual((stream['signalingServer'],stream['signalingPort']),('stream.example',50239))
            self.assertEqual((stream['mediaServer'],stream['mediaPort']),('172.17.0.6',47998))
            ice=stream['iceServerConfiguration']
            self.assertEqual(ice['iceTransportPolicy'],'relay')
            self.assertEqual(ice['iceServers'][0]['urls'],'turn:stream.example:27645?transport=tcp')

    def test_restricted_coturn(self):
        text = render({'relay_ip':'172.17.0.6','username':'a'*32,'password':'b'*64})
        self.assertIn('allowed-peer-ip=172.17.0.6', text)
        self.assertIn('no-tcp-relay', text)
        self.assertNotIn('external-ip', text)

    def test_host_streaming_uses_selected_interface(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            credentials = directory/'credentials.json'
            credentials.write_text(json.dumps({'username':'a'*32, 'password':'b'*64}))
            config = {'streaming': {'credentials_file':str(credentials), 'host':'stream.example',
                                   'signaling_port':6006, 'turn_port':6008, 'relay_ip':'100.127.117.67'}}
            with patch('runtime.session.relay_ip', return_value='100.127.117.67') as resolve, \
                 patch('runtime.session.socket.socket'):
                address, turn, ingress, viewer = session.stream_config(config, directory)
            resolve.assert_called_once_with('100.127.117.67')
            self.assertIn('server coturn 100.127.117.67:16010 source 100.127.117.67', ingress.read_text())
            self.assertIn('relay-ip=100.127.117.67', turn.read_text())
            self.assertEqual(viewer['turn']['mediaServer'], address)

    def test_stale_state_never_kills_reused_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            c = {'data_root':temp}
            write_json(Path(temp)/'state/session.json', {'pid':os.getpid(),'start':'different'})
            with patch('runtime.session.os.kill') as kill:
                session.stop(c)
                kill.assert_not_called()
            self.assertFalse(session.status(c)['manager_active'])

    def test_stop_does_not_wait_for_parent_to_reap_exited_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                config = {'data_root': temp}
                write_json(Path(temp)/'state/session.json', {'pid': child.pid, 'start': stamp(child.pid)})
                session.stop(config)
                # No poll/wait ran before stop: the terminated child can still
                # exist as a zombie, but no longer owns a live session.
                self.assertFalse(session.status(config)['manager_active'])
            finally:
                if child.poll() is None: child.kill()
                child.wait()

    def test_reject_data_inside_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'machine.json'
            path.write_text(json.dumps({'schema_version':1,'repo_path':temp,'data_root':temp+'/data'}))
            with self.assertRaisesRegex(ValueError,'outside'):
                load(str(path))

    def test_owned_worker_stops_on_eof(self):
        worker=Path(__file__).resolve().parents[1]/'runtime/remote_worker.py'
        payload={'argv':[sys.executable,'-c','import time; time.sleep(120)']}
        proc=subprocess.Popen([sys.executable,str(worker),json.dumps(payload)],stdin=subprocess.PIPE)
        proc.stdin.close()
        proc.wait(timeout=8)
        self.assertIsNotNone(proc.returncode)

    def test_worker_preserves_child_exit_with_stdin_open(self):
        worker=Path(__file__).resolve().parents[1]/'runtime/remote_worker.py'
        payload={'argv':[sys.executable,'-c','import time; time.sleep(.1); raise SystemExit(7)']}
        proc=subprocess.Popen([sys.executable,str(worker),json.dumps(payload)],
                              stdin=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            self.assertEqual(proc.wait(timeout=8),7)
            self.assertNotIn(b'Fatal Python error',proc.stderr.read())
        finally:
            if proc.poll() is None:
                proc.kill();proc.wait()
            proc.stdin.close();proc.stderr.close()


if __name__=='__main__': unittest.main()
