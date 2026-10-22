#!/usr/bin/env python3
"""验收的外部进程与 Kubernetes 身份通道；原始输出始终先进入私有捕获。"""
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess as native
import threading
import time
import uuid

from session import atomic_json, stamp

ACTIVE = ContextVar("acceptance_session", default=None)


def process_identity(pid):
    """记录实际 child 的出生时间，恢复时拒绝只凭可能复用的 PID 终止进程。"""
    result = native.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="], capture_output=True, text=True)
    fields = result.stdout.strip().rsplit(None, 1)
    return fields[0].strip() if result.returncode == 0 and len(fields) == 2 and not fields[1].startswith("Z") else None


def group_members(pgid):
    """只读取 PID/进程组/出生身份，不采集可能含凭据的命令行。"""
    result = native.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=True)
    members = {}
    for line in result.stdout.splitlines():
        pid, group = map(int, line.split())
        if group == pgid:
            birth = process_identity(pid)
            if birth:
                members[pid] = birth
    return members


class ProcessFailure(RuntimeError):
    """公开异常只包含稳定标签及 exit，不包含命令参数或原始输出。"""


class ProcessRunner:
    """统一捕获真实子进程，并为辅助进程保存可核验的本次身份。"""

    def __init__(self, private_directory, *, checkpoint=None, event=None):
        self.root = Path(private_directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.checkpoint = checkpoint or (lambda: None)
        self.event = event or (lambda value: None)
        self.lock = threading.Lock()
        self.children = []

    def _capture(self, suffix):
        """文件名不编码 argv，避免 token/密码或私有路径进入公开目录列表。"""
        path = self.root / (uuid.uuid4().hex + suffix)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return path, os.fdopen(fd, "wb")

    def run(self, argv, *, label="external-process", input=None, cwd=None, env=None,
            timeout=1800, check=False, text=False, encoding=None, stdout=None, stderr=None,
            capture_output=False, defer_stop=False, cooperative_signals=None, **kwargs):
        """返回真实结果给内存调用者；不 tee，非零也不能自动打印 stderr。"""
        self.checkpoint()
        sink = stdout if hasattr(stdout, "fileno") else None
        if sink is not None:
            mode = os.fstat(sink.fileno())
            if not stat.S_ISREG(mode.st_mode) or stat.S_IMODE(mode.st_mode) & 0o077:
                raise ProcessFailure("只允许显式私有普通文件作为二进制 stdout sink")
        elif isinstance(stdout, int) and stdout >= 0:
            raise ProcessFailure("禁止将外部原始 stdout 直接转发给公开文件描述符")
        outpath, out = self._capture(".stdout")
        errpath, err = self._capture(".stderr")
        intent = {"id": uuid.uuid4().hex, "kind": "process", "label": label,
                  "stage": "intent", "at": stamp(), "auxiliary": False}
        self.event(intent)
        if isinstance(input, str):
            input = input.encode(encoding or "utf-8")
        options = dict(kwargs)
        options.pop("universal_newlines", None)
        options.setdefault("start_new_session", True)
        child = None
        timed_out = False
        interrupted = None
        try:
            child = native.Popen(argv, cwd=cwd, env=env, stdin=native.PIPE if input is not None else None,
                                 stdout=out, stderr=err, **options)
            identity = process_identity(child.pid)
            self.event({**intent, "stage": "started", "pid": child.pid, "birth": identity})
            try:
                end = time.monotonic() + timeout
                first = True
                forwarded = 0
                while True:
                    try:
                        child.communicate(input=input if first else None, timeout=max(.001, min(.1, end-time.monotonic())))
                        break
                    except native.TimeoutExpired:
                        first = False
                        if time.monotonic() >= end:
                            raise
                        if cooperative_signals is not None:
                            signals = list(cooperative_signals())
                            for event in signals[forwarded:]:
                                if child.poll() is None and process_identity(child.pid) == identity:
                                    os.kill(child.pid, event["number"])
                            forwarded = len(signals)
                        # 关键：API mutation等待原响应期间延后停止，消费方确认UID/fsync后才checkpoint。
                        if not defer_stop:
                            self.checkpoint()
            except native.TimeoutExpired:
                timed_out = True
                # 关键：新 session 的进程组仅属于这次 child；超时不能留下 Go/Node 孙进程继续写入。
                if identity and process_identity(child.pid) == identity and os.getpgid(child.pid) == child.pid:
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    raise ProcessFailure("超时进程出生身份不能确认，保留恢复失败")
                child.communicate()
            except BaseException as error:
                interrupted = error
                if child.poll() is None:
                    if not identity or process_identity(child.pid) != identity or os.getpgid(child.pid) != child.pid:
                        raise ProcessFailure("停止时进程出生身份不能确认") from None
                    members = group_members(child.pid)
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=1)
                    except native.TimeoutExpired:
                        pass
                    remaining = group_members(child.pid)
                    if remaining:
                        if not any(remaining.get(pid) == birth for pid, birth in members.items()):
                            raise ProcessFailure("停止时进程组原身份不能确认") from None
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            finally:
                out.flush(); err.flush()
                os.fsync(out.fileno()); os.fsync(err.fileno())
        finally:
            out.close(); err.close()
        outbytes, errbytes = outpath.read_bytes(), errpath.read_bytes()
        if sink is not None:
            sink.write(outbytes)
            sink.flush()
            os.fsync(sink.fileno())
        if stderr == native.STDOUT:
            outbytes += errbytes
            errbytes = b""
        result = native.CompletedProcess(argv, child.returncode, outbytes, errbytes)
        self.event({**intent, "stage": "exited", "pid": child.pid, "returncode": child.returncode,
                    "timedOut": timed_out, "stdoutSHA256": hashlib.sha256(outpath.read_bytes()).hexdigest(),
                    "stderrSHA256": hashlib.sha256(errpath.read_bytes()).hexdigest()})
        if interrupted:
            raise interrupted
        if timed_out:
            raise ProcessFailure(label + " 超过预算；原始输出已私有捕获")
        if check and result.returncode:
            raise ProcessFailure(label + " exit=" + str(result.returncode))
        if text or encoding:
            result.stdout = result.stdout.decode(encoding or "utf-8", errors="replace")
            result.stderr = result.stderr.decode(encoding or "utf-8", errors="replace")
        return result

    def popen(self, argv, *, label="auxiliary-process", cwd=None, env=None, **kwargs):
        """辅助进程启动后立即保存 PID 与出生身份，不依赖 Suite 字段成功赋值。"""
        self.checkpoint()
        outpath, out = self._capture(".stdout")
        errpath, err = self._capture(".stderr")
        intent = {"id": uuid.uuid4().hex, "kind": "process", "label": label,
                  "stage": "intent", "at": stamp(), "auxiliary": True}
        self.event(intent)
        options = dict(kwargs)
        options.pop("stdout", None); options.pop("stderr", None)
        options.setdefault("start_new_session", True)
        try:
            child = native.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err, **options)
        finally:
            out.close(); err.close()
        birth = process_identity(child.pid)
        item = {**intent, "stage": "started", "pid": child.pid, "birth": birth}
        self.children.append((child, item))
        self.event(item)
        # 调用者可核对该 child 自己的监听就绪记录；路径仅指向私有捕获，不转发原文。
        child.private_stdout_path=outpath
        return child

    def close_auxiliary(self):
        """逐个结束本次启动的辅助进程；一个失败不跳过后续安全清理。"""
        failures = []
        for child, item in self.children:
            try:
                if child.poll() is None:
                    if not item["birth"] or process_identity(child.pid) != item["birth"]:
                        raise ProcessFailure("辅助进程出生身份不能确认")
                    if os.getpgid(child.pid) != child.pid:
                        raise ProcessFailure("辅助进程组不是本次独立 child")
                    members = group_members(child.pid)
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=1)
                    except native.TimeoutExpired:
                        pass
                    remaining = group_members(child.pid)
                    if remaining:
                        # 关键：leader退出不等于整组结束；先核至少一个原成员仍占同组，才可结束忽略TERM的孙进程。
                        if not any(remaining.get(pid) == birth for pid, birth in members.items()):
                            raise ProcessFailure("辅助进程组原成员身份不能确认")
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                    end = time.monotonic() + 5
                    while group_members(child.pid) and time.monotonic() < end:
                        time.sleep(.02)
                    if group_members(child.pid):
                        raise ProcessFailure("辅助进程组仍有存活成员")
                self.event({**item, "stage": "exited", "returncode": child.returncode})
            except Exception:
                failures.append({"id": item["id"], "error": "辅助进程正常退出未确认"})
        return failures


class ProcessProxy:
    """现有 Suite 显式导入的薄适配器；不修改全局 subprocess 模块。"""

    def __getattr__(self, name):
        return getattr(native, name)

    def run(self, argv, **kwargs):
        active = ACTIVE.get()
        return active.processes.run(argv, **kwargs) if active else native.run(argv, **kwargs)

    def Popen(self, argv, **kwargs):
        active = ACTIVE.get()
        return active.processes.popen(argv, **kwargs) if active else native.Popen(argv, **kwargs)

    def check_output(self, argv, **kwargs):
        active = ACTIVE.get()
        if not active:
            return native.check_output(argv, **kwargs)
        kwargs.pop("stdout", None)
        return active.processes.run(argv, check=True, **kwargs).stdout


process = ProcessProxy()
