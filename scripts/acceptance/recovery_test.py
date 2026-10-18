"""恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from recovery import PersistenceStop, RecoveryLedger, stop_processes, process_inventory
from kube_transport import KubeTransport
from transport import ProcessRunner

HERE = Path(__file__).resolve().parent


class RecoveryProtocol(unittest.TestCase):
    def fixture(self, root, actual_cluster_uid='original-cluster', profile='CI_SMOKE'):
        repo = root / 'source';scripts = repo / 'scripts/acceptance';scripts.mkdir(parents=True)
        for name in ['recovery.py', 'session.py', 'transport.py', 'ledger.py', 'kube_transport.py', 'prepare.py', 'source-lock.json', 'image_provenance.py', 'gate.py', 'registry.py']:
            shutil.copyfile(HERE / name, scripts / name)
        shutil.copytree(HERE.parents[1] / 'docs', repo / 'docs')
        (repo / '.gitignore').write_text('__pycache__/\n')
        state = root / 'fake-state.json';calls = root / 'fake-calls.jsonl'
        kubectl = repo / 'fake-kubectl'
        kubectl.write_text('#!' + sys.executable + '\n' + '''import json,pathlib,sys
state=pathlib.Path(STATE);calls=pathlib.Path(CALLS);args=sys.argv[1:];tail=args[args.index('--context')+2:]
body=sys.stdin.read() if '-f' in tail else ''
with calls.open('a') as out:out.write(json.dumps({'args':args,'body':json.loads(body) if body else None})+'\\n')
if tail[:3] in [['get','namespaces','kube-system'],['get','namespace','kube-system']]:
 print(json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':'kube-system','uid':'original-data-cluster' if 'data' in args[args.index('--context')+1] else 'original-cluster','resourceVersion':'1'}}))
elif tail[:2]==['delete','--raw']:
 assert json.loads(body)['preconditions']['uid']=='original-namespace';state.write_text('deleted');print('{}')
elif state.exists():
 print('Error from server (NotFound)',file=sys.stderr);sys.exit(1)
else:print(json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':'original-ns','uid':'original-namespace','resourceVersion':'1'}}))
'''.replace('STATE', repr(str(state))).replace('CALLS', repr(str(calls))).replace("else 'original-cluster'", "else "+repr(actual_cluster_uid)))
        kubectl.chmod(0o755)
        for argv in [['git','init','-q'],['git','add','.'],['git','-c','user.name=HostProtocol','-c','user.email=fixture@example.invalid','commit','-qm','test: Host恢复夹具']]:
            subprocess.run(argv,cwd=repo,check=True,capture_output=True)
        candidate = subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
        tree = subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=repo,text=True).strip()
        old = root / 'original';private = old / '.private';private.mkdir(parents=True)
        lane = root / 'api-lane';lane.touch()
        common = dict(candidateCommit=candidate,testedCommit=candidate,runnerCommit=candidate,testedTree=tree,dirty=False,
                      source=dict(commit=candidate,tree=tree,dirty=False))
        index = dict(schemaVersion=1,runId='host-protocol-original',profile=profile,executionBoundary='validator-test',
                     productGate=False,environment={'targets':[{'name':'control','clusterUID':'original-cluster','nodeCount':3}]},**common)
        (old / 'index.json').write_text(json.dumps(index))
        descriptor = dict(profile=profile,runId=index['runId'],candidate=candidate,source=candidate,apiLane=str(lane),
                          kubectl=str(kubectl),kind='NEVER-REAL-KIND',targets={'control':dict(name='mm-host-protocol-control',
                          kubeconfig=str(private/'control-config'),context='kind-mm-host-protocol-control',clusterUID='original-cluster',
                          containers=[{'id':'d'*64,'name':'mm-host-protocol-control-control-plane'}])})
        (private / 'environment.json').write_text(json.dumps(descriptor));(private / 'environment.json').chmod(0o600)
        entry = dict(id='original-entry',target='control',clusterUID='original-cluster',action='create',origin='fixture-response',case='fixture.K',
                     stage='confirmed',uid='original-namespace',resourceVersion='1',identity=dict(apiVersion='v1',kind='Namespace',namespace='',name='original-ns'))
        ledger = dict(schemaVersion=1,runId=index['runId'],candidateCommit=candidate,targets={'control':'original-cluster'},
                      entries={'original-entry':entry},processes={},events=[])
        if profile=='LOCAL_FULL':
            index['environment']['targets'].append(dict(name='data',clusterUID='original-data-cluster',nodeCount=1))
            descriptor['targets']['data']=dict(descriptor['targets']['control'],name='mm-host-protocol-data',context='kind-mm-host-protocol-data',
                kubeconfig=str(private/'data-config'),clusterUID='original-data-cluster',containers=[{'id':'e'*64,'name':'mm-host-protocol-data-control-plane'}])
            ledger['targets']['data']='original-data-cluster'
            (old/'index.json').write_text(json.dumps(index));(private/'environment.json').write_text(json.dumps(descriptor))
        (old / 'ledger.json').write_text(json.dumps(ledger))
        return repo,old,candidate,calls

    def invoke(self, root, repo, old, candidate, **updates):
        args = dict(output=str(old),environment_json=str(old/'.private/environment.json'),candidate=candidate,
                    recovery_output=str(root/'recovery'),environment_only=False,inherited_lane_fd=None)
        args.update(updates)
        command = [sys.executable,'-c','import json,sys,types,recovery;sys.exit(recovery.recover(types.SimpleNamespace(**json.loads(sys.argv[1]))))',json.dumps(args)]
        descriptors=(args['inherited_lane_fd'],) if args.get('inherited_lane_fd') is not None else ()
        result = subprocess.run(command,cwd=repo,env=dict(os.environ,PYTHONPATH=str(repo/'scripts/acceptance')),capture_output=True,text=True,pass_fds=descriptors)
        return result,json.loads((Path(args['recovery_output'])/'recovery.json').read_text())

    def test_actual_clean_source_uid_normal_gc_keeps_original_run_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            before={str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            result,report=self.invoke(root,repo,old,candidate)
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            self.assertTrue(report['success']);self.assertIs(report['productGate'],False)
            after={str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            writes=[json.loads(line) for line in calls.read_text().splitlines() if 'delete' in json.loads(line)['args']]
            self.assertEqual(len(writes),1);self.assertEqual(writes[0]['body']['preconditions'],{'uid':'original-namespace'})
            self.assertNotIn('gracePeriodSeconds',writes[0]['body'])
            self.assertNotIn('finalizers',json.dumps(writes))

    def test_wrong_actual_source_or_dirty_tree_rejects_before_any_api(self):
        for mode in ['wrong','dirty']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);repo,old,candidate,calls=self.fixture(root)
                if mode=='wrong':candidate='e'*40
                else:(repo/'uncommitted').write_text('Host only')
                result,report=self.invoke(root,repo,old,candidate)
                self.assertEqual(result.returncode,1);self.assertFalse(report['success'])
                self.assertFalse(calls.exists());self.assertIs(report['productGate'],False)

    def test_original_cluster_uid_mismatch_never_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            descriptor=old/'.private/environment.json';value=json.loads(descriptor.read_text());value['targets']['control']['clusterUID']='replacement-cluster';descriptor.write_text(json.dumps(value))
            result,report=self.invoke(root,repo,old,candidate)
            self.assertEqual(result.returncode,1);self.assertFalse(report['success'])
            self.assertFalse(calls.exists())

    def test_child_manifest_without_ledger_keeps_identity_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            index=json.loads((old/'index.json').read_text());manifest={k:index[k] for k in ['schemaVersion','runId','profile','candidateCommit','testedCommit','testedTree','runnerCommit','source','dirty']}
            manifest.update(targets=index['environment']['targets'],entries=[dict(variantId='fixture.child',layer='K',output='cases/K.fixture',ledgerPath='cases/K.fixture/ledger.json',requestSHA256='f'*64,stage='intent')])
            request=dict(entry=dict(variantId='fixture.child',layer='K'),profile='CI_SMOKE',candidate=candidate,tree=index['testedTree'],
                         parentRunId=index['runId'],runId='host-protocol-child',output=str(old/'cases/K.fixture'),supplemental=False,
                         environment=json.loads((old/'.private/environment.json').read_text()),apiLane=str(root/'api-lane'))
            path=old/'.private/case-request.json';path.write_text(json.dumps(request))
            manifest['entries'][0].update(requestPath='.private/case-request.json',requestSHA256=hashlib.sha256(path.read_bytes()).hexdigest())
            (old/'case-runs.json').write_text(json.dumps(manifest))
            result,report=self.invoke(root,repo,old,candidate)
            self.assertEqual(result.returncode,1);self.assertFalse(report['success'])
            self.assertTrue(report['identityGaps'])

    def test_actual_cluster_uid_replacement_is_rejected_before_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root,'actual-replacement')
            result,report=self.invoke(root,repo,old,candidate)
            self.assertEqual(result.returncode,1)
            self.assertEqual(report['errorCode'],'recovery-actual-cluster-uid-mismatch')
            self.assertFalse(any('delete' in json.loads(line)['args'] for line in calls.read_text().splitlines()))

    def test_prepare_partial_snapshot_never_adopts_unknown_container_uid(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            env=old/'.private/environment.json';descriptor=json.loads(env.read_text());env.unlink();(old/'ledger.json').unlink()
            descriptor['targets']['control'].pop('clusterUID');descriptor['targets']['control'].pop('containers')
            (old/'.private/environment-ledger.jsonl').write_text(json.dumps({'event':'prepare_failed','descriptor':descriptor})+'\n')
            request=root/'request.json';request.write_text(json.dumps(descriptor))
            index=old/'index.json';value=json.loads(index.read_text());value['environment']['targets']=[];index.write_text(json.dumps(value))
            before={str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            result,report=self.invoke(root,repo,old,candidate,environment_json=str(request),environment_only=True)
            self.assertEqual(result.returncode,1)
            self.assertEqual(report['environment']['targets']['control']['error'],'RECOVER_CONTAINER_UID_UNKNOWN')
            self.assertFalse(calls.exists())
            self.assertEqual(before,{str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()})

    def test_inherited_same_fd_lease_is_kept_locked_after_recovery_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            with (root/'api-lane').open('a') as lane:
                fcntl.flock(lane,fcntl.LOCK_EX)
                result,report=self.invoke(root,repo,old,candidate,inherited_lane_fd=lane.fileno())
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertTrue(report['success'])
                with (root/'api-lane').open('a') as other:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(lane,fcntl.LOCK_UN)

    def test_shared_verified_full_environment_keeps_creation_owner_for_new_execution(self):
        for mode in ['valid','wrong-owner','wrong-execution']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);repo,old,candidate,calls=self.fixture(root,profile='LOCAL_FULL')
                path=old/'index.json';index=json.loads(path.read_text());index.update(runId='host-protocol-reg104',supplemental=True,
                    executionRunId='host-protocol-reg104',environmentOwnerRunId='host-protocol-original')
                if mode=='wrong-owner':index['environmentOwnerRunId']='wrong-environment-owner'
                elif mode=='wrong-execution':index['executionRunId']='wrong-execution'
                path.write_text(json.dumps(index))
                path=old/'ledger.json';ledger=json.loads(path.read_text());ledger['runId']=index['runId'];path.write_text(json.dumps(ledger))
                environment=old/'.private/environment.json';before=environment.read_bytes()
                result,report=self.invoke(root,repo,old,candidate)
                self.assertEqual(result.returncode,0 if mode=='valid' else 1,result.stdout+result.stderr)
                if mode=='valid':
                    self.assertEqual(report['executionRunId'],'host-protocol-reg104')
                    self.assertEqual(report['environmentOwnerRunId'],'host-protocol-original')
                else:
                    self.assertEqual(report['errorCode'],'recovery-environment-owner-mismatch');self.assertFalse(calls.exists())
                self.assertEqual(environment.read_bytes(),before)

    def test_fsync_failure_escapes_old_recover_broad_catch_before_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=RecoveryLedger(Path(directory)/'ledger.json',run_id='Host-protocol',candidate='a'*40,targets={'control':'original-cluster'})
            obj=dict(apiVersion='v1',kind='Namespace',metadata=dict(name='original-ns',uid='original-namespace',resourceVersion='1'))
            token=ledger.intent('control','create',dict(apiVersion='v1',kind='Namespace',namespace='',name='original-ns'));ledger.confirm(token,obj)
            calls=[]
            def execute(argv,**kwargs):calls.append(argv);return subprocess.CompletedProcess(argv,0,json.dumps(obj).encode(),b'')
            kube=KubeTransport(ledger=ledger,execute=execute,targets={'control':{'kubeconfig':'Host-only','context':'Host-only'}},kubectl=['NEVER-REAL-KUBECTL'])
            with patch('ledger.atomic_json',side_effect=OSError('SYNTHETIC-PRIVATE-FSYNC')):
                with self.assertRaises(PersistenceStop):kube.recover_case()
            self.assertEqual(len(calls),1);self.assertFalse(any('delete' in argv for argv in calls))

    def test_auxiliary_group_and_term_ignoring_grandchild_are_stopped_by_birth_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);events=[];runner=ProcessRunner(root/'raw',event=events.append)
            ready=root/'ready'
            code="import pathlib,signal,subprocess,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(60)"
            child=runner.popen([sys.executable,'-c',code,str(ready)])
            original=dict(events[-1]);end=time.monotonic()+3
            try:
                while not ready.exists() and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(ready.exists());checkpoints=[]
                report=stop_processes({original['id']:original},runner.run,lambda value:checkpoints.append(json.loads(json.dumps(value))),grace=.1)
                self.assertTrue(report['success']);child.wait(timeout=5)
                self.assertTrue(any(value['processes'][0].get('state')=='stop-intent' for value in checkpoints))
                self.assertFalse(any(v['pgid']==child.pid for v in process_inventory(runner.run).values()))
            finally:runner.close_auxiliary()

    def test_reused_birth_is_refused_without_signalling_live_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            events=[];runner=ProcessRunner(Path(directory)/'raw',event=events.append)
            child=runner.popen([sys.executable,'-c','import time;time.sleep(60)']);original=dict(events[-1],birth='wrong original birth')
            try:
                report=stop_processes({original['id']:original},runner.run,lambda value:None,grace=.1)
                self.assertFalse(report['success']);self.assertIsNone(child.poll())
            finally:runner.close_auxiliary()

    def test_active_recovery_coordinator_is_never_a_stop_target(self):
        with tempfile.TemporaryDirectory() as directory:
            runner=ProcessRunner(Path(directory)/'raw')
            for pid in [os.getpid(),os.getppid()]:
                report=stop_processes({'fixture':dict(pid=pid,birth='must-not-signal',stage='started')},runner.run,lambda value:None)
                self.assertFalse(report['success'])
                self.assertEqual(report['processes'][0]['errorCode'],'process-is-active-recovery-coordinator')

    def test_child_request_original_hash_and_child_run_id_are_required(self):
        for mode in ['valid','changed-request','wrong-child-ledger']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);repo,old,candidate,calls=self.fixture(root)
                index=json.loads((old/'index.json').read_text());environment=json.loads((old/'.private/environment.json').read_text())
                child=old/'cases/K.fixture';child.mkdir(parents=True)
                ledger=json.loads((old/'ledger.json').read_text());ledger.update(runId='expected-child' if mode!='wrong-child-ledger' else 'different-child',entries={})
                (child/'ledger.json').write_text(json.dumps(ledger))
                request=dict(entry=dict(variantId='fixture.child',layer='K'),profile='CI_SMOKE',candidate=candidate,tree=index['testedTree'],
                    parentRunId=index['runId'],runId='expected-child',output=str(child),supplemental=False,environment=environment,apiLane=str(root/'api-lane'))
                path=old/'.private/case-request.json';path.write_text(json.dumps(request));digest=hashlib.sha256(path.read_bytes()).hexdigest()
                if mode=='changed-request':path.write_text(json.dumps(dict(request,runId='changed-child')))
                manifest={k:index[k] for k in ['schemaVersion','runId','profile','candidateCommit','testedCommit','testedTree','runnerCommit','source','dirty']}
                manifest.update(targets=index['environment']['targets'],entries=[dict(variantId='fixture.child',layer='K',output='cases/K.fixture',
                    ledgerPath='cases/K.fixture/ledger.json',requestPath='.private/case-request.json',requestSHA256=digest,stage='exited')])
                (old/'case-runs.json').write_text(json.dumps(manifest))
                result,report=self.invoke(root,repo,old,candidate)
                self.assertEqual(result.returncode,0 if mode=='valid' else 1,result.stdout+result.stderr)
                if mode=='valid':self.assertEqual(len(report['resources']),2)
                else:self.assertFalse(calls.exists())

    def test_lock_wait_signal_keeps_seed_and_never_enters_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo,old,candidate,calls=self.fixture(root)
            args=dict(output=str(old),environment_json=str(old/'.private/environment.json'),candidate=candidate,
                      recovery_output=str(root/'recovery'),environment_only=False,inherited_lane_fd=None)
            command=[sys.executable,'-c','import json,sys,types,recovery;sys.exit(recovery.recover(types.SimpleNamespace(**json.loads(sys.argv[1]))))',json.dumps(args)]
            with (root/'api-lane').open('a') as lane:
                fcntl.flock(lane,fcntl.LOCK_EX)
                child=subprocess.Popen(command,cwd=repo,env=dict(os.environ,PYTHONPATH=str(repo/'scripts/acceptance')),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    index=root/'recovery/index.json';end=time.monotonic()+5
                    while time.monotonic()<end:
                        if index.exists() and json.loads(index.read_text()).get('state')=='waiting_lock':break
                        time.sleep(.02)
                    self.assertEqual(json.loads(index.read_text())['state'],'waiting_lock')
                    child.send_signal(signal.SIGTERM);stdout,stderr=child.communicate(timeout=5)
                    self.assertEqual(child.returncode,1,(stdout+stderr).decode())
                    self.assertEqual(json.loads(index.read_text())['state'],'interrupted')
                    self.assertFalse(calls.exists())
                    self.assertEqual(json.loads((root/'recovery/recovery.json').read_text())['state'],'interrupted')
                finally:
                    if child.poll() is None:child.kill();child.wait()
                    fcntl.flock(lane,fcntl.LOCK_UN)


if __name__ == '__main__':unittest.main()
