#!/usr/bin/env python3
"""来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。"""
import hashlib
import io
import pathlib
import tarfile
import tempfile
import unittest

from bootstrap_linux import BootstrapError, extract_tar, verify_file, apt_policy_summary


class BootstrapProtocolTest(unittest.TestCase):
    def test_download_mismatch_rejects_actual_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / 'asset'
            path.write_bytes(b'changed bytes')
            with self.assertRaisesRegex(BootstrapError, 'BOOTSTRAP_DIGEST_MISMATCH'):
                verify_file(path, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            path.write_bytes(b'abc')
            self.assertEqual(verify_file(path, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'), 3)

    def test_unsafe_archive_cannot_escape_install_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            archive = root / 'unsafe.tar'
            with tarfile.open(archive, 'w') as stream:
                member = tarfile.TarInfo('../outside')
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(BootstrapError, 'BOOTSTRAP_ARCHIVE_UNSAFE'):
                extract_tar(archive, root / 'install')
            self.assertFalse((root / 'outside').exists())

    def test_apt_policy_keeps_suite_candidate_and_excludes_url_credentials(self):
        summary = apt_policy_summary('libnss3:\n  Installed: 1:2.3\n  Candidate: 1:2.4\n  Version table:\n     1:2.4 500\n        500 https://user:SYNTHETIC@archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages\n')
        self.assertEqual(summary[0]['package'], 'libnss3')
        self.assertEqual(summary[0]['candidate'], '1:2.4')
        self.assertEqual(summary[0]['origins'], [{'host': 'archive.ubuntu.com', 'suite': 'noble-updates', 'component': 'main'}])
        self.assertNotIn('SYNTHETIC', str(summary))


if __name__ == '__main__':
    unittest.main()
