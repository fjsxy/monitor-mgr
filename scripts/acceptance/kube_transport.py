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
