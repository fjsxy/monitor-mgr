#!/usr/bin/env python3
"""统一入口的 Host 协议检查；列举与失败留存不算功能子例执行。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class RunCLI(unittest.TestCase):
    def test_setup_error_writes_full_expected_profile(self):
        """正式入口在读取坏环境前预写17个Smoke键，错误不能吞掉剩余键。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = root / "invalid.json"
            environment.write_text("not-json")
            result = subprocess.run([sys.executable, str(HERE / "run.py"), "run", "--profile", "CI_SMOKE",
                                     "--run-id", "fixture-invalid-environment", "--output", str(root / "run"),
                                     "--candidate", "0" * 40, "--environment-json", str(environment)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            path = root / "run" / "index.json"
            self.assertTrue(path.exists(), "Suite/environment构造前必须预写完整index")
            data = json.loads(path.read_text())
            self.assertEqual(len(data["expectedKeys"]), 17)
            self.assertEqual(len(data["results"]), 17)
            self.assertEqual(data["results"][0]["outcome"], "environment_failed")
            self.assertTrue(all(r["outcome"] == "not_run" for r in data["results"][1:]))
            self.assertNotEqual(data["state"], "completed")


if __name__ == "__main__":
    unittest.main()
