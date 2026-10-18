#!/usr/bin/env python3
"""外部进程与持久恢复协议的 Host 检查；不作为真实 Kubernetes 证据。"""
import contextlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest


class ProcessBoundary(unittest.TestCase):
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
