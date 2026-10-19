#!/usr/bin/env python3
"""通过真实子进程验证批次协议；这些夹具不代表产品或 Kubernetes 通过。"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent


class ProtocolCLI(unittest.TestCase):
    def test_inherited_lane_lease_does_not_relock_or_unlock_batch_owner(self):
        """受控child共享同一open-file-description；退出后整批owner仍持独占锁。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane = root / 'api.lock'
            with lane.open('a') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX)
                result = subprocess.run([sys.executable, str(HERE/'session.py'), 'protocol-child',
                                         '--output', str(root/'child'), '--api-lane', str(lane),
                                         '--inherited-lane-fd', str(owner.fileno()), '--mode', 'before-first'],
                                        pass_fds=(owner.fileno(),), capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue((root/'child'/'setup-entered').exists())
                with lane.open('a') as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX|fcntl.LOCK_NB)

    """只从公开 CLI、持久 index 和进程退出观察停止协议。"""

    def test_before_first_failure_keeps_all_keys(self):
        """首例 setup 失败有当前环境错误和完整剩余键，不能丢弃尚未执行的例。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = subprocess.run([sys.executable, str(HERE / "session.py"), "protocol-child",
                                     "--output", str(out), "--mode", "before-first"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((out / "index.json").exists(), "setup 失败仍须保存完整 index")
            index = json.loads((out / "index.json").read_text())
            self.assertEqual(index["executionBoundary"], "protocol-fixture")
            self.assertEqual([r["outcome"] for r in index["results"]], ["environment_failed", "not_run"])
            self.assertEqual(index["state"], "failed")
            self.assertTrue(index["cleanup"]["success"])

    def test_after_first_failure_keeps_remaining_not_run(self):
        """首个协议 fixture 完成后崩溃，不能借这一行声称完整 profile 通过。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = subprocess.run([sys.executable, str(HERE / "session.py"), "protocol-child",
                                     "--output", str(out), "--mode", "after-first"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((out / "index.json").exists())
            index = json.loads((out / "index.json").read_text())
            self.assertEqual(index["executionBoundary"], "protocol-fixture")
            self.assertEqual([r["outcome"] for r in index["results"]], ["passed", "not_run"])
            self.assertEqual(index["state"], "failed")
            self.assertTrue(index["cleanup"]["success"])

    def test_lock_wait_stops_before_setup(self):
        """等锁时收到停止后，不能在锁释放时继续创建任何夹具。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane = root / "lane.lock"
            # 关键：测试父进程仅作为独立锁持有者，不持锁等待一个需要成功取锁的 child。
            with lane.open("a") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                child = subprocess.Popen(
                    [sys.executable, str(HERE / "session.py"), "protocol-child",
                     "--output", str(root / "run"), "--api-lane", str(lane),
                     "--mode", "lock-wait"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    index = root / "run" / "index.json"
                    end = time.monotonic() + 8
                    while not index.exists() and time.monotonic() < end and child.poll() is None:
                        time.sleep(.02)
                    self.assertTrue(index.exists(), "必须在等锁前持久化完整期望集")
                    seeded = json.loads(index.read_text())
                    self.assertEqual(len(seeded["expectedKeys"]), 2)
                    self.assertEqual([r["outcome"] for r in seeded["results"]], ["not_run", "not_run"])
                    child.send_signal(signal.SIGTERM)
                    child.communicate(timeout=8)
                    self.assertNotEqual(child.returncode, 0)
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)
                    fcntl.flock(held, fcntl.LOCK_UN)
            actual = json.loads(index.read_text())
            self.assertEqual(actual["state"], "interrupted")
            self.assertEqual([r["outcome"] for r in actual["results"]], ["not_run", "not_run"])
            self.assertFalse((root / "run" / "setup-entered").exists())
            self.assertIn("SIGTERM", [s["name"] for s in actual["signals"]])
            self.assertTrue(actual["cleanup"]["success"])


if __name__ == "__main__":
    unittest.main()
