#!/usr/bin/env python3
"""真实子进程验证公开 console 出口，不使用真实认证材料。"""
import json
import os
import pathlib
import signal
import time
import subprocess
import sys
import tempfile
import unittest


class CaptureProtocolTest(unittest.TestCase):
    def test_nonzero_child_keeps_private_bytes_and_safe_console(self):
        canary = 'SYNTHETIC-AUTH-CANARY-21'
        with tempfile.TemporaryDirectory() as root:
            command = [sys.executable, str(pathlib.Path(__file__).with_name('capture.py')),
                       '--private-root', root, '--label', 'protocol-canary', '--', sys.executable,
                       '-c', "import sys;print('SYNTHETIC-AUTH-CANARY-21');sys.stderr.write('SYNTHETIC-AUTH-CANARY-21');sys.exit(7)"]
            process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(process.returncode, 7)
            self.assertNotIn(canary.encode(), process.stdout + process.stderr)
            summary = json.loads(process.stdout)
            self.assertEqual(summary['exit'], 7)
            self.assertEqual(summary['id'], 'protocol-canary')
            logs = list(pathlib.Path(root).glob('*.raw'))
            self.assertEqual(len(logs), 2)
            for log in logs:
                self.assertIn(canary, log.read_text())
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_repeated_signals_do_not_interrupt_child_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            child = root / 'child.py'
            child.write_text("import signal,time,pathlib,sys\np=pathlib.Path(sys.argv[1])\ndef stop(n,f):\n with (p/'signals').open('a') as s:s.write(str(n)+'\\n')\n (p/'cleaning').touch()\nsignal.signal(signal.SIGTERM,stop)\nsignal.signal(signal.SIGINT,stop)\n(p/'ready').touch()\nwhile not (p/'finish').exists():time.sleep(.01)\nsys.exit(3)\n")
            command = [sys.executable, str(pathlib.Path(__file__).with_name('capture.py')),
                       '--private-root', str(root / 'raw'), '--label', 'protocol-signal', '--', sys.executable, str(child), str(root)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                end = time.monotonic() + 5
                while not (root / 'ready').exists() and time.monotonic() < end: time.sleep(.01)
                self.assertTrue((root / 'ready').exists())
                process.send_signal(signal.SIGTERM)
                while not (root / 'cleaning').exists() and time.monotonic() < end: time.sleep(.01)
                self.assertTrue((root / 'cleaning').exists())
                process.send_signal(signal.SIGINT)
                time.sleep(.1)
                process.send_signal(signal.SIGTERM)
                time.sleep(.1)
                (root / 'finish').touch()
                output, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 3)
                self.assertEqual(len((root / 'signals').read_text().splitlines()), 1)
                receipt = json.loads(next((root / 'raw').glob('*.json')).read_text())
                self.assertEqual(len(receipt['signals']), 3)
            finally:
                (root / 'finish').touch()
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
