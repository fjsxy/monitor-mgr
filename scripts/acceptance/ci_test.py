#!/usr/bin/env python3
"""CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import yaml

from ci import write_request
from fetch_source import fetch_source


class CIProtocolTest(unittest.TestCase):
    def test_bootstrap_reports_are_uploaded_always_before_request_without_raw(self):
        workflow=yaml.safe_load((pathlib.Path(__file__).resolve().parents[2]/'.github/workflows/phase1.yml').read_text())
        for job in ['fast','smoke']:
            with self.subTest(job=job):
                steps=workflow['jobs'][job]['steps']
                bootstrap=[step for step in steps if 'bootstrap_linux.py' in step.get('run','')]
                self.assertEqual(len(bootstrap),1)
                self.assertIn('--public-report "$BOOTSTRAP_REPORT"',bootstrap[0]['run'])
                artifacts=[step for step in steps if step.get('with',{}).get('path')=='${{ runner.temp }}/phase1-bootstrap-public.json']
                self.assertEqual(len(artifacts),1)
                self.assertEqual(artifacts[0]['if'],'always()')
                self.assertTrue(artifacts[0]['uses'].startswith('actions/upload-artifact@'))
                self.assertEqual(artifacts[0]['with']['if-no-files-found'],'error')
                self.assertLess(steps.index(bootstrap[0]),steps.index(artifacts[0]))
                self.assertLess(steps.index(artifacts[0]),next(i for i,step in enumerate(steps) if step.get('id')=='request'))
                self.assertIn('BOOTSTRAP_REPORT=$RUNNER_TEMP/phase1-bootstrap-public.json',steps[0]['run'])
                self.assertNotIn('raw',artifacts[0]['with']['path']);self.assertNotIn('*',artifacts[0]['with']['path'])

    def test_smoke_request_contains_one_control_no_data(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            request = write_request(profile='CI_SMOKE', run_id='protocol-smoke', candidate='a' * 40,
                                    private_root=str(root / 'private'), destination=str(root / 'environment.json'),
                                    monitor_op_source=str(root / 'private/monitor-op'))
            self.assertEqual(set(request['targets']), {'control'})
            self.assertEqual(request['platform'], 'linux/amd64')
            self.assertEqual(request['lifecycle'], 'prepare')
            self.assertEqual((root / 'environment.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(request['targets']['control']['context'], 'kind-mm21-protocol-smoke-control')

    def test_fast_request_has_no_private_source_or_cluster(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            request = write_request(profile='CI_FAST', run_id='protocol-fast', candidate='b' * 40,
                                    private_root=str(root / 'private'), destination=str(root / 'environment.json'))
            self.assertEqual(request['targets'], {})
            self.assertNotIn('monitorOpSource', request)

    def test_untrusted_repository_is_rejected_before_git_or_key_write(self):
        with tempfile.TemporaryDirectory() as root:
            command = [sys.executable, str(pathlib.Path(__file__).with_name('fetch_source.py')),
                       '--destination', str(pathlib.Path(root) / 'source'), '--private-root', str(pathlib.Path(root) / 'private')]
            process = subprocess.run(command, env={'PATH': os.environ['PATH'], 'MONITOR_OP_SOURCE_REPOSITORY': 'synthetic/untrusted',
                                     'MONITOR_OP_DEPLOY_KEY': 'SYNTHETIC-NOT-A-REAL-KEY'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(process.returncode, 1)
            self.assertNotIn(b'SYNTHETIC-NOT-A-REAL-KEY', process.stdout + process.stderr)
            self.assertEqual(list(pathlib.Path(root).iterdir()), [])

    def test_private_fetch_failure_removes_synthetic_key_and_captures_git_output(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root / 'bin').mkdir()
            git = root / 'bin/git'
            git.write_text('#!/bin/sh\nprintf SYNTHETIC-PRIVATE-GIT-OUTPUT\nexit 9\n')
            git.chmod(0o755)
            command = [sys.executable, str(pathlib.Path(__file__).with_name('fetch_source.py')),
                       '--destination', str(root / 'source'), '--private-root', str(root / 'private')]
            process = subprocess.run(command, env={'PATH': str(root / 'bin'), 'MONITOR_OP_SOURCE_REPOSITORY': 'fjsxy/monitor-in-hand',
                                     'MONITOR_OP_DEPLOY_KEY': 'SYNTHETIC-NOT-A-REAL-KEY'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(process.returncode, 1)
            self.assertNotIn(b'SYNTHETIC', process.stdout + process.stderr)
            self.assertFalse((root / 'private/monitor-op-deploy-key').exists())
            self.assertTrue(any(b'SYNTHETIC-PRIVATE-GIT-OUTPUT' in file.read_bytes() for file in (root / 'private/raw').glob('*.raw')))



if __name__ == '__main__':
    unittest.main()
