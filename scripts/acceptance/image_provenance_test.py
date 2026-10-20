#!/usr/bin/env python3
"""通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。"""
import hashlib
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

from image_provenance import ImageProofError, read_docker_archive, read_oci_graph


def archive(path, source):
    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': source}}}).encode()
    digest = hashlib.sha256(config).hexdigest()
    files = {'manifest.json': json.dumps([{'Config': digest + '.json', 'RepoTags': ['example:fixture'], 'Layers': []}]).encode(), digest + '.json': config}
    with tarfile.open(path, 'w') as stream:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    return digest


class ImageProofProtocolTest(unittest.TestCase):
    def node_graph(self, source='a'*40, architecture='amd64'):
        config=json.dumps({'os':'linux','architecture':architecture,'config':{'Labels':{'org.opencontainers.image.revision':source}}}).encode()
        digest='sha256:'+hashlib.sha256(config).hexdigest()
        manifest=json.dumps({'schemaVersion':2,'config':{'digest':digest,'size':len(config)},'layers':[]}).encode()
        child='sha256:'+hashlib.sha256(manifest).hexdigest()
        index=json.dumps({'schemaVersion':2,'manifests':[{'digest':child,'size':len(manifest),'platform':{'os':'linux','architecture':architecture}}]}).encode()
        parent='sha256:'+hashlib.sha256(index).hexdigest()
        return {digest:config,child:manifest,parent:index},digest,child,parent

    def test_original_node_index_and_child_bytes_bind_frozen_config(self):
        content,digest,child,parent=self.node_graph()
        with tempfile.TemporaryDirectory() as tmp:
            proof=read_oci_graph(content.__getitem__,parent,expected_config=digest,expected_source='a'*40,
                                 platform='linux/amd64',destination=pathlib.Path(tmp)/'graph',require_manifest=True)
            self.assertEqual((proof['indexDigest'],proof['manifestDigest'],proof['configDigest']),(parent,child,digest))
            for ref in proof['evidenceRefs']:
                path=pathlib.Path(ref['path']);self.assertEqual('sha256:'+hashlib.sha256(path.read_bytes()).hexdigest(),
                    {'oci-root.raw.json':parent,'oci-manifest.raw.json':child,'config.raw.json':digest}[path.name])
                self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_node_graph_rejects_unknown_wrong_config_source_platform_bytes_and_ambiguous_index(self):
        for mode in ['bytes','missing','config-binding','source','platform','ambiguous','missing-platform','config-only']:
            with self.subTest(mode=mode):
                content,digest,child,parent=self.node_graph(source='b'*40 if mode=='source' else 'a'*40,
                                                           architecture='arm64' if mode=='platform' else 'amd64')
                root=parent
                if mode=='bytes':content[child]+=b' '
                elif mode=='missing':content.pop(child)
                elif mode=='config-binding':
                    obj=json.loads(content[child]);obj['config']['digest']='sha256:'+'e'*64
                    raw=json.dumps(obj).encode();root='sha256:'+hashlib.sha256(raw).hexdigest();content[root]=raw
                elif mode in ['ambiguous','missing-platform']:
                    obj=json.loads(content[parent]);obj['manifests']=obj['manifests']*2 if mode=='ambiguous' else []
                    raw=json.dumps(obj).encode();root='sha256:'+hashlib.sha256(raw).hexdigest();content[root]=raw
                elif mode=='config-only':root=digest
                with self.assertRaises(ImageProofError):
                    read_oci_graph(lambda key:content.get(key,b''),root,expected_config=digest,expected_source='a'*40,
                                   platform='linux/amd64',require_manifest=True)

    def test_old_actual_config_cannot_borrow_new_inspect_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            digest = archive(directory / 'image.tar', 'b' * 40)
            with self.assertRaisesRegex(ImageProofError, 'IMAGE_ACTUAL_CONFIG_SOURCE_MISMATCH'):
                read_docker_archive(directory / 'image.tar', image='example:fixture', expected_source='a' * 40,
                                    platform='linux/amd64', inspect={'Id': 'sha256:' + digest,
                                      'Config': {'Labels': {'org.opencontainers.image.revision': 'a' * 40}}},
                                    destination=directory / 'proof')

    def test_raw_config_bytes_determine_config_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            digest = archive(directory / 'image.tar', 'a' * 40)
            proof = read_docker_archive(directory / 'image.tar', image='example:fixture', expected_source='a' * 40,
                                       platform='linux/amd64', inspect={'Id': 'sha256:' + digest}, destination=directory / 'proof')
            self.assertEqual(proof['configDigest'], 'sha256:' + digest)
            self.assertEqual(proof['source'], 'a' * 40)
            for ref in proof['evidenceRefs']:
                self.assertEqual(hashlib.sha256(pathlib.Path(ref['path']).read_bytes()).hexdigest(), ref['sha256'])

    def test_docker29_manifest_id_is_not_confused_with_config_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            config = b'{"os":"linux","architecture":"amd64","config":{"Labels":{"org.opencontainers.image.revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}'
            config_digest = hashlib.sha256(config).hexdigest()
            manifest = json.dumps({'schemaVersion': 2, 'config': {'digest': 'sha256:' + config_digest}, 'layers': []}).encode()
            manifest_digest = hashlib.sha256(manifest).hexdigest()
            files = {'manifest.json': json.dumps([{'Config': 'blobs/sha256/' + config_digest, 'RepoTags': ['example:fixture'], 'Layers': []}]).encode(),
                     'blobs/sha256/' + config_digest: config, 'blobs/sha256/' + manifest_digest: manifest}
            with tarfile.open(directory / 'image.tar', 'w') as stream:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    stream.addfile(member, io.BytesIO(data))
            proof = read_docker_archive(directory / 'image.tar', image='example:fixture', expected_source='a' * 40,
                                       platform='linux/amd64', inspect={'Id': 'sha256:' + manifest_digest,
                                       'Descriptor': {'digest': 'sha256:' + manifest_digest}}, destination=directory / 'proof')
            self.assertEqual(proof['configDigest'], 'sha256:' + config_digest)
            self.assertEqual(proof['manifestDigest'], 'sha256:' + manifest_digest)
            self.assertNotEqual(proof['configDigest'], proof['dockerInspectId'])


if __name__ == '__main__': unittest.main()
