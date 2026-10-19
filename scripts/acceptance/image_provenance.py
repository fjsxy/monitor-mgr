#!/usr/bin/env python3
"""从 Docker archive 原始元数据核实 config；不提取或公开 layer。"""
import hashlib
import json
import os
import pathlib
import re
import tarfile


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
    if not isinstance(labels, dict) or labels.get('org.opencontainers.image.revision') != expected_source:
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
