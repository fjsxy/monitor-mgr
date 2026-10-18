#!/usr/bin/env python3
"""Actions 只负责生成显式环境请求；实际运行、锁、验证与恢复归主入口。"""
import argparse
import json
import os
import pathlib
import re
import sys


def write_request(*, profile, run_id, candidate, private_root, destination, monitor_op_source=None):
    """FULL/SMOKE 拓扑在 prepare 中再次核验；FAST 明确不含 data 或私有源。"""
    if profile not in ('CI_FAST', 'CI_SMOKE') or not re.fullmatch('[a-f0-9]{40}', candidate) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,53}', run_id):
        raise ValueError('CI_REQUEST_INVALID')
    root = pathlib.Path(private_root)
    if not root.is_absolute():
        raise ValueError('CI_PRIVATE_ROOT_INVALID')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    request = {'schemaVersion': 1, 'profile': profile, 'runId': run_id, 'candidate': candidate, 'source': candidate,
               'platform': 'linux/amd64', 'lifecycle': 'prepare', 'privateRoot': str(root),
               'kubectl': 'kubectl', 'kind': 'kind', 'toolchainRecord': str(root / 'bootstrap.json'), 'apiLane': str(root / 'api-lane.lock'), 'targets': {}}
    if profile == 'CI_SMOKE':
        source = pathlib.Path(monitor_op_source or '')
        if not source.is_absolute():
            raise ValueError('CI_OPERATOR_SOURCE_REQUIRED')
        name = 'mm21-' + run_id + '-control'
        if len(name) > 53:
            raise ValueError('CI_CLUSTER_NAME_TOO_LONG')
        request.update(monitorOpSource=str(source), targets={'control': {
            'name': name, 'context': 'kind-' + name, 'kubeconfig': str(root / 'control.kubeconfig')}})
    path = pathlib.Path(destination)
    if not path.is_absolute():
        raise ValueError('CI_ENVIRONMENT_PATH_INVALID')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(request, stream, indent=2)
        stream.write('\n')
    return request


def main():
    parser = argparse.ArgumentParser(description='生成私有 CI 环境请求，不运行集群命令')
    parser.add_argument('--profile', choices=['CI_FAST', 'CI_SMOKE'], required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--monitor-op-source')
    args = parser.parse_args()
    try:
        write_request(profile=args.profile, run_id=args.run_id, candidate=args.candidate, private_root=args.private_root,
                      destination=args.destination, monitor_op_source=args.monitor_op_source)
        summary = {'id': 'ci-request', 'exit': 0}
    except (ValueError, OSError):
        summary = {'id': 'ci-request', 'exit': 1}
    print(json.dumps(summary), flush=True)
    return summary['exit']


if __name__ == '__main__':
    sys.exit(main())
