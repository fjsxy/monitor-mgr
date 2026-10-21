#!/usr/bin/env python3
"""CI 只安装 source-lock 中固定且经 SHA256 校验的 Helm Linux amd64。"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request
lock=json.loads((Path(__file__).parent/'acceptance/source-lock.json').read_text())['tools']['Helm']
raw=urllib.request.urlopen(lock['url'],timeout=90).read()
assert hashlib.sha256(raw).hexdigest()==lock['sha256'],'Helm archive digest mismatch'
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
    binary=archive.extractfile('linux-amd64/helm').read()
path=Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(binary);path.chmod(0o755)
