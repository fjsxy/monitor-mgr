#!/usr/bin/env python3
"""为 CI 创建锁定 BuildKit 的独占构建器，并按记录的容器身份清理。"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
RECORD=ROOT/'.scratch/ci-builder.json'


def run(*args, capture=False, check=True):
    return subprocess.run(['docker',*args],capture_output=capture,text=True,check=check)


def container_id(name):
    result=run('inspect','--format','{{.Id}}','buildx_buildkit_'+name+'0',capture=True,check=False)
    return result.stdout.strip() if result.returncode==0 else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','cleanup']);parser.add_argument('--name',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'mm-[a-z0-9-]{1,50}',args.name):raise ValueError('构建器必须使用本 run 独占 mm- 名称')
    if args.action=='create':
        lock=json.loads((ROOT/'scripts/acceptance/source-lock.json').read_text())['images']['buildkit']
        if RECORD.exists() or run('buildx','inspect',args.name,capture=True,check=False).returncode==0:
            raise ValueError('不接管已有构建器')
        run('buildx','create','--name',args.name,'--driver','docker-container','--driver-opt','image='+lock['image'])
        RECORD.parent.mkdir(parents=True,exist_ok=True)
        record=dict(name=args.name,containerID=None,image=lock['image']);RECORD.write_text(json.dumps(record)+'\n')
        try:
            run('buildx','inspect',args.name,'--bootstrap')
            version=run('exec','buildx_buildkit_'+args.name+'0','buildkitd','--version',capture=True).stdout.split()
            if lock['version'] not in version:raise ValueError('实际 BuildKit 版本与锁定值不同')
        finally:
            # 关键：bootstrap 失败也保存已经创建的原容器 ID，后续不凭同名删除替代对象。
            record['containerID']=container_id(args.name);RECORD.write_text(json.dumps(record)+'\n')
    elif RECORD.exists():
        record=json.loads(RECORD.read_text())
        if record['name']!=args.name or container_id(args.name)!=record['containerID']:
            raise ValueError('构建器身份已变化，拒绝清理')
        run('buildx','rm',args.name)
        if container_id(args.name) is not None:raise ValueError('原构建器仍存在')
        RECORD.unlink()


if __name__=='__main__':main()
