#!/usr/bin/env python3
"""显式目标的 Kubernetes 传输；创建响应、条件写入与恢复共享同一身份账本。"""
import copy
import json
import subprocess
import time

from session import stamp


class KubeFailure(RuntimeError):
    """安全错误码不包含 Secret、token、kubeconfig 或原始 API 响应。"""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def identity(obj):
    """只提取无敏感值的资源定位信息。"""
    return {"apiVersion": obj["apiVersion"], "kind": obj["kind"],
            "namespace": obj["metadata"].get("namespace", ""), "name": obj["metadata"]["name"]}


RESOURCES = {
    "Namespace": ("v1", "namespaces", False), "Pod": ("v1", "pods", True),
    "Secret": ("v1", "secrets", True), "ConfigMap": ("v1", "configmaps", True),
    "ServiceAccount": ("v1", "serviceaccounts", True), "Service": ("v1", "services", True),
    "PersistentVolumeClaim": ("v1", "persistentvolumeclaims", True),
    "Deployment": ("apps/v1", "deployments", True), "ReplicaSet": ("apps/v1", "replicasets", True),
    "StatefulSet": ("apps/v1", "statefulsets", True), "DaemonSet": ("apps/v1", "daemonsets", True),
    "Job": ("batch/v1", "jobs", True),
    "ClusterRole": ("rbac.authorization.k8s.io/v1", "clusterroles", False),
    "ClusterRoleBinding": ("rbac.authorization.k8s.io/v1", "clusterrolebindings", False),
    "Role": ("rbac.authorization.k8s.io/v1", "roles", True),
    "RoleBinding": ("rbac.authorization.k8s.io/v1", "rolebindings", True),
    "CustomResourceDefinition": ("apiextensions.k8s.io/v1", "customresourcedefinitions", False),
    "StorageClass": ("storage.k8s.io/v1", "storageclasses", False),
    "KubeStateMetrics": ("monitoring.fjsxy.github.io/v1alpha1", "kubestatemetrics", False),
    "PrometheusAgent": ("monitoring.coreos.com/v1alpha1", "prometheusagents", True),
    "ServiceMonitor": ("monitoring.coreos.com/v1", "servicemonitors", True),
    "PodMonitor": ("monitoring.coreos.com/v1", "podmonitors", True),
    "Probe": ("monitoring.coreos.com/v1", "probes", True),
    "ScrapeConfig": ("monitoring.coreos.com/v1alpha1", "scrapeconfigs", True),
}
ALIASES = {alias: kind for kind, (_, plural, _) in RESOURCES.items()
           for alias in [kind.lower(), plural]}
ALIASES.update(ns="Namespace", sa="ServiceAccount", cm="ConfigMap", pvc="PersistentVolumeClaim",
               po="Pod", svc="Service", deploy="Deployment", sts="StatefulSet", ds="DaemonSet",
               rs="ReplicaSet", crd="CustomResourceDefinition", sc="StorageClass")


def pointer_escape(value):
    """JSON Pointer 字段名与 API 资源名分别处理。"""
    return value.replace("~", "~0").replace("/", "~1")


def changes(before, after, path=""):
    """只生成实际变动字段，保留目标上其他 metadata/spec/status 字段。"""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            where = path + "/" + pointer_escape(key)
            if key not in after:
                result.append({"op": "remove", "path": where})
            elif key not in before:
                result.append({"op": "add", "path": where, "value": after[key]})
            else:
                result.extend(changes(before[key], after[key], where))
        return result
    return [] if before == after else [{"op": "replace", "path": path, "value": after}]


def merged(before, patch):
    """按 JSON merge patch 语义合成内存副本；null 是删除字段。"""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    value = copy.deepcopy(before) if isinstance(before, dict) else {}
    for key, item in patch.items():
        if item is None:
            value.pop(key, None)
        else:
            value[key] = merged(value.get(key), item)
    return value


_MISSING = object()


def undo_delta(before, after, current, path=""):
    """撤回本次字段差异；后来外部写入的字段和 finalizer 必须保留。"""
    if before == after:
        return copy.deepcopy(current) if current is not _MISSING else _MISSING
    if path == "/metadata/finalizers" and all(isinstance(v, list) for v in [before, after, current]):
        ours = set(after) - set(before)
        removed = [v for v in before if v not in after]
        return [v for v in current if v not in ours] + [v for v in removed if v not in current]
    if isinstance(after, dict) and (isinstance(before, dict) or before is _MISSING) and isinstance(current, dict):
        original = {} if before is _MISSING else before
        result = copy.deepcopy(current)
        for key in original.keys() | after.keys():
            value = undo_delta(original.get(key, _MISSING), after.get(key, _MISSING),
                               current.get(key, _MISSING), path + "/" + pointer_escape(key))
            if value is _MISSING:
                result.pop(key, None)
            else:
                result[key] = value
        return _MISSING if before is _MISSING and not result else result
    if current == after:
        return copy.deepcopy(before) if before is not _MISSING else _MISSING
    if current == before:
        return copy.deepcopy(current) if current is not _MISSING else _MISSING
    raise KubeFailure("fault_field_changed_by_other_writer")


def apply_json_patch(before, operations):
    """仅解释 fixture 使用的 RFC6902 变更，不允许默默忽略未知操作。"""
    value = copy.deepcopy(before)
    for operation in operations:
        parts = [p.replace("~1", "/").replace("~0", "~") for p in operation["path"].lstrip("/").split("/")]
        parent = value
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        if operation["op"] == "test":
            if parent[key] != operation["value"]:
                raise KubeFailure("fixture_patch_test_failed")
        elif operation["op"] == "remove":
            del parent[key]
        elif operation["op"] in ["add", "replace"]:
            parent[key] = copy.deepcopy(operation["value"])
        else:
            raise KubeFailure("unsupported_fixture_patch")
    return value


def resource_path(info):
    """DELETE raw 路径来自已确认的类型和身份，不从任意 URL 猜归属。"""
    api, plural, namespaced = RESOURCES[info["kind"]]
    api = info.get("apiVersion", api)
    root = "/api/" + api if "/" not in api else "/apis/" + api
    if namespaced:
        if not info["namespace"]:
            raise KubeFailure("missing_namespace")
        root += "/namespaces/" + info["namespace"]
    return root + "/" + plural + "/" + info["name"]


class KubeTransport:
    """所有 fixture 写入在唯一 BatchSession 内走此公开边界。"""

    def __init__(self, *, ledger, execute, targets, kubectl, checkpoint=None):
        self.ledger = ledger
        self.execute = execute
        self.targets = targets
        self.kubectl = [kubectl] if isinstance(kubectl, str) else list(kubectl)
        self.checkpoint = checkpoint or (lambda: None)

    def command(self, target, args, *, data=None, check=True):
        """旧 Suite 的薄命令适配；所有写入必须归一为明确动作，未知形式失败闭合。"""
        self.checkpoint()
        verbs = {"get", "describe", "logs", "wait", "rollout", "version", "auth", "create", "apply", "replace", "patch", "delete", "label", "annotate", "scale", "exec"}
        skip = False
        position = None
        for i, arg in enumerate(args):
            if skip:
                skip = False
                continue
            if arg in ["--kubeconfig", "--context"] or arg.startswith(("--kubeconfig=", "--context=")):
                raise KubeFailure("context_override_forbidden")
            if arg in ["-n", "--namespace", "-o", "--output"]:
                skip = True
                continue
            if arg in verbs:
                position = i
                break
        if position is None:
            raise KubeFailure("undeclared_command")
        verb = args[position]
        if verb in ["create", "apply"] and isinstance(data, dict):
            objects = data.get("items") if data.get("kind") == "List" else [data]
            if not isinstance(objects, list) or not objects:
                raise KubeFailure("empty_mutation_list")
            # 关键：List 部分成功后也已持久逐对象原响应，不能等最后一个成功才登记整个 List。
            values = []
            for obj in objects:
                info = identity(obj)
                known = self.ledger.find(target, info["kind"], info["name"], info["namespace"])
                if known and verb == "apply":
                    current = self.get_obj(target, info)
                    if current is None:
                        raise KubeFailure("fixture_apply_original_missing")
                    values.append(self.mutate_obj(target, merged(current, obj)))
                else:
                    restored = any(e["action"] == "fault-delete" and e["target"] == target and e["identity"] == info
                                   for e in self.ledger.entries.values())
                    values.append(self.create_obj(target, obj, origin="fault-restored" if restored else "fixture-response"))
            value = values[0] if data.get("kind") != "List" else {"apiVersion": "v1", "kind": "List", "items": values}
            return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")
        if verb in ["get", "describe", "logs", "wait", "version"] or verb == "rollout" and args[position + 1:position + 2] == ["status"] or verb == "auth" and args[position + 1:position + 2] == ["can-i"]:
            result = self._run(target, list(args), data=data)
            if check and result.returncode:
                raise KubeFailure(self.error(result))
            if not result.returncode and verb == "get":
                try:
                    self.observe(target, json.loads(result.stdout))
                except (ValueError, TypeError, KeyError):
                    pass
            return result
        tail = list(args[position + 1:])
        namespace = self.option(args, "-n", "--namespace") or ""
        if verb == "create":
            if tail[:1] == ["token"]:
                info = {"apiVersion": "v1", "kind": "ServiceAccount", "namespace": namespace, "name": tail[1]}
                self.require_owner(target, info)
                token = self.ledger.intent(target, "token-request", info, origin="fixture-service-account")
                result = self._run(target, list(args), label="fixture-token-request")
                self.ledger.mark(token, "completed" if not result.returncode else "rejected")
                if check and result.returncode:
                    raise KubeFailure(self.error(result))
                return result
            kind = ALIASES.get(tail[0].lower())
            if kind in ["Namespace", "ServiceAccount"]:
                obj = {"apiVersion": RESOURCES[kind][0], "kind": kind, "metadata": {"name": tail[1]}}
                if kind == "ServiceAccount":
                    obj["metadata"]["namespace"] = namespace
                else:
                    obj["metadata"]["labels"] = {"monitor-mgr-run": self.ledger.data["runId"]}
            elif kind == "ClusterRoleBinding":
                account = self.option(tail, "--serviceaccount")
                role = self.option(tail, "--clusterrole")
                if not account or not role:
                    raise KubeFailure("unsupported_role_binding")
                ns, name = account.split(":", 1)
                obj = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": kind, "metadata": {"name": tail[1]},
                       "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role},
                       "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": ns}]}
            else:
                raise KubeFailure("create_requires_object")
            value = self.create_obj(target, obj)
            return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")
        if verb == "exec":
            name = next((v for v in tail if not v.startswith("-")), None)
            info = {"apiVersion": "v1", "kind": "Pod", "namespace": namespace, "name": name}
            self.require_owner(target, info)
            token = self.ledger.intent(target, "fixture-exec", info, origin="owned-namespace-descendant")
            result = self._run(target, list(args), data=data, label="fixture-exec", timeout=180)
            self.ledger.mark(token, "completed" if not result.returncode else "rejected")
            if check and result.returncode:
                raise KubeFailure(self.error(result))
            return result
        if verb == "replace" and isinstance(data, dict):
            value = self.mutate_obj(target, data, subresource=self.option(args, "--subresource"))
            return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")
        raw = self.option(tail, "--raw")
        if raw and verb == "delete":
            all_entries = [e for e in self.ledger.entries.values() if e.get("uid") and e["target"] == target
                           and resource_path(e["identity"]) == raw and e["stage"] in ["confirmed", "delete_accepted"]]
            if not all_entries:
                raise KubeFailure("delete_identity_unconfirmed")
            entry = all_entries[-1]
            if not isinstance(data, dict) or data.get("preconditions", {}).get("uid") != entry["uid"]:
                raise KubeFailure("delete_uid_precondition_required")
            self.delete_uid(entry, wait=False, propagation=data.get("propagationPolicy", "Foreground"))
            return subprocess.CompletedProcess(args, 0, b"{}", b"")
        if not tail:
            raise KubeFailure("resource_required")
        resource, slash, name = tail[0].partition("/")
        kind = ALIASES.get(resource.lower())
        if not kind:
            raise KubeFailure("undeclared_resource")
        if not slash:
            name = tail[1] if len(tail) > 1 and not tail[1].startswith("-") else ""
        if not name:
            raise KubeFailure("named_resource_required")
        info = {"apiVersion": RESOURCES[kind][0], "kind": kind,
                "namespace": namespace if RESOURCES[kind][2] else "", "name": name}
        current = self.get_obj(target, info)
        if current is None:
            if check:
                raise KubeFailure("NotFound")
            return subprocess.CompletedProcess(args, 1, b"", b"Error from server (NotFound)")
        if verb == "delete":
            entry = self.require_owner(target, info, current=current)
            if kind == "CustomResourceDefinition":
                token = self.ledger.intent(target, "fault-delete", info, origin="fault-existing",
                                           uid=current["metadata"]["uid"], resource_version=current["metadata"]["resourceVersion"],
                                           baseline={"object": current})
                entry = self.ledger.entries[token]
            self.delete_uid(entry, force="--force" in args, wait=True)
            return subprocess.CompletedProcess(args, 0, b"{}", b"")
        desired = copy.deepcopy(current)
        if verb == "patch":
            payload = self.option(tail, "-p", "--patch")
            patch_type = self.option(tail, "--type") or "strategic"
            if not payload:
                raise KubeFailure("patch_body_required")
            patch = json.loads(payload)
            desired = apply_json_patch(current, patch) if patch_type == "json" else merged(current, patch)
        elif verb in ["label", "annotate"]:
            field = "labels" if verb == "label" else "annotations"
            desired["metadata"].setdefault(field, {})
            for value in tail[2 if not slash else 1:]:
                if value.startswith("-"):
                    continue
                if "=" in value:
                    key, item = value.split("=", 1)
                    desired["metadata"][field][key] = item
                elif value.endswith("-"):
                    desired["metadata"][field].pop(value[:-1], None)
                else:
                    raise KubeFailure("metadata_assignment_required")
        elif verb == "scale":
            replicas = self.option(tail, "--replicas")
            if replicas is None:
                raise KubeFailure("replicas_required")
            desired.setdefault("spec", {})["replicas"] = int(replicas)
        else:
            raise KubeFailure("mutation_requires_typed_adapter")
        value = self.mutate_obj(target, desired, subresource=self.option(tail, "--subresource"))
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    @staticmethod
    def option(args, *names):
        """识别 flag=value 与 flag value；只解析调用边界已声明的选项。"""
        for i, value in enumerate(args):
            for name in names:
                if value.startswith(name + "="):
                    return value[len(name) + 1:]
                if value == name and i + 1 < len(args):
                    return args[i + 1]
        return None

    def observe(self, target, value):
        """仅登记已确认 namespace 内的后代读取，明确不伪称 fixture 创建原响应。"""
        for obj in value.get("items", [value]):
            if obj.get("kind") not in RESOURCES or not obj.get("metadata", {}).get("uid"):
                continue
            info = identity(obj)
            if not info["namespace"] or self.ledger.find(target, info["kind"], info["name"], info["namespace"]):
                continue
            ns = self.ledger.find(target, "Namespace", info["namespace"])
            if not ns or ns["origin"] != "fixture-response":
                continue
            token = self.ledger.intent(target, "observe-descendant", info, origin="owned-namespace-descendant")
            self.ledger.confirm(token, obj)

    def require_owner(self, target, info, *, current=None):
        """变更必须有原确认身份，或已声明全局故障的当前原 UID/私有基线。"""
        entry = self.ledger.find(target, info["kind"], info["name"], info["namespace"])
        if entry:
            if current and current["metadata"]["uid"] != entry["uid"]:
                raise KubeFailure("replacement_uid")
            return entry
        if info["kind"] in ["CustomResourceDefinition", "StorageClass"] and self.ledger.case:
            current = current or self.get_obj(target, info)
            token = self.ledger.intent(target, "fault-baseline", info, origin="fault-existing",
                                       uid=current["metadata"]["uid"], resource_version=current["metadata"]["resourceVersion"],
                                       baseline={"object": current})
            self.ledger.confirm(token, current)
            return self.ledger.entries[token]
        if info["namespace"]:
            current = current or self.get_obj(target, info)
            if current:
                self.observe(target, current)
                entry = self.ledger.find(target, info["kind"], info["name"], info["namespace"])
                if entry:
                    return entry
        raise KubeFailure("resource_identity_unconfirmed")

    def _run(self, target, args, *, data=None, label="kube-read", timeout=180):
        """即使 caller 已有默认配置，仍强制显式 kubeconfig/context。"""
        if target not in self.targets:
            raise KubeFailure("target_not_declared")
        config = self.targets[target]
        if not config.get("kubeconfig") or not config.get("context"):
            raise KubeFailure("explicit_context_required")
        return self.execute([*self.kubectl, "--kubeconfig", config["kubeconfig"], "--context", config["context"], *args],
                            input=None if data is None else json.dumps(data).encode(), label=label, timeout=timeout)

    @staticmethod
    def error(result):
        """只有 kubectl 明确的 Status reason 才当 NotFound，任意非零都不是空集合。"""
        raw = result.stderr if isinstance(result.stderr, bytes) else str(result.stderr).encode()
        for reason in ["NotFound", "Forbidden", "Unauthorized", "AlreadyExists", "Invalid", "Conflict"]:
            if ("(" + reason + ")").encode() in raw:
                return reason
        return "TransportUncertain"

    def get_obj(self, target, info):
        """成功响应只在内存使用，恢复不会因此新建创建确认。"""
        args = ["get", RESOURCES[info["kind"]][1], info["name"], "-o", "json"]
        if info["namespace"]:
            args += ["-n", info["namespace"]]
        result = self._run(target, args)
        if result.returncode:
            code = self.error(result)
            if code == "NotFound":
                return None
            raise KubeFailure(code)
        value = json.loads(result.stdout)
        if identity(value) != info:
            raise KubeFailure("read_identity_mismatch")
        return value

    def create_obj(self, target, obj, *, origin="fixture-response"):
        """写前 intent/实际原响应 UID；发送后任何缺失响应都保持未知。"""
        token = self.ledger.intent(target, "create", identity(obj), origin=origin)
        try:
            result = self._run(target, ["create", "-f", "-", "-o", "json"], data=obj, label="kube-create")
            if result.returncode:
                code = self.error(result)
                if code == "TransportUncertain":
                    self.ledger.uncertain(token)
                else:
                    self.ledger.mark(token, "rejected", errorCode=code)
                raise KubeFailure(code)
            response = json.loads(result.stdout)
            self.ledger.confirm(token, response)
        except BaseException:
            if self.ledger.entries[token]["stage"] == "intent":
                self.ledger.uncertain(token)
            raise
        # 关键：协作停止检查必须在原响应身份落盘之后，不能先抛信号丢掉已成功创建的 UID。
        self.checkpoint()
        return response

    def mutate_obj(self, target, desired, *, subresource=None, restoring=False):
        """已确认 UID 的条件变更；全局故障先私有持久字段基线，恢复只撤回自己的改动。"""
        self.checkpoint()
        info = identity(desired)
        current = self.get_obj(target, info)
        if current is None:
            raise KubeFailure("mutation_target_missing")
        owner = self.ledger.find(target, info["kind"], info["name"], info["namespace"])
        global_fault = info["kind"] in ["StorageClass", "CustomResourceDefinition"] and bool(self.ledger.case)
        if not owner and not global_fault:
            raise KubeFailure("mutation_identity_unconfirmed")
        uid = current["metadata"]["uid"]
        if owner and owner["uid"] != uid or desired["metadata"].get("uid", uid) != uid:
            raise KubeFailure("replacement_uid")
        revision = current["metadata"]["resourceVersion"]
        if desired["metadata"].get("resourceVersion", revision) != revision:
            raise KubeFailure("stale_fixture_revision")
        desired = copy.deepcopy(desired)
        desired["metadata"]["uid"] = uid
        desired["metadata"]["resourceVersion"] = revision
        patch = changes(current, desired)
        if not patch:
            return current
        fault = not restoring and (global_fault or any(p["path"].startswith("/metadata/finalizers") for p in patch))
        baseline = {"before": current, "after": desired, "subresource": subresource} if fault else None
        origin = "fault-existing" if global_fault else owner["origin"]
        token = self.ledger.intent(target, "fault-mutate" if fault else "restore" if restoring else "mutate", info, origin=origin,
                                   uid=uid, resource_version=revision, baseline=baseline)
        # 关键：GET 后按名 PATCH 仍有替代竞争窗，实际请求必须原子测试 UID 和读到的 RV。
        payload = [{"op": "test", "path": "/metadata/uid", "value": uid},
                   {"op": "test", "path": "/metadata/resourceVersion", "value": revision}, *patch]
        args = ["patch", RESOURCES[info["kind"]][1], info["name"], "--type=json", "-p", json.dumps(payload), "-o", "json"]
        if info["namespace"]:
            args += ["-n", info["namespace"]]
        if subresource:
            args += ["--subresource=" + subresource]
        try:
            result = self._run(target, args, label="kube-conditional-patch")
            if result.returncode:
                raise KubeFailure(self.error(result))
            response = json.loads(result.stdout)
            self.ledger.confirm(token, response)
        except BaseException:
            self.ledger.uncertain(token)
            raise
        self.checkpoint()
        return response

    def restore_fault(self, entry):
        """只恢复持久记录的本次故障；替代 UID 和不可判定的外部变更均受阻。"""
        if entry["stage"] == "restored":
            return
        baseline = self.ledger.baseline(entry)
        target, info = entry["target"], entry["identity"]
        current = self.get_obj(target, info)
        if entry["action"] == "fault-mutate":
            if current is None:
                raise KubeFailure("fault_original_missing")
            if current["metadata"]["uid"] != entry["uid"]:
                raise KubeFailure("replacement_uid")
            desired = undo_delta(baseline["before"], baseline["after"], current)
            self.mutate_obj(target, desired, subresource=baseline.get("subresource"), restoring=True)
        elif entry["action"] == "fault-delete":
            if current is not None:
                own_restored = self.ledger.find(target, info["kind"], info["name"], info["namespace"])
                if own_restored and own_restored["origin"] == "fault-restored" and current["metadata"]["uid"] == own_restored["uid"]:
                    self.ledger.mark(entry["id"], "restored", restoredAt=stamp())
                    return
                if current["metadata"]["uid"] != entry["uid"]:
                    raise KubeFailure("replacement_uid")
                if not current["metadata"].get("deletionTimestamp"):
                    self.ledger.mark(entry["id"], "restored", restoredAt=stamp())
                    return
                raise KubeFailure("fault_original_still_deleting")
            original = copy.deepcopy(baseline["object"])
            original.pop("status", None)
            for field in ["uid", "resourceVersion", "generation", "creationTimestamp", "deletionTimestamp", "deletionGracePeriodSeconds", "managedFields"]:
                original["metadata"].pop(field, None)
            self.create_obj(target, original, origin="fault-restored")
        else:
            raise KubeFailure("unknown_fault_action")
        self.ledger.mark(entry["id"], "restored", restoredAt=stamp())

    def delete_uid(self, entry, *, propagation="Foreground", wait=True, timeout=660, force=False):
        """原 UID 条件删除；替代对象和读取错误均保持恢复失败。"""
        target, info = entry["target"], entry["identity"]
        actual = self.get_obj(target, info)
        if actual is None:
            self.ledger.mark(entry["id"], "absent", notFoundAt=stamp())
            return
        if actual["metadata"]["uid"] != entry["uid"]:
            self.ledger.mark(entry["id"], "replacement", replacementUID=actual["metadata"]["uid"])
            raise KubeFailure("replacement_uid")
        options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": entry["uid"]},
                   "propagationPolicy": propagation}
        if force:
            options["gracePeriodSeconds"] = 0
        token = self.ledger.intent(target, "delete", info, origin=entry["origin"], uid=entry["uid"], resource_version=actual["metadata"]["resourceVersion"])
        result = self._run(target, ["delete", "--raw", resource_path(info), "-f", "-"], data=options, label="kube-delete")
        if result.returncode and self.error(result) != "NotFound":
            self.ledger.mark(token, "delete_failed", errorCode=self.error(result))
            raise KubeFailure(self.error(result))
        self.ledger.mark(token, "delete_accepted", deleteAcceptedAt=stamp())
        self.ledger.mark(entry["id"], "delete_accepted", deleteAcceptedAt=stamp())
        if wait:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                actual = self.get_obj(target, info)
                if actual is None:
                    self.ledger.mark(entry["id"], "absent", notFoundAt=stamp())
                    self.ledger.mark(token, "absent", notFoundAt=stamp())
                    return
                if actual["metadata"]["uid"] != entry["uid"]:
                    raise KubeFailure("replacement_uid")
                time.sleep(.2)
            raise KubeFailure("normal_gc_timeout")

    def recover_case(self, case=None):
        """只回收已确认原身份；原gap/403/替代UID分别保留失败，不用prefix inventory认领。"""
        failures = []
        results = []
        entries = [e for e in self.ledger.entries.values() if case is None or e["case"] == case]
        blocked = set()
        for entry in reversed(entries):
            if entry["action"] not in ["fault-mutate", "fault-delete"] or entry["stage"] in ["restored", "rejected"]:
                continue
            try:
                self.restore_fault(entry)
            except Exception as error:
                failures.append({"id": entry["id"], "errorCode": getattr(error, "code", "fault_restore_failed")})
                blocked.add(entry["target"])
        for entry in entries:
            if entry["action"] in ["create", "product-confirm"] and entry["stage"] in ["intent", "identity_unconfirmed"]:
                failures.append({"id": entry["id"], "errorCode": "identity_unconfirmed"})
        candidates = [e for e in entries if e["action"] in ["create", "product-confirm"]
                      and e.get("uid") and e["stage"] in ["confirmed", "delete_accepted", "read_failed"]
                      and e["origin"] != "fault-restored"]
        # Agent先结束正常Foreground，再回收namespace，最后撤本case RBAC，保证GC期间身份仍可用。
        def order(entry):
            kind = entry["identity"]["kind"]
            return 0 if kind in ["KubeStateMetrics", "PrometheusAgent"] else 1 if kind == "Namespace" else 2
        parents = {(e["target"], e["identity"]["name"]) for e in candidates if e["identity"]["kind"] == "Namespace"}
        namespaces = []
        for entry in sorted(candidates, key=order):
            info = entry["identity"]
            if info["namespace"] and (entry["target"], info["namespace"]) in parents and info["kind"] != "PrometheusAgent":
                continue
            if entry["target"] in blocked:
                failures.append({"id": entry["id"], "errorCode": "dependent_cleanup_blocked"})
                continue
            try:
                if info["kind"] == "Namespace":
                    self.delete_uid(entry, wait=False)
                    namespaces.append(entry)
                    continue
                # 关键：namespace正常GC完成前，不能撤销本case RBAC等cluster-scoped依赖。
                if order(entry) == 2 and namespaces:
                    self.wait_namespaces(namespaces, failures, results, blocked)
                    namespaces = []
                    if entry["target"] in blocked:
                        failures.append({"id": entry["id"], "errorCode": "dependent_cleanup_blocked"})
                        continue
                self.delete_uid(entry)
                results.append({"id": entry["id"], "uid": entry["uid"], "notFound": True})
            except KubeFailure as error:
                failures.append({"id": entry["id"], "uid": entry["uid"], "errorCode": error.code})
                blocked.add(entry["target"])
            except Exception:
                failures.append({"id": entry["id"], "uid": entry["uid"], "errorCode": "cleanup_transport_failed"})
                blocked.add(entry["target"])
        self.wait_namespaces(namespaces, failures, results, blocked)
        return {"success": not failures, "failures": failures, "resources": results,
                "mode": "original-uid-normal-gc", "force": False, "genericFinalizerRemoval": False}

    def wait_namespaces(self, entries, failures, results, blocked, timeout=660):
        """先条件删除全部本case namespace，再统一有界等待正常GC，不串行叠加预算。"""
        pending = list(entries)
        end = time.monotonic() + timeout
        while pending and time.monotonic() < end:
            for entry in list(pending):
                try:
                    current = self.get_obj(entry["target"], entry["identity"])
                    if current is None:
                        self.ledger.mark(entry["id"], "absent", notFoundAt=stamp())
                        results.append({"id": entry["id"], "uid": entry["uid"], "notFound": True})
                        pending.remove(entry)
                    elif current["metadata"]["uid"] != entry["uid"]:
                        raise KubeFailure("replacement_uid")
                except Exception as error:
                    failures.append({"id": entry["id"], "errorCode": getattr(error, "code", "namespace_read_failed")})
                    blocked.add(entry["target"])
                    pending.remove(entry)
            if pending:
                time.sleep(.2)
        for entry in pending:
            failures.append({"id": entry["id"], "errorCode": "normal_gc_timeout"})
            blocked.add(entry["target"])
