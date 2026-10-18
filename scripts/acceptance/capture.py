#!/usr/bin/env python3
"""在公开 console 前捕获子进程原字节；公开信息仅有固定 ID、exit 与 hash。"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import uuid


def capture(argv, *, private_root, label, cwd=None, env=None, timeout=None, publish=True):
    """执行真正子进程；原 stdout/stderr 从第一字节起即写入 0600 文件。"""
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,80}', label):
        raise ValueError('CAPTURE_LABEL_INVALID')
    root = pathlib.Path(private_root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('CAPTURE_ROOT_INVALID')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    prefix = label + '-' + uuid.uuid4().hex
    paths = [root / (prefix + '.' + stream + '.raw') for stream in ('stdout', 'stderr')]
    handles = [os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') for path in paths]
    started = time.time()
    process = None
    previous = {}
    signals = []
    timed_out = False
    try:
        process = subprocess.Popen(argv, stdout=handles[0], stderr=handles[1], cwd=cwd, env=env, start_new_session=True)
        def forward(number, frame):
            signals.append(number)
            # 关键：只转发首次信号，重复信号只留记录，避免再次杀掉正在 finally 的清理进程。
            if len(signals) > 1:
                return
            try:
                os.killpg(process.pid, number)
            except ProcessLookupError:
                pass
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, forward)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    except OSError:
        # 启动失败也只公开固定 exit；异常中的 argv/路径可能含私有值。
        code = 127
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        for handle in handles:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
    child_exit = process.returncode if process is not None else None
    if signals and code == 0:
        code = 128 + signals[0]
    receipt = {'id': label, 'exit': code, 'childExit': child_exit, 'timedOut': timed_out, 'startedAt': started, 'finishedAt': time.time(),
               'signals': signals, 'raw': [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                          'bytes': path.stat().st_size} for path in paths]}
    receipt_path = root / (prefix + '.json')
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    if publish:
        print(json.dumps({'id': label, 'exit': code, 'stdoutBytes': receipt['raw'][0]['bytes'], 'stderrBytes': receipt['raw'][1]['bytes']}), flush=True)
    return subprocess.CompletedProcess(argv, code, paths[0].read_bytes(), paths[1].read_bytes())


def main():
    parser = argparse.ArgumentParser(description='受保护子进程输出捕获')
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('必须有明确命令')
    try:
        result = capture(command, private_root=args.private_root, label=args.label)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (ValueError, OSError):
        print(json.dumps({'id': 'capture', 'exit': 125}), flush=True)
        return 125


if __name__ == '__main__':
    sys.exit(main())
