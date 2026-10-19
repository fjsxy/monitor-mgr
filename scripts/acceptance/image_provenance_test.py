#!/usr/bin/env python3
"""通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。"""
import hashlib
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

from image_provenance import ImageProofError, read_docker_archive


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
