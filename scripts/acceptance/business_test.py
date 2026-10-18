"""原业务异常的私有留存与公开诊断边界；不建立产品通过键。"""
import json
import pathlib
import stat
import tempfile
import types
import unittest

import business


class BusinessFailureBoundary(unittest.TestCase):
    def test_original_exception_is_private_and_public_summary_has_no_message_or_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            session=types.SimpleNamespace(private=pathlib.Path(directory))
            marker='SYNTHETIC-PRIVATE-FAILURE'
            try:
                exec(compile("raise RuntimeError('SYNTHETIC-PRIVATE-FAILURE')",'/private-source/credential.py','exec'))
            except RuntimeError as failure:
                result=business.capture_failure(session,failure)
                safe=result['errorDiagnostics']
            original=session.private/'business-exception.json'
            self.assertEqual(stat.S_IMODE(original.stat().st_mode),0o600)
            value=json.loads(original.read_text())
            self.assertIn(marker,value['traceback'])
            self.assertIn('/private-source/credential.py',value['traceback'])
            self.assertEqual(safe['exceptionType'],'RuntimeError')
            self.assertTrue(safe['repositoryFrames'])
            self.assertNotIn(marker,json.dumps(safe))
            self.assertNotIn('/private-source',json.dumps(safe))
            self.assertTrue(all(not pathlib.Path(v['file']).is_absolute() and '..' not in pathlib.Path(v['file']).parts
                                for v in safe['repositoryFrames']))

    def test_private_capture_oserror_keeps_primary_failure_code_type_and_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            private=pathlib.Path(directory)/'not-a-directory';private.write_text('original-file')
            session=types.SimpleNamespace(private=private)
            class OriginalFailure(RuntimeError):code='ORIGINAL_BUSINESS_CODE'
            try:raise OriginalFailure('SYNTHETIC-PRIVATE-FAILURE')
            except OriginalFailure as failure:result=business.capture_failure(session,failure)
            self.assertEqual(result['errorCode'],'ORIGINAL_BUSINESS_CODE')
            safe=result['errorDiagnostics']
            self.assertIs(safe['privateCaptureSucceeded'],False)
            self.assertEqual(safe['diagnosticErrorCode'],'private-exception-capture-failed')
            self.assertEqual(safe['exceptionType'],'OriginalFailure')
            self.assertTrue(safe['repositoryFrames'])
            self.assertNotIn('SYNTHETIC-PRIVATE-FAILURE',json.dumps(result))
            self.assertNotIn(str(private),json.dumps(result))
            self.assertEqual(private.read_text(),'original-file')


if __name__=='__main__':unittest.main()
