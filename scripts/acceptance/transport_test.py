#!/usr/bin/env python3
"""外部进程与持久恢复协议的 Host 检查；不作为真实 Kubernetes 证据。"""
import contextlib
import io
import json
from pathlib import Path
import stat
import os
import sys
import tempfile
import time
import unittest


class ProcessBoundary(unittest.TestCase):
    def test_parent_forwards_each_signal_once_and_waits_for_child_finally(self):
        """父持批次锁等待时转交TERM/INT，child自己的finally自然完成且不被重复触发。"""
        import signal
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ready=root/'ready';done=root/'finally.json'
            script=("import json,signal,time,pathlib\nseen=[]\n"+
                "def stop(number,frame):seen.append(number)\n"+
                "signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)\n"+
                "pathlib.Path("+repr(str(ready))+").write_text('ready')\n"+
                "try:\n while not seen:time.sleep(.02)\n time.sleep(.2)\nfinally:pathlib.Path("+repr(str(done))+").write_text(json.dumps(seen))\n")
            values=[{'number':signal.SIGTERM},{'number':signal.SIGINT}]
            result=ProcessRunner(root/'private').run([sys.executable,'-c',script],timeout=5,
                cooperative_signals=lambda: values if ready.exists() else [])
            self.assertEqual(result.returncode,0)
            self.assertCountEqual(json.loads(done.read_text()),[signal.SIGTERM,signal.SIGINT])

    def test_running_browser_process_observes_cooperative_stop(self):
        """主线程等待Node/Go时也检查停止；被中断child先退出再将原停止上抛。"""
        from transport import ProcessRunner
        from session import StopRequested
        count=0
        def checkpoint():
            nonlocal count
            count+=1
            if count>1:raise StopRequested('host-fixture')
        with tempfile.TemporaryDirectory() as tmp:
            runner=ProcessRunner(Path(tmp),checkpoint=checkpoint)
            with self.assertRaises(StopRequested):
                runner.run([sys.executable,'-c','import time;time.sleep(.4)'])

    def test_auxiliary_group_stops_ignoring_grandchild_after_parent_exits(self):
        """父进程收到TERM即退出时，也必须结束同一原进程组中忽略TERM的孙进程。"""
        from transport import ProcessRunner, process_identity
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); pidfile=root/'grandchild.pid'
            child_code="import os,signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(60)"
            script="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child_code)+"]);time.sleep(60)"
            runner=ProcessRunner(root/'private')
            parent=runner.popen([sys.executable,'-c',script])
            end=time.monotonic()+5
            while not pidfile.exists() and time.monotonic()<end:time.sleep(.02)
            self.assertTrue(pidfile.exists())
            pid=int(pidfile.read_text());birth=process_identity(pid)
            try:
                self.assertEqual(runner.close_auxiliary(),[])
                end=time.monotonic()+2
                while process_identity(pid)==birth and time.monotonic()<end:time.sleep(.02)
                self.assertNotEqual(process_identity(pid),birth)
            finally:
                if process_identity(pid)==birth:os.kill(pid,9)
                if parent.poll() is None:parent.kill();parent.wait()

    def test_private_file_sink_keeps_actual_bytes_without_console_passthrough(self):
        """Docker save 一类调用保留私有二进制文件，不能恢复任意stdout透传。"""
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            runner = ProcessRunner(Path(tmp) / "private")
            with tempfile.TemporaryFile() as saved:
                runner.run([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'fixture-tar-bytes')"], stdout=saved)
                saved.seek(0)
                self.assertEqual(saved.read(), b"fixture-tar-bytes")

    def test_raw_output_is_private_before_any_console(self):
        """真实 child 的原 stdout/stderr 仅给调用者与私有捕获，不自动进入公开 console。"""
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            console = io.StringIO()
            runner = ProcessRunner(root)
            with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                result = runner.run([sys.executable, "-c", "import sys;print('fixture-private-canary');print('fixture-stderr-canary',file=sys.stderr);sys.exit(7)"], label="fixture-output")
            self.assertEqual(result.returncode, 7)
            self.assertIn(b"fixture-private-canary", result.stdout)
            self.assertNotIn("canary", console.getvalue())
            captures = list(root.glob("*.stdout")) + list(root.glob("*.stderr"))
            self.assertEqual(len(captures), 2)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in captures))


class IdentityLedger(unittest.TestCase):
    def test_unconfirmed_create_blocks_dependent_namespace_cleanup(self):
        """cluster-scoped创建gap未闭合时，不撤其可能依赖的原namespace/RBAC。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'})
            namespace={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'owned','uid':'original-ns','resourceVersion':'1'}}
            token=ledger.intent('control','create',identity(namespace));ledger.confirm(token,namespace)
            ledger.intent('control','create',{'apiVersion':'monitoring.fjsxy.github.io/v1alpha1','kind':'KubeStateMetrics','namespace':'','name':'uncertain'})
            def execute(*args,**kwargs):
                calls.append(args)
                raise AssertionError('identity gap must block dependent cleanup')
            kube=KubeTransport(ledger=ledger,execute=execute,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl='fixture')
            report=kube.recover_case()
            self.assertFalse(report['success'])
            self.assertEqual(calls,[])
            self.assertEqual(ledger.entries[token]['stage'],'confirmed')

    def test_client_dry_run_keeps_yaml_bytes_and_never_creates_ledger_identity(self):
        """U06本地YAML转换保留原参数/stdin，不进入实际create及原UID确认。"""
        from ledger import Ledger
        from kube_transport import KubeTransport
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'})
            raw=b'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: yaml-only\n'
            script=root/'client.py';script.write_text("import sys,json\nassert sys.argv[-7:]==['create','--dry-run=client','--validate=false','-f','-','-o','json'];assert sys.stdin.buffer.read()=="+repr(raw)+"\nprint(json.dumps({'kind':'ConfigMap','metadata':{'name':'yaml-only'}}))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            result=kube.client_dry_run('control',['create','--dry-run=client','--validate=false','-f','-','-o','json'],raw)
            self.assertEqual(result.returncode,0)
            self.assertEqual(ledger.entries,{})

    def test_explicit_nonwaiting_delete_preserves_shutdown_observation_window(self):
        """T08的--wait=false必须在条件DELETE受理后返回，不能等Pod退出再观察503。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);read=root/'read'
            pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':'writer','namespace':'owned','uid':'original','resourceVersion':'9'}}
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'})
            key=ledger.intent('control','create',identity(pod));ledger.confirm(key,pod)
            script=root/'client.py';script.write_text("import json,sys,pathlib\np=pathlib.Path("+repr(str(read))+ ")\nif 'get' in sys.argv:\n n=int(p.read_text()) if p.exists() else 0\n if n>=2:print('Error from server (Forbidden)',file=sys.stderr);sys.exit(1)\n p.write_text(str(n+1));print(json.dumps("+repr(pod)+"))\nelse:print('{}')\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            result=kube.command('control',['delete','pod','writer','-n','owned','--wait=false'])
            self.assertEqual(result.returncode,0)
            self.assertEqual(ledger.entries[key]['stage'],'delete_accepted')

    def test_own_finalizer_added_to_absent_field_preserves_later_foreign_token(self):
        """原字段缺失也按token差异撤回，不能因缺失与空列表不同而清空外部token。"""
        from kube_transport import undo_delta
        before={'metadata':{}}
        after={'metadata':{'finalizers':['phase1.fixture/hold']}}
        current={'metadata':{'finalizers':['phase1.fixture/hold','external/retain']}}
        self.assertEqual(undo_delta(before,after,current),{'metadata':{'finalizers':['external/retain']}})

    def test_namespaced_create_rejects_replaced_parent_uid_before_mutation(self):
        """本run namespace被替代后，不能按同名将新fixture写进外部namespace。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, KubeFailure, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'created'
            original={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'owned','uid':'original','resourceVersion':'1'}}
            replacement=json.loads(json.dumps(original));replacement['metadata']['uid']='replacement'
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'})
            token=ledger.intent('control','create',identity(original));ledger.confirm(token,original)
            script=root/'client.py';script.write_text("import json,sys,pathlib\nif 'create' in sys.argv:\n pathlib.Path("+repr(str(marker))+").touch();obj=json.load(sys.stdin);obj['metadata'].update(uid='new-pod',resourceVersion='1');print(json.dumps(obj))\nelse:print(json.dumps("+repr(replacement)+"))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            with self.assertRaises(KubeFailure):
                kube.create_obj('control',{'apiVersion':'v1','kind':'Pod','metadata':{'name':'fixture','namespace':'owned'}})
            self.assertFalse(marker.exists())

    def test_cr_cleanup_failure_preserves_namespace_and_rbac(self):
        """原Agent未结束时不能撤namespace/RBAC，否则GC失去原运行身份。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); marker=root/'namespace-delete'
            objects=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':'owned','uid':'ns-original','resourceVersion':'1'}},
                     {'apiVersion':'monitoring.coreos.com/v1alpha1','kind':'PrometheusAgent','metadata':{'name':'agent','namespace':'owned','uid':'agent-original','resourceVersion':'1'}}]
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'});ledger.case='fixture.case'
            for obj in objects:
                token=ledger.intent('control','create',identity(obj));ledger.confirm(token,obj)
            script=root/'client.py'
            script.write_text("import json,sys,pathlib\nobjects="+repr(objects)+"\nif 'delete' in sys.argv:\n raw=sys.argv[sys.argv.index('--raw')+1]\n if '/namespaces/owned'==raw:pathlib.Path("+repr(str(marker))+").touch()\n print('Error from server (Forbidden)',file=sys.stderr);sys.exit(1)\nprint(json.dumps(objects[1] if 'prometheusagents' in sys.argv else objects[0]))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            result=kube.recover_case('fixture.case')
            self.assertFalse(result['success'])
            self.assertFalse(marker.exists())
            self.assertIn('dependent_cleanup_blocked',[f['errorCode'] for f in result['failures']])

    def test_recovery_read_forbidden_is_not_absent(self):
        """真实外部client的403必须保留失败，不能按无资源报告清理成功。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            obj={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'owned','uid':'original','resourceVersion':'1'}}
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'});ledger.case='fixture.case'
            token=ledger.intent('control','create',identity(obj));ledger.confirm(token,obj)
            script=root/'client.py';script.write_text("import sys\nprint('Error from server (Forbidden)',file=sys.stderr);sys.exit(1)\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            result=kube.recover_case('fixture.case')
            self.assertFalse(result['success'])
            self.assertEqual(result['failures'][0]['errorCode'],'Forbidden')
            self.assertEqual(ledger.entries[token]['stage'],'confirmed')

    def test_restore_only_our_finalizer_and_preserve_later_foreign_token(self):
        """P14恢复只撤本fixture新增token，不清除后来出现的外来finalizer。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            before={"apiVersion":"v1","kind":"Namespace","metadata":{"name":"owned","uid":"original","resourceVersion":"9","finalizers":["original/keep"]}}
            after=json.loads(json.dumps(before));after['metadata']['finalizers'].append('phase1.fixture/hold')
            current=json.loads(json.dumps(after));current['metadata']['finalizers'].append('external/retain');current['metadata']['resourceVersion']='10'
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'});ledger.case='fixture.case'
            token=ledger.intent('control','fault-mutate',identity(before),uid='original',resource_version='9',baseline={'before':before,'after':after,'subresource':None});ledger.confirm(token,after)
            script=root/'client.py'
            script.write_text("import json,sys\nobj="+repr(current)+"\nif 'patch' in sys.argv:\n p=json.loads(sys.argv[sys.argv.index('-p')+1]);assert p[2:]==[{'op':'replace','path':'/metadata/finalizers','value':['original/keep','external/retain']}];obj['metadata']['finalizers']=['original/keep','external/retain'];obj['metadata']['resourceVersion']='11'\nprint(json.dumps(obj))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            kube.restore_fault(ledger.entries[token])
            self.assertEqual(ledger.entries[token]['stage'],'restored')

    def test_recovery_rejects_same_name_replacement_without_delete(self):
        """恢复读到替代UID必须失败，真实child不能收到任何delete命令。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            obj={"apiVersion":"v1","kind":"Namespace","metadata":{"name":"owned","uid":"original","resourceVersion":"9"}}
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'});ledger.case='fixture.case'
            token=ledger.intent('control','create',identity(obj));ledger.confirm(token,obj)
            script=root/'client.py';marker=root/'deleted'
            replacement=json.loads(json.dumps(obj));replacement['metadata']['uid']='replacement'
            script.write_text("import json,sys,pathlib\nif 'delete' in sys.argv:pathlib.Path("+repr(str(marker))+").touch()\nprint(json.dumps("+repr(replacement)+"))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            result=kube.recover_case('fixture.case')
            self.assertFalse(result['success'])
            self.assertFalse(marker.exists())
            self.assertIn('replacement_uid', [f['errorCode'] for f in result['failures']])

    def test_mutation_uses_original_uid_and_current_revision_conditions(self):
        """受控外部 client 验证实际发送JSON Patch含UID/RV测试，而非先GET后按名覆盖。"""
        from ledger import Ledger
        from kube_transport import KubeTransport, identity
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            obj={"apiVersion":"v1","kind":"Namespace","metadata":{"name":"owned","uid":"original","resourceVersion":"9","labels":{"other":"keep"}}}
            ledger=Ledger(root/'ledger.json',run_id='fixture',candidate='fixture',targets={'control':'cluster'})
            token=ledger.intent('control','create',identity(obj));ledger.confirm(token,obj)
            script=root/'client.py'
            script.write_text("import json,sys\nobj="+repr(obj)+"\nif 'patch' in sys.argv:\n p=json.loads(sys.argv[sys.argv.index('-p')+1]);assert p[0]=={'op':'test','path':'/metadata/uid','value':'original'};assert p[1]=={'op':'test','path':'/metadata/resourceVersion','value':'9'};obj['metadata']['labels']['ours']='set';obj['metadata']['resourceVersion']='10'\nprint(json.dumps(obj))\n")
            kube=KubeTransport(ledger=ledger,execute=ProcessRunner(root/'private').run,targets={'control':{'clusterUID':'cluster','kubeconfig':'fixture','context':'explicit'}},kubectl=[sys.executable,str(script)])
            desired=json.loads(json.dumps(obj));desired['metadata']['labels']['ours']='set'
            actual=kube.mutate_obj('control',desired)
            self.assertEqual(actual['metadata']['labels'],{'other':'keep','ours':'set'})

    def test_apply_list_confirms_each_original_response(self):
        """List部分成功仍有逐对象原UID，不能只记一个批次名字。"""
        from ledger import Ledger
        from kube_transport import KubeTransport
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = Ledger(root / "ledger.json", run_id="fixture", candidate="fixture", targets={"control":"cluster"})
            script = root / "client.py"
            script.write_text("import json,sys\nobj=json.load(sys.stdin);obj['metadata'].update(uid='original-'+obj['metadata']['name'],resourceVersion='1');print(json.dumps(obj))\n")
            kube = KubeTransport(ledger=ledger, execute=ProcessRunner(root / "private").run,
                                 targets={"control":{"clusterUID":"cluster","kubeconfig":"fixture","context":"explicit"}},
                                 kubectl=[sys.executable,str(script)])
            objects=[{"apiVersion":"v1","kind":"Namespace","metadata":{"name":n}} for n in ["one","two"]]
            result=kube.command("control", ["apply","-f","-"], data={"apiVersion":"v1","kind":"List","items":objects})
            self.assertEqual(result.returncode, 0)
            entries=list(Ledger.open(ledger.path).entries.values())
            self.assertEqual([e["uid"] for e in entries], ["original-one","original-two"])
            self.assertEqual([e["stage"] for e in entries], ["confirmed","confirmed"])

    def test_baseline_under_canonical_run_root_allows_system_tmp_alias(self):
        """系统入口的 /tmp 别名可用，run 内真正符号链接仍不能读取基线。"""
        from ledger import Ledger
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp) / "nested-run"
            ledger = Ledger(root / "ledger.json", run_id="fixture", candidate="fixture", targets={"control": "cluster"})
            token = ledger.intent("control", "fault", {"apiVersion":"v1","kind":"Namespace","namespace":"","name":"fixture"}, baseline={"field":"original"})
            self.assertEqual(ledger.baseline(ledger.entries[token]), {"field":"original"})
            baseline = root / ledger.entries[token]["baseline"]["path"]
            baseline.unlink()
            outside = root / "outside.json"
            outside.write_text('{"field":"original"}\n')
            baseline.symlink_to(outside)
            with self.assertRaises(ValueError):
                ledger.baseline(ledger.entries[token])

    def test_intent_survives_reopen_without_invented_uid(self):
        """发送窗口崩溃后保留未知身份，重开不能凭名字造出创建确认。"""
        from ledger import Ledger
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            ledger = Ledger(path, run_id="fixture", candidate="fixture", targets={"control": "cluster-original"})
            key = ledger.intent("control", "create", {"apiVersion": "v1", "kind": "Namespace", "name": "fixture-ns", "namespace": ""})
            reopened = Ledger.open(path)
            self.assertEqual(reopened.entries[key]["stage"], "intent")
            self.assertIsNone(reopened.entries[key].get("uid"))
            with self.assertRaises(ValueError):
                reopened.confirm(key, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "wrong", "uid": "replacement", "resourceVersion": "2"}})
            self.assertIsNone(Ledger.open(path).entries[key].get("uid"))

    def test_transport_persists_intent_before_actual_child_response(self):
        """外部 client 在返回创建响应前已能读到 intent，确认后才出现原 UID。"""
        from ledger import Ledger
        from kube_transport import KubeTransport
        from transport import ProcessRunner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = Ledger(root / "ledger.json", run_id="fixture", candidate="fixture", targets={"control": "cluster-original"})
            script = root / "client.py"
            script.write_text("import json,sys,pathlib\nledger=json.loads(pathlib.Path(sys.argv[1]).read_text());assert len(ledger['entries'])==1;assert next(iter(ledger['entries'].values()))['stage']=='intent'\nobj=json.load(sys.stdin);obj['metadata'].update(uid='original-uid',resourceVersion='9');print(json.dumps(obj))\n")
            transport = KubeTransport(ledger=ledger, execute=ProcessRunner(root / "private").run,
                                      targets={"control": {"clusterUID": "cluster-original", "kubeconfig": "fixture-only", "context": "explicit"}},
                                      kubectl=[sys.executable, str(script), str(ledger.path)])
            obj = transport.create_obj("control", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "fixture-ns"}})
            self.assertEqual(obj["metadata"]["uid"], "original-uid")
            entry = next(iter(Ledger.open(ledger.path).entries.values()))
            self.assertEqual(entry["stage"], "confirmed")
            self.assertEqual(entry["uid"], "original-uid")


if __name__ == "__main__":
    unittest.main()
