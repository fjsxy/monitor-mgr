#!/usr/bin/env python3
"""从 Docker archive 原始元数据核实 config；不提取或公开 layer。"""
import hashlib
import json
import os
import pathlib
import re
import tarfile
import uuid


class ImageProofError(RuntimeError):
    """固定错误码避免异常夹带私有配置。"""


def read_oci_graph(read, root_digest, *, expected_config, expected_source, platform, destination=None, require_manifest=False):
    """核实际内容index/manifest→原config；read只读原node字节，不能由digest声明构造元数据。"""
    originals = {}
    def content(digest, name):
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ImageProofError('IMAGE_OCI_DIGEST_INVALID')
        raw = read(digest)
        if not isinstance(raw, bytes) or 'sha256:' + hashlib.sha256(raw).hexdigest() != digest:
            raise ImageProofError('IMAGE_OCI_CONTENT_DIGEST_MISMATCH')
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            raise ImageProofError('IMAGE_METADATA_MISSING') from None
        if not isinstance(obj, dict):
            raise ImageProofError('IMAGE_METADATA_MEMBER_INVALID')
        originals[name] = raw
        return obj, len(raw)
    config, config_size = content(expected_config, 'config.raw.json')
    os_name, architecture = platform.split('/')
    if config.get('os') != os_name or config.get('architecture') != architecture:
        raise ImageProofError('IMAGE_ACTUAL_CONFIG_PLATFORM_MISMATCH')
    labels = config.get('config', {}).get('Labels', {}) if isinstance(config.get('config'), dict) else {}
    if expected_source is not None and (not isinstance(labels, dict) or labels.get('org.opencontainers.image.revision') != expected_source):
        raise ImageProofError('IMAGE_ACTUAL_CONFIG_SOURCE_MISMATCH')
    manifest_digest = index_digest = None
    if root_digest != expected_config:
        manifest, _ = content(root_digest, 'oci-root.raw.json')
        manifest_digest = root_digest
        if 'manifests' in manifest:
            index_digest = root_digest
            items = manifest['manifests']
            if not isinstance(items, list) or any(not isinstance(v, dict) or not isinstance(v.get('platform', {}), dict) for v in items):
                raise ImageProofError('IMAGE_OCI_PLATFORM_AMBIGUOUS')
            selected = [v for v in items if v.get('platform', {}).get('os') == os_name and v['platform'].get('architecture') == architecture]
            if manifest.get('schemaVersion') != 2 or len(selected) != 1:
                raise ImageProofError('IMAGE_OCI_PLATFORM_AMBIGUOUS')
            manifest_digest = selected[0].get('digest')
            manifest, size = content(manifest_digest, 'oci-manifest.raw.json')
            if selected[0].get('size') != size:
                raise ImageProofError('IMAGE_METADATA_MEMBER_INVALID')
        descriptor = manifest.get('config')
        if (manifest.get('schemaVersion') != 2 or not isinstance(manifest.get('layers'), list) or not isinstance(descriptor, dict) or
                descriptor.get('digest') != expected_config or descriptor.get('size') != config_size):
            raise ImageProofError('IMAGE_OCI_CONFIG_BINDING_MISMATCH')
    elif require_manifest:
        raise ImageProofError('IMAGE_OCI_DESCRIPTOR_MISSING')
    refs = []
    if destination is not None:
        destination = pathlib.Path(destination)
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        for name, raw in originals.items():
            path = destination / name
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            refs.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    return dict(configDigest=expected_config, manifestDigest=manifest_digest, indexDigest=index_digest,
                source=expected_source, platform=platform, os=os_name, architecture=architecture,
                originalContentBytesVerified=True, evidenceRefs=refs)


def source_config_bytes(descriptor, image):
    """原Docker config只在私有根回读；私有Operator的完整JSON不进入公开证据。"""
    proof = descriptor['imageProofs'][image]
    revision = descriptor['monitorOpCommit'] if image == descriptor.get('monitorOpImage') else descriptor['source']
    if (proof.get('source') != revision or proof.get('platform') != descriptor['platform'] or
            proof.get('originalContentBytesVerified') is not True):
        raise ImageProofError('IMAGE_HOST_SOURCE_PROOF_MISMATCH')
    root = pathlib.Path(descriptor['privateRoot']).absolute()
    refs = [ref for ref in proof.get('evidenceRefs', []) if 'sha256:' + ref.get('sha256', '') == proof['configDigest']]
    if len(refs) != 1:raise ImageProofError('IMAGE_HOST_CONFIG_MISSING')
    path = pathlib.Path(refs[0]['path'])
    if not path.is_absolute() or not path.is_relative_to(root):raise ImageProofError('IMAGE_HOST_CONFIG_MISSING')
    current = root
    for part in path.relative_to(root).parts:
        if part in ('.', '..'):raise ImageProofError('IMAGE_HOST_CONFIG_MISSING')
        current = current / part
        if current.is_symlink():raise ImageProofError('IMAGE_HOST_CONFIG_MISSING')
    raw = path.read_bytes()
    if 'sha256:' + hashlib.sha256(raw).hexdigest() != proof['configDigest']:
        raise ImageProofError('IMAGE_HOST_CONFIG_CHANGED')
    config = json.loads(raw)
    if (config.get('os', '') + '/' + config.get('architecture', '') != descriptor['platform'] or
            config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != revision):
        raise ImageProofError('IMAGE_HOST_SOURCE_PROOF_MISMATCH')
    return raw


def runtime_image_proof(descriptor, role, pod, container, status, node_object, *, read, lock, output=None):
    """原node64ID、Pod/container与实际CRI config→原bytes闭环；repoDigest声明不能自证。"""
    def digest(value):
        match = re.fullmatch(r'(?:[^\s@]+@|[a-z][a-z0-9+.-]*://)?(sha256:[0-9a-f]{64})', value) if isinstance(value, str) else None
        return match[1] if match else None
    def uid(value):
        return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) else None
    image = container.get('image');reference_hash = hashlib.sha256(str(image).encode()).hexdigest()
    observed = dict(schemaVersion=1, kind='runtime-image-identity', productGate=False, verified=False, stage='pod',
        podUID=uid(pod.get('metadata', {}).get('uid')), nodeUID=uid(node_object.get('metadata', {}).get('uid')),
        imageReferenceSHA256=reference_hash, imageID=digest(status.get('imageID')))
    path = None
    if output is not None:
        path = pathlib.Path(output) / 'runtime-image-identities' / (uuid.uuid4().hex + '.json')
    def checkpoint():
        if path is not None:
            from session import atomic_json
            atomic_json(path, observed)
    def require(ok, code):
        if not ok:raise ImageProofError(code)
    checkpoint()
    try:
        target = descriptor['targets'][role];node_name = pod.get('spec', {}).get('nodeName')
        nodes = [n for n in target['containers'] if n['name'] == node_name]
        topology = [n for n in target['nodeTopology'] if n['name'] == node_name]
        require(len(nodes) == len(topology) == 1 and node_object.get('metadata', {}).get('name') == node_name and
                observed['nodeUID'] == topology[0]['uid'], 'IMAGE_RUNTIME_NODE_UID_MISMATCH')
        info = node_object.get('status', {}).get('nodeInfo', {})
        require(info.get('operatingSystem', '') + '/' + info.get('architecture', '') == descriptor['platform'],
                'IMAGE_RUNTIME_NODE_PLATFORM_MISMATCH')
        node = nodes[0];node_id = node.get('id', '')
        require(bool(re.fullmatch('[0-9a-f]{64}', node_id)), 'IMAGE_RUNTIME_NODE_ID_MISMATCH')
        observed.update(nodeId=node_id, stage='node');checkpoint()
        require(read(['docker', 'inspect', '--format', '{{.Id}}', node_id]).strip() == node_id.encode(), 'IMAGE_RUNTIME_NODE_ID_MISMATCH')
        official = [value for value in lock['images'].values() if image in (value.get('image'), value.get('tag'))]
        local = {descriptor.get('image'), descriptor.get('monitorOpImage'), *descriptor.get('fixtureImages', {}).values()}
        require(len(official) == 1 or image in local, 'IMAGE_RUNTIME_REFERENCE_NOT_LOCKED')
        require(status.get('name') == container.get('name') and status.get('ready') is True and
                any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])),
                'IMAGE_RUNTIME_POD_NOT_READY')
        cid = re.fullmatch('containerd://([0-9a-f]{64})', status.get('containerID', ''))
        require(cid is not None and observed['podUID'] is not None, 'IMAGE_RUNTIME_CONTAINER_ID_MISMATCH')
        observed.update(containerID=cid[1], image=image, stage='cri');checkpoint()
        actual = json.loads(read(['docker', 'exec', node_id, 'crictl', 'inspect', cid[1]]));actual_status = actual['status']
        actual_config = actual['info']['config']['image']['image']
        observed.update(actualConfigDigest=digest(actual_config), actualImageRef=digest(actual_status.get('imageRef')));checkpoint()
        require(actual_status.get('id') == cid[1] and actual_status.get('metadata', {}).get('name') == container['name'] and
                actual_status.get('labels', {}).get('io.kubernetes.pod.uid') == observed['podUID'] and
                actual_status.get('state') == 'CONTAINER_RUNNING', 'IMAGE_RUNTIME_CONTAINER_IDENTITY_MISMATCH')
        require(observed['imageID'] is not None and observed['imageID'] == observed['actualImageRef'], 'IMAGE_RUNTIME_IMAGE_REF_MISMATCH')
        read_content = lambda key: read(['docker', 'exec', node_id, 'ctr', '--namespace', 'k8s.io', 'content', 'get', key])
        if official:
            locked = official[0]
            proofs = [v for v in descriptor['officialContent'].values() if v['image'] == locked['image']]
            require(len(proofs) == 1, 'IMAGE_RUNTIME_OFFICIAL_PROOF_MISSING')
            proof = proofs[0];expected_config = proof['configDigest'];revision = None
            require(proof.get('indexDigest') == locked['indexDigest'] and proof.get('platform') == descriptor['platform'], 'IMAGE_RUNTIME_OFFICIAL_LOCK_MISMATCH')
            architecture = descriptor['platform'].split('/')[1]
            require(not locked.get(architecture + 'Config') or (expected_config == locked[architecture + 'Config'] and
                    proof['manifestDigest'] == locked[architecture + 'Manifest']), 'IMAGE_RUNTIME_OFFICIAL_LOCK_MISMATCH')
            root_digest = locked['indexDigest']
        else:
            require(image != descriptor.get('monitorOpImage') or descriptor.get('monitorOpCommit') == lock['monitorOp']['source'],
                    'IMAGE_HOST_SOURCE_PROOF_MISMATCH')
            source_config_bytes(descriptor, image)
            proof = descriptor['imageProofs'][image];expected_config = proof['configDigest'];revision = proof['source']
            root_digest = observed['imageID']
        observed.update(expectedConfigDigest=expected_config, stage='content');checkpoint()
        require(actual_config == expected_config, 'IMAGE_RUNTIME_CONFIG_MISMATCH')
        graph = read_oci_graph(read_content, root_digest, expected_config=expected_config, expected_source=revision,
                               platform=descriptor['platform'], require_manifest=bool(official))
        require(not official or (graph['manifestDigest'] == proof['manifestDigest'] and observed['imageID'] in
                {graph['indexDigest'], graph['manifestDigest'], expected_config}), 'IMAGE_RUNTIME_OFFICIAL_CHAIN_MISMATCH')
        observed.update(verified=True, stage='verified', configDigest=expected_config, manifestDigest=graph['manifestDigest'],
            indexDigest=graph['indexDigest'], source=revision, platform=descriptor['platform'], actualRuntimeConfigVerified=True,
            originalContentBytesVerified=True);checkpoint()
        return dict(observed)
    except Exception as error:
        observed['errorCode'] = error.args[0] if isinstance(error, ImageProofError) else 'IMAGE_RUNTIME_READ_FAILED'
        checkpoint()
        if isinstance(error, ImageProofError):raise
        raise ImageProofError('IMAGE_RUNTIME_READ_FAILED') from None


def read_docker_archive(path, *, image, expected_source, platform, inspect, destination):
    """原 Docker-save/OCI graph 与 config 字节是来源依据，inspect.Id 仅记录观测。"""
    refs = []
    destination = pathlib.Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    def save(name, data):
        target = destination / name
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(data)
        refs.append({'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    with tarfile.open(path) as archive:
        def read(name):
            try:
                member = archive.getmember(name)
                if not member.isfile() or member.size > 32 * 1024 * 1024:
                    raise ImageProofError('IMAGE_METADATA_MEMBER_INVALID')
                return archive.extractfile(member).read()
            except (KeyError, AttributeError):
                raise ImageProofError('IMAGE_METADATA_MISSING') from None
        manifest_bytes = read('manifest.json')
        entries = json.loads(manifest_bytes)
        if len(entries) != 1 or image not in entries[0].get('RepoTags', []):
            raise ImageProofError('IMAGE_DOCKER_MANIFEST_AMBIGUOUS')
        config_bytes = read(entries[0]['Config'])
        config_digest = 'sha256:' + hashlib.sha256(config_bytes).hexdigest()
        config = json.loads(config_bytes)
        source = config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision')
        if source != expected_source:
            raise ImageProofError('IMAGE_ACTUAL_CONFIG_SOURCE_MISMATCH')
        if config.get('os') + '/' + config.get('architecture') != platform:
            raise ImageProofError('IMAGE_ACTUAL_CONFIG_PLATFORM_MISMATCH')
        config_filename_digest = pathlib.PurePosixPath(entries[0]['Config']).name.removesuffix('.json')
        if re.fullmatch('[a-f0-9]{64}', config_filename_digest) and config_filename_digest != config_digest.split(':', 1)[1]:
            raise ImageProofError('IMAGE_CONFIG_MEMBER_DIGEST_MISMATCH')
        save('docker-manifest.raw.json', manifest_bytes)
        save('config.raw.json', config_bytes)
        descriptor_digest = inspect.get('Descriptor', {}).get('digest')
        manifest_digest = None
        index_digest = None
        if descriptor_digest:
            def content(digest, name):
                if not re.fullmatch('sha256:[a-f0-9]{64}', digest):
                    raise ImageProofError('IMAGE_OCI_DIGEST_INVALID')
                data = read('blobs/sha256/' + digest.split(':', 1)[1])
                if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
                    raise ImageProofError('IMAGE_OCI_CONTENT_DIGEST_MISMATCH')
                save(name, data)
                return json.loads(data)
            descriptor = content(descriptor_digest, 'oci-root.raw.json')
            manifest_digest = descriptor_digest
            if 'manifests' in descriptor:
                index_digest = descriptor_digest
                os_name, architecture = platform.split('/')
                selected = [item for item in descriptor['manifests'] if item.get('platform', {}).get('os') == os_name
                            and item.get('platform', {}).get('architecture') == architecture]
                if len(selected) != 1:
                    raise ImageProofError('IMAGE_OCI_PLATFORM_AMBIGUOUS')
                manifest_digest = selected[0]['digest']
                descriptor = content(manifest_digest, 'oci-manifest.raw.json')
            if descriptor.get('config', {}).get('digest') != config_digest:
                raise ImageProofError('IMAGE_OCI_CONFIG_BINDING_MISMATCH')
        elif inspect.get('Id') != config_digest:
            raise ImageProofError('IMAGE_OCI_DESCRIPTOR_MISSING')
    return {'image': image, 'dockerInspectId': inspect.get('Id'), 'configDigest': config_digest,
            'manifestDigest': manifest_digest, 'indexDigest': index_digest, 'source': source,
            'platform': platform, 'os': config['os'], 'architecture': config['architecture'],
            'configDigestMethod': 'SHA256 of original docker-save Config bytes', 'evidenceRefs': refs,
            'originalContentBytesVerified': True}
