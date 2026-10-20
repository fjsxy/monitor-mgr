#!/usr/bin/env python3
"""一期验收批次的持久 index、唯一锁所有权与协作停止边界。"""
import argparse
from contextlib import contextmanager
import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

# 关键：无法确认writer退出时不能显式解锁；仅保留到本进程退出，由仍持租约的父执行独立恢复。
_LEASES_UNTIL_PROCESS_EXIT = []


def stamp():
    """所有持久事件使用明确的 UTC 时间。"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic_json(path, value, mode=0o600):
    """持久化完整文件后原子换名，连目录项一起同步，避免恢复读取半份身份。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class StopRequested(BaseException):
    """停止不是可重试 API 错误，不能被业务轮询的 except Exception 吞掉。"""


class BatchSession:
    """在 Suite 构造前保存期望键，单一 child 持锁至全部 finally 和收据落盘。"""

    def __init__(self, output, *, expected, run_id, profile, candidate, lane=None,
                 environment=None, source=None, protocol_fixture=False, inherited_lane_fd=None):
        self.out = Path(output).resolve()
        if self.out.exists() and any(self.out.iterdir()):
            raise ValueError("新的 attempt 不能覆盖已有输出目录")
        self.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.out, 0o700)
        self.lane_path = lane
        self.lane = None
        self.inherited_lane_fd = inherited_lane_fd
        self.borrowed_lane = False
        self.signals = []
        self.old_handlers = {}
        self.cleaning = False
        self.closed = False
        self.cleanup_count = 0
        source = source or {}
        self.index = {
            "schemaVersion": 1, "runId": run_id, "profile": profile,
            "candidateCommit": candidate, "testedCommit": source.get("commit", candidate),
            "testedTree": source.get("tree"), "runnerCommit": source.get("commit", candidate),
            "dirty": source.get("dirty", False), "environment": environment or {},
            "executionBoundary": "protocol-fixture" if protocol_fixture else "acceptance",
            "expectedKeys": expected, "startedAt": stamp(), "state": "pending",
            "results": [{**key, "outcome": "not_run"} for key in expected],
            "signals": [], "cleanup": {"success": False, "state": "not_started"},
        }
        # 关键：这一写入先于任何环境锁等待、Suite 构造及外部动作，失败时也不能缺键。
        self.flush()

    def flush(self):
        """信号处理器只写内存，持久化在正常执行检查点完成。"""
        self.index["signals"] = list(self.signals)
        atomic_json(self.out / "index.json", self.index)

    def _signal(self, number, _frame):
        self.signals.append({"number": number, "name": signal.Signals(number).name, "at": stamp()})

    def checkpoint(self):
        """在安全边界停止新动作；清理期间重复信号只计数，不重入 finally。"""
        if self.signals and not self.cleaning:
            self.flush()
            if self.signals[0]["number"] == signal.SIGINT:
                raise KeyboardInterrupt("收到 SIGINT，停止新用例")
            raise StopRequested("收到停止信号，停止新用例")

    def start(self):
        """可取消地取得唯一批次锁；调用者不能在父进程再持同一把锁等待本 child。"""
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.old_handlers[sig] = signal.signal(sig, self._signal)
        self.index["state"] = "waiting_lock" if self.lane_path else "running"
        self.flush()
        if self.lane_path:
            if self.inherited_lane_fd is not None:
                inherited = os.fstat(self.inherited_lane_fd)
                declared = os.stat(self.lane_path)
                if (inherited.st_dev, inherited.st_ino) != (declared.st_dev, declared.st_ino):
                    raise ValueError("继承lane与声明路径不符")
                # 关键：同一open-file-description的非阻塞核验不会再次等待父owner；不同租约立即失败。
                fcntl.flock(self.inherited_lane_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.lane = os.fdopen(os.dup(self.inherited_lane_fd), "a")
                self.borrowed_lane = True
                self.index["state"] = "running"
                self.index["laneOwnership"] = "inherited-batch-lease"
                self.flush()
                self.checkpoint()
                return self
            self.lane = open(self.lane_path, "a")
            while True:
                self.checkpoint()
                try:
                    fcntl.flock(self.lane, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.05)
            self.checkpoint()
        self.index["state"] = "running"
        self.flush()
        return self

    @contextmanager
    def cleanup_phase(self):
        """正常清理不受重复 TERM/INT 抢占；原始信号仍保存在 index。"""
        previous = self.cleaning
        self.cleaning = True
        try:
            yield
        finally:
            self.cleaning = previous

    def close(self, error=None, cleanup=None):
        """一次性完成最终收据后才放锁；不会用恢复成功覆盖原业务失败。"""
        if self.closed:
            return
        self.closed = True
        self.cleanup_count += 1
        self.index["cleanup"] = cleanup or {"success": True, "scope": "无外部资源动作"}
        self.index["cleanup"]["entryCount"] = self.cleanup_count
        if getattr(self, "retain_lane_until_exit", False):
            self.index["cleanup"].update(success=False, laneRetainedUntilProcessExit=True)
        if self.signals or isinstance(error, (StopRequested, KeyboardInterrupt)):
            self.index["state"] = "interrupted"
        elif error:
            self.index["state"] = "failed"
        else:
            self.index["state"] = "completed" if self.index["cleanup"].get("success") is True and all(r["outcome"] == "passed" for r in self.index["results"]) else "failed"
        if error:
            self.index["failure"] = {"type": type(error).__name__, "stage": self.index.get("stage", "setup")}
        self.index["finishedAt"] = stamp()
        try:
            self.flush()
        finally:
            if self.lane:
                if getattr(self, "retain_lane_until_exit", False):
                    _LEASES_UNTIL_PROCESS_EXIT.append(self.lane)
                else:
                    if not self.borrowed_lane:
                        fcntl.flock(self.lane, fcntl.LOCK_UN)
                    self.lane.close()
                self.lane = None
            for sig, handler in self.old_handlers.items():
                signal.signal(sig, handler)

    def result(self, variant, layer, *, outcome, **fields):
        """逐键原子落盘；不能用后来的恢复或另一次 attempt 覆盖已有终态。"""
        found = [r for r in self.index["results"] if r["variantId"] == variant and r["layer"] == layer]
        if len(found) != 1:
            raise ValueError("未知或重复执行键")
        record = found[0]
        if record["outcome"] != "not_run":
            raise ValueError("本 attempt 的已完成执行键不可覆盖")
        record.update(fields, outcome=outcome)
        self.flush()


def protocol_child(a):
    """仅供真实协议子进程回归；fixture 键永不计入产品验收结果。"""
    session = BatchSession(a.output, expected=[{"variantId": "fixture.first", "layer": "Q"},
                                            {"variantId": "fixture.second", "layer": "Q"}],
                           run_id="protocol-child", profile="validator-test", candidate="fixture",
                           lane=a.api_lane, protocol_fixture=True, inherited_lane_fd=a.inherited_lane_fd)
    error = None
    try:
        session.start()
        session.checkpoint()
        (session.out / "setup-entered").write_text("protocol-fixture\n")
        if a.mode == "before-first":
            session.index["stage"] = "setup"
            session.result("fixture.first", "Q", outcome="environment_failed")
            raise RuntimeError("受控首例前失败，仅验证 index 协议")
        if a.mode == "after-first":
            session.result("fixture.first", "Q", outcome="passed", executionBoundary="protocol-fixture")
            session.index["stage"] = "between_cases"
            raise RuntimeError("受控首例后失败，不代表任何业务例通过")
    except BaseException as failure:
        error = failure
    finally:
        session.close(error)
    return 1 if error else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["protocol-child"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--api-lane")
    parser.add_argument("--inherited-lane-fd", type=int)
    parser.add_argument("--mode", choices=["lock-wait", "before-first", "after-first"], required=True)
    raise SystemExit(protocol_child(parser.parse_args()))
