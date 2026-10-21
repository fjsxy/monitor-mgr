"""通过主机 HTTPS 取得锁定的 Docker Hub OCI 输入，BuildKit 使用原字节构建，避免 daemon 的 registry 元数据阻塞。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

ACCEPT='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'


def pinned_layout(spec, architecture, destination):
    """关键：original index→唯一平台 manifest→config/layers 逐字节验 hash，不能用可变 tag 冒充锁定来源。"""
    destination=Path(destination);blobs=destination/'blobs/sha256';blobs.mkdir(parents=True,exist_ok=True)
    os.chmod(destination,0o700)
    repository='library/'+spec['tag'].split(':',1)[0]
    token=json.load(urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:'+repository+':pull',timeout=30))['token']
    headers={'Authorization':'Bearer '+token,'Accept':ACCEPT}
    base='https://registry-1.docker.io/v2/'+repository+'/'
    def fetch(digest,kind):
        path=blobs/digest.removeprefix('sha256:')
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==path.name:return path.read_bytes()
        for attempt in range(3):
            temporary=path.with_suffix('.partial')
            try:
                request=urllib.request.Request(base+kind+'/'+digest,headers=headers)
                sha=hashlib.sha256()
                with urllib.request.urlopen(request,timeout=90) as response,temporary.open('wb') as stream:
                    while chunk:=response.read(1024*1024):sha.update(chunk);stream.write(chunk)
                if 'sha256:'+sha.hexdigest()!=digest:raise ValueError('OCI input digest mismatch')
                temporary.replace(path);return path.read_bytes()
            except Exception:
                temporary.unlink(missing_ok=True)
                if attempt==2:raise
                time.sleep(attempt+1)
    original=fetch(spec['indexDigest'],'manifests');index=json.loads(original)
    selected=[m for m in index['manifests'] if m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')==architecture]
    assert len(selected)==1,'必须唯一匹配目标架构'
    child=selected[0];manifest=json.loads(fetch(child['digest'],'manifests'))
    config=json.loads(fetch(manifest['config']['digest'],'blobs'))
    assert config['os']=='linux' and config['architecture']==architecture
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda descriptor:fetch(descriptor['digest'],'blobs'),manifest['layers']))
    (destination/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
    descriptor=dict(child);descriptor['annotations']={'org.opencontainers.image.ref.name':'locked'}
    (destination/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[descriptor]}))
    (destination/'provenance.json').write_text(json.dumps({'lockedIndex':spec['indexDigest'],'platformManifest':child['digest'],'configDigest':manifest['config']['digest'],'platform':'linux/'+architecture}))
    return 'oci-layout://'+str(destination)+':locked'
