#!/usr/bin/env python3
"""统一入口的 Host 协议检查；列举与失败留存不算功能子例执行。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import patch

import run as entrypoint

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
            self.assertIs(data['failureDiagnostic']['productGate'],False)
            self.assertTrue(data['failureDiagnostic']['privateCaptureSucceeded'])
            original=root/'run/.private/run-exception.json'
            self.assertEqual(original.stat().st_mode&0o777,0o600)
            self.assertIn('JSONDecodeError',original.read_text())

    def test_original_exception_is_private_and_public_index_has_no_message_or_private_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);environment=root/'HOST_PRIVATE_MISSING_ENV.json'
            result=subprocess.run([sys.executable,str(HERE/'run.py'),'run','--profile','CI_SMOKE','--run-id','fixture-private-error',
                '--output',str(root/'run'),'--candidate','0'*40,'--environment-json',str(environment)],capture_output=True)
            self.assertEqual(result.returncode,1)
            raw=root/'run/.private/run-exception.json';index=(root/'run/index.json').read_text()
            self.assertIn('HOST_PRIVATE_MISSING_ENV',raw.read_text());self.assertIn('FileNotFoundError',raw.read_text())
            self.assertNotIn('HOST_PRIVATE',index);self.assertNotIn('run-exception.json',index)
            self.assertNotIn(str(root),index);self.assertEqual(json.loads(index)['failureDiagnostic']['errorCode'],'unknown')

    def test_private_exception_write_failure_is_secondary_and_keeps_original_failure_and_full_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);atomic=entrypoint.atomic_json;output=io.StringIO()
            def write(path,value,*args,**kwargs):
                if Path(path).name=='run-exception.json':raise OSError('HOST_PRIVATE_SECONDARY_ERROR')
                return atomic(path,value,*args,**kwargs)
            args=SimpleNamespace(profile='CI_SMOKE',supplemental=False,focus=None,candidate='0'*40,
                output=str(root/'run'),run_id='fixture-private-write-failed',environment_json=str(root/'HOST_PRIVATE_MISSING'),api_lane=None,fail_fast=False)
            with patch.object(entrypoint,'atomic_json',write),contextlib.redirect_stdout(output):code=entrypoint.run_batch(args)
            data=json.loads((root/'run/index.json').read_text())
            self.assertEqual(code,1);self.assertEqual(data['failure']['type'],'FileNotFoundError')
            self.assertFalse(data['failureDiagnostic']['privateCaptureSucceeded'])
            self.assertEqual(data['failureDiagnostic']['privateCaptureErrorCode'],'private-exception-capture-failed')
            self.assertEqual(len(data['results']),17);self.assertEqual(data['results'][0]['outcome'],'environment_failed')
            self.assertTrue(all(item['outcome']=='not_run' for item in data['results'][1:]))
            self.assertNotIn('HOST_PRIVATE',json.dumps(data)+output.getvalue())


if __name__ == "__main__":
    unittest.main()
