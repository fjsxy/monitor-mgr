#!/usr/bin/env python3
"""批次资源身份与故障基线的持久账本；名字和前缀不能替代原响应 UID。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

from session import atomic_json, stamp


class Ledger:
    """每次外部变更先 intent，实际响应确认身份后才允许持久 checkpoint。"""

    def __init__(self, path, *, run_id, candidate, targets):
        self.path = Path(path).resolve()
        self.data = {"schemaVersion": 1, "runId": run_id, "candidateCommit": candidate,
                     "targets": targets, "entries": {}, "processes": {}, "events": []}
        self.case = None
        self.flush()

    @classmethod
    def open(cls, path):
        """恢复只读取已有账本，不用当前 inventory 补造已丢失的创建身份。"""
        path = Path(path)
        if path.is_symlink():
            raise ValueError("ledger 不能是符号链接")
        obj = cls.__new__(cls)
        obj.path = path.resolve()
        obj.data = json.loads(path.read_text())
        obj.case = None
        if obj.data.get("schemaVersion") != 1 or not isinstance(obj.data.get("entries"), dict):
            raise ValueError("未知 ledger 协议")
        return obj

    @property
    def entries(self):
        return self.data["entries"]

    def flush(self):
        """原子写入与目录 fsync 由共享持久通道保证。"""
        atomic_json(self.path, self.data)

    def intent(self, target, action, identity, *, origin="fixture-response", case=None,
               uid=None, resource_version=None, baseline=None):
        """公开只存动作和身份；敏感基线另存 0600 私有文件，绝不存请求 body。"""
        if target not in self.data["targets"]:
            raise ValueError("动作未绑定已声明目标")
        required = {"apiVersion", "kind", "namespace", "name"}
        if set(identity) != required or any(not isinstance(identity[k], str) for k in required):
            raise ValueError("资源身份字段不完整")
        token = uuid.uuid4().hex
        entry = {"id": token, "target": target, "clusterUID": self.data["targets"][target],
                 "action": action, "identity": copy.deepcopy(identity), "origin": origin,
                 "case": case if case is not None else self.case, "stage": "intent", "at": stamp()}
        if uid:
            entry.update(uid=uid, resourceVersion=resource_version)
        if baseline is not None:
            private = self.path.parent / ".private" / "baselines" / (token + ".json")
            atomic_json(private, baseline)
            entry["baseline"] = {"path": str(private.relative_to(self.path.parent)),
                                 "sha256": hashlib.sha256(private.read_bytes()).hexdigest(),
                                 "visibility": "private"}
        self.entries[token] = entry
        self.flush()
        return token

    def confirm(self, token, response):
        """只接受本次实际响应的完整身份；不同名字/namespace/UID不能确认同一 intent。"""
        entry = self.entries[token]
        metadata = response.get("metadata", {})
        actual = {"apiVersion": response.get("apiVersion"), "kind": response.get("kind"),
                  "name": metadata.get("name"), "namespace": metadata.get("namespace", "")}
        if actual != entry["identity"] or not metadata.get("uid") or not metadata.get("resourceVersion"):
            raise ValueError("实际响应身份不符，不能确认创建关系")
        if entry.get("uid") and entry["uid"] != metadata["uid"]:
            raise ValueError("实际响应为替代 UID")
        entry.update(stage="confirmed", uid=metadata["uid"], resourceVersion=metadata["resourceVersion"],
                     confirmedAt=stamp())
        self.flush()
        return entry

    def uncertain(self, token):
        """响应丢失保持可能已发送；不能在事后 GET 同名对象后换成 confirmed。"""
        self.entries[token].update(stage="identity_unconfirmed", failedAt=stamp())
        self.flush()

    def mark(self, token, stage, **safe_fields):
        """记录确定的恢复/删除阶段，不向公开 ledger 添加原始异常或对象字段。"""
        allowed = {"notFoundAt", "deleteAcceptedAt", "restoredAt", "replacementUID", "errorCode", "originRef"}
        if any(k not in allowed for k in safe_fields):
            raise ValueError("ledger 只允许安全阶段字段")
        self.entries[token].update(stage=stage, **safe_fields)
        self.flush()

    def find(self, target, kind, name, namespace=""):
        """只查已持久确认的身份，不从名字推断当前对象属于本次运行。"""
        matches = [e for e in self.entries.values() if e["target"] == target
                   and e["identity"]["kind"] == kind and e["identity"]["name"] == name
                   and e["identity"]["namespace"] == namespace and e.get("uid")
                   and e["stage"] in ["confirmed", "delete_accepted", "read_failed"]]
        return matches[-1] if matches else None

    def baseline(self, entry):
        """基线只允许在本 run 受保护目录内解析，并重新核对原件 hash。"""
        ref = entry["baseline"]
        relative = Path(ref["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("baseline 路径越界")
        # 关键：可信 run 根先 canonicalize；只禁止根内部的别名，不把 macOS /tmp 系统入口误当越界。
        root = self.path.parent.resolve()
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("baseline 路径不能经过符号链接")
        if not path.resolve().is_relative_to(root):
            raise ValueError("baseline 路径越界")
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
            raise ValueError("baseline 字节变化")
        return json.loads(path.read_text())

    def process_event(self, event):
        """辅助 PID 的出生身份和阶段独立于 Suite 成员赋值持久化。"""
        self.data["processes"][event["id"]] = dict(event)
        self.flush()
