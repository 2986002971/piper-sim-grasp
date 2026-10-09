import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
import lifecycle
import manage
import migrate_config
import turn
import turn_remote


class ServerTests(unittest.TestCase):
    def test_turn_only_configuration_and_migration(self):
        c={'remote':'user@example.invalid','ssh_port':22,'raw_dir':'/tmp/private',
           'remote_worker':'/remote/worker.py','isaac':{'launcher':'/remote/start'},
           'turn':{'host':'turn.example.invalid','port':27645,'username':'a'*32,'password':'b'*64}}
        result=turn.viewer_config(c,'172.17.0.6')
        self.assertEqual(set(result),{'turn'})
        server,client=migrate_config.split(dict(c,proxy='removed',tun='removed',remote_key='private'),result)
        self.assertNotIn('remote_key',server);self.assertNotIn('proxy',server)
        self.assertNotIn('remote',client);self.assertEqual(client,result)

    def test_restricted_coturn(self):
        text=turn_remote.render({'relay_ip':'172.17.0.6','username':'a'*32,'password':'b'*64})
        self.assertIn('allowed-peer-ip=172.17.0.6',text)
        self.assertIn('no-tcp-relay',text);self.assertNotIn('external-ip',text)
        with self.assertRaises(ValueError):turn_remote.render({'relay_ip':'127.0.0.1','username':'a'*32,'password':'b'*64})

    def test_failure_cleanup_removes_partial_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            c={'raw_dir':temp,'turn':{'tcp_ingress':True},'isaac':{}}
            stack=turn.TurnStack(c,'turn')
            with patch('turn.preflight',return_value={'relay_ip':'172.17.0.6'}),patch('turn.remote',side_effect=[RuntimeError('write failed'),None]) as remote:
                with self.assertRaises(RuntimeError): stack.start()
                stack.close()
                self.assertEqual(remote.call_args_list[-1].args[1],'remove')
                self.assertIn('ingress_name',remote.call_args_list[-1].args[2])
            runtime=Path(temp)/'viewer-runtime.json';runtime.write_text('{}');stack.runtime_file=runtime
            with patch('turn.remote'):stack.close()
            self.assertFalse(runtime.exists())

    def test_owned_worker_stops_on_eof(self):
        worker=Path(__file__).resolve().parents[1]/'server/remote_worker.py'
        payload={'argv':[sys.executable,'-c','import time; time.sleep(120)']}
        proc=subprocess.Popen([sys.executable,str(worker),json.dumps(payload)],stdin=subprocess.PIPE)
        proc.stdin.close()
        proc.wait(timeout=8)
        self.assertIsNotNone(proc.returncode)

    def test_process_cleanup_and_stale_state(self):
        with tempfile.TemporaryDirectory() as temp:
            owner=lifecycle.ProcessOwner({'raw_dir':temp},'test')
            proc=owner.spawn('owned',[sys.executable,'-c','import time; time.sleep(120)'])
            owner.close();self.assertIsNotNone(proc.poll())
            manage.private_write(Path(temp)/'network-state.json',{'pid':os.getpid(),'start':'different','mode':'turn'})
            (Path(temp)/'viewer-runtime.json').write_text('{}')
            result=manage.state_status(Path(temp))
            self.assertTrue(result['configuration_present']);self.assertFalse(result['manager_active'])
            self.assertFalse(result['isaac_ready'])
            self.assertEqual((Path(temp)/'network-state.json').stat().st_mode & 0o777,0o600)


if __name__=='__main__':unittest.main()
