#!/usr/bin/env python3
"""唯一 Session 的实际执行接缝；原业务断言、原响应身份与公开证据分别保留。"""
import base64
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

import registry
from kube_transport import KubeFailure, KubeTransport, identity
from ledger import Ledger
from session import BatchSession, StopRequested, atomic_json, stamp
from transport import ProcessRunner


def reference(path, root, kind="safe-json"):
    """引用绑定现有原件字节，路径只能在本run内。"""
    path, root = Path(path).resolve(), Path(root).resolve()
    return dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                visibility="public", kind=kind)


def go_events(raw, entries, returncode):
    """公开仅保留真实Go事件的安全字段；根与leaf的skip/fail均使对应键失败。"""
    values = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("Action") in ["start", "run", "pause", "cont", "pass", "fail", "skip"]:
            values.append({k: event[k] for k in ["Time", "Action", "Package", "Test", "Elapsed"] if k in event})
    outcomes = {}
    for entry in entries:
        selectors = entry["testSelectors"]
        roots = {s.split("/")[0] for s in selectors}
        relevant = [v for v in values if v.get("Test", "").split("/")[0] in roots]
        passed = {v.get("Test") for v in relevant if v["Action"] == "pass"}
        outcomes[entry["variantId"]] = (returncode == 0 and set(selectors) <= passed and
                                         not any(v["Action"] in ["skip", "fail"] for v in relevant))
    return values, outcomes


class ExecutionSession(BatchSession):
    """一个批次唯一持锁者；Suite、Node子进程和恢复共享同一身份/进程通道。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.private = self.out / ".private"
        self.private.mkdir(mode=0o700)
        self.canaries = set()
        self.process_events = {}
        self.processes = ProcessRunner(self.private / "processes", checkpoint=self.checkpoint, event=self.process_event)
        self.environment_descriptor = {}
        self.ledger = None
        self.images = {}
        self.http_lock = threading.RLock()
        self.http_context = threading.local()
        self.proxied_suites = set()
        self.platform_accesses = {}
        self.platform_auth = {}
        self.confirmed_instances = {}
        self.fixture_gates = []
        self.cluster_targets = {}
        self.instances = {}
        self.publications = {}
        self.servers = []
        self.handler_drain_timeout = 900
        self.handler_abort_timeout = 45
        self.abort_handlers = False
        self.owner_thread = threading.get_ident()
        self.kube_lock = threading.RLock()
        self.ipc_token = secrets.token_urlsafe(32)
        self.register_secrets(self.ipc_token)

    def checkpoint(self):
        """强制排空阶段禁止handler开始下一条外部动作；已得原响应仍先确认/fsync。"""
        if self.abort_handlers and threading.get_ident() != self.owner_thread:
            raise StopRequested("HTTP handler 已停止新动作")
        super().checkpoint()

    def process_event(self, event):
        """PID出生身份先持久化；Suite未赋值也不能丢失已启动child。"""
        self.process_events[event["id"]] = event
        atomic_json(self.private / "processes.json", self.process_events)
        if self.ledger:
            self.ledger.process_event(event)

    def register_secrets(self, *values):
        """公开出口重新读取本run私有canary清单，不将认证文本写入index。"""
        for value in values:
            if isinstance(value, str) and value:
                self.canaries.add(value)
        atomic_json(self.private / "canaries.json", sorted(self.canaries))

    def header(self, entry):
        """所有原件记录实际同C身份，规范wrapper不能洗掉原件来源。"""
        return {**registry.contract(entry), "profile": self.index["profile"],
                "executionRunId": self.index["runId"], "environmentOwnerRunId": self.environment_descriptor.get("runId"),
                "candidateCommit": self.index["candidateCommit"], "testedCommit": self.index["testedCommit"],
                "testedTree": self.index["testedTree"], "runnerCommit": self.index["runnerCommit"],
                "source": dict(commit=self.index["testedCommit"], tree=self.index["testedTree"], dirty=self.index["dirty"]),
                "dirty": self.index["dirty"], "manifestHash": registry.MANIFEST_HASH,
                "contractFingerprint": entry["contractFingerprint"], "testSelectors": entry["testSelectors"],
                "executionBoundary": entry["executionBoundary"], "startedAt": stamp()}

    def store(self, entry, original, case_root=None, original_file=None, original_evidence_base=None):
        """原业务函数本次输出先封存原件；另建规范wrapper，所有引用重新实算字节。"""
        case_root = Path(case_root or self.out)
        def normalize(value):
            if isinstance(value, dict):
                if "path" in value and "sha256" in value:
                    relative = Path(value["path"])
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("business-evidence-path-escape")
                    path = self.out / relative
                    if not path.is_file():
                        path = case_root / relative
                    extension = path.suffix
                    kind = value.get("kind") or ("screenshot" if extension in [".png", ".jpg", ".webp"] else
                            "safe-summary" if extension in [".log", ".txt", ".md"] else "go-events" if extension == ".jsonl" else "safe-json")
                    actual = reference(path, self.out, kind)
                    if actual["sha256"] != value["sha256"]:
                        raise ValueError("business-original-artifact-changed")
                    return actual
                return {k: normalize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(v) for v in value]
            return value
        original = normalize(original)
        original["finishedAt"] = stamp()
        self.safe(original)
        stem = entry["layer"] + "." + entry["variantId"]
        raw_path = Path(original_file) if original_file else self.out / "original" / (stem + ".json")
        if original_file is None:
            atomic_json(raw_path, original)
        wrapper = {**copy.deepcopy(original), "originalReceipt": reference(raw_path, self.out, "receipt"),
                   "originalEvidenceBase": original_evidence_base if original_evidence_base is not None else str(raw_path.parent.relative_to(self.out)) if original_file else ""}
        atomic_json(self.out / "receipts" / (stem + ".json"), wrapper)
        self.result(entry["variantId"], entry["layer"], outcome=original["outcome"],
                    receipt=reference(self.out / "receipts" / (stem + ".json"), self.out, "receipt"))
        return wrapper

    def store_original_file(self, entry, original_path, original_evidence_base=None):
        """封存派生模块已写好的原件；只新增wrapper，不修改原JSON或原hash。"""
        path = Path(original_path)
        return self.store(entry, json.loads(path.read_text()), path.parent, original_file=path,
                          original_evidence_base=original_evidence_base)

    def run_go(self, entries):
        """复用原Go测试函数，实际HTTP→generated Kitex/TLS/SQLite断言保持原样。"""
        if not entries:
            return
        roots = sorted({s.split("/")[0] for e in entries for s in e["testSelectors"]})
        command = ["go", "test", "./internal/platform", "-count=1", "-json", "-run", "^(" + "|".join(roots) + ")$"]
        result = self.processes.run(command, label="actual-go-acceptance", cwd=registry.ROOT, timeout=1800)
        events, outcomes = go_events(result.stdout, entries, result.returncode)
        path = self.out / "events" / "go.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")
            stream.flush(); os.fsync(stream.fileno())
        event_ref = reference(path, self.out, "go-events")
        for entry in entries:
            original = {**self.header(entry), "outcome": "passed" if outcomes[entry["variantId"]] else "failed",
                        "command": command, "commandExitCode": result.returncode, "processLocation": "host-Q",
                        "noSkipped": outcomes[entry["variantId"]], "goEventRef": event_ref,
                        "runtimeOS": self.index["environment"]["runtimeOS"], "runtimeArch": self.index["environment"]["runtimeArch"],
                        "cleanup": {"success": True, "scope": "原Go测试t.Cleanup/TempDir/listener"}, "evidenceRefs": [event_ref]}
            self.store(entry, original)

    def run_protocol_q(self, entries):
        """协议Q在实际新进程执行；validator-test原事件不会冒充真实K/B。"""
        if not entries:
            return
        selectors = [s for e in entries for s in e["testSelectors"]]
        events_path = self.out / "events" / "protocol.json"
        command = [sys.executable, str(Path(__file__).with_name("gate.py")), "protocol-tests", "--events", str(events_path), "--selectors", *selectors]
        result = self.processes.run(command, label="actual-protocol-q", cwd=registry.ROOT, timeout=300)
        value = json.loads(events_path.read_text())
        passed = {v["selector"] for v in value["tests"] if v["outcome"] == "passed"}
        event_ref = reference(events_path, self.out, "protocol-events")
        for entry in entries:
            success = result.returncode == 0 and set(entry["testSelectors"]) <= passed
            self.store(entry, {**self.header(entry), "outcome": "passed" if success else "failed", "command": command,
                "commandExitCode": result.returncode, "processLocation": "host-Q", "noSkipped": success,
                "protocolEventRef": event_ref, "runtimeOS": self.index["environment"]["runtimeOS"],
                "runtimeArch": self.index["environment"]["runtimeArch"], "cleanup": {"success": True, "scope": "协议fixture临时目录与真实child"},
                "evidenceRefs": [event_ref]})

    def safe(self, value):
        """批准JSON仍逐值扫描；发现敏感值保持失败，不改写原证据凑绿色。"""
        data = json.dumps(value, ensure_ascii=False).encode()
        if any(v.encode() in data for v in self.canaries):
            raise ValueError("public-evidence-sensitive-canary")
        return value

    def configure(self, descriptor):
        """环境已真实verify后才建立业务transport；descriptor原件始终私有。"""
        self.environment_descriptor = copy.deepcopy(descriptor)
        # 关键：独立REG复用同C环境时，两次执行不共用runId；环境原owner及labels不得改写。
        self.index.update(executionRunId=self.index["runId"], environmentOwnerRunId=descriptor.get("runId"))
        atomic_json(self.private / "environment.json", descriptor)
        targets = descriptor.get("targets", {})
        self.ledger = Ledger(self.out / "ledger.json", run_id=self.index["runId"], candidate=self.index["candidateCommit"],
                             targets={name: target["clusterUID"] for name, target in targets.items()})
        self.kube = KubeTransport(ledger=self.ledger, execute=self.processes.run, targets=targets,
                                  kubectl=descriptor.get("kubectl", "kubectl"), checkpoint=self.checkpoint)
        self.index["environment"]["targets"] = [dict(name=name, clusterUID=target["clusterUID"],
                                                          nodeCount=len(target["nodeTopology"])) for name, target in targets.items()]
        self.flush()
        self._start_kube_ipc()

    def image_metadata(self, image):
        """只保存公开平台镜像的原OCI JSON；私有archive不进入证据闭包。"""
        if image in self.images:
            return copy.deepcopy(self.images[image])
        approved = {self.environment_descriptor.get("image"), *self.environment_descriptor.get("fixtureImages", {}).values()}
        existing = self.environment_descriptor.get("imageProofs", {}).get(image)
        if existing is not None:
            if image not in approved or existing.get("source") != self.index["candidateCommit"]:
                raise ValueError("unapproved-public-image-proof")
            destination = self.out / "images" / uuid.uuid4().hex
            destination.mkdir(parents=True, mode=0o700)
            refs = {}
            for item in existing["evidenceRefs"]:
                source = Path(item["path"])
                data = source.read_bytes()
                if source.is_symlink() or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("prepared-image-original-bytes-changed")
                json.loads(data)
                target = destination / source.name
                target.write_bytes(data); target.chmod(0o600)
                refs[source.name] = reference(target, self.out, "image-config" if source.name == "config.raw.json" else "image-manifest")
            config = json.loads((destination / "config.raw.json").read_bytes())
            manifest_name = "oci-manifest.raw.json" if "oci-manifest.raw.json" in refs else "oci-root.raw.json"
            if manifest_name not in refs:
                raise ValueError("prepared-image-oci-manifest-required")
            manifest = json.loads((destination / manifest_name).read_bytes())
            config_digest = "sha256:" + refs["config.raw.json"]["sha256"]
            manifest_digest = "sha256:" + refs[manifest_name]["sha256"]
            if (manifest.get("schemaVersion") != 2 or manifest.get("config", {}).get("digest") != config_digest
                    or existing.get("manifestDigest") != manifest_digest or existing["configDigest"] != config_digest
                    or config.get("config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != self.index["candidateCommit"]
                    or config.get("os") + "/" + config.get("architecture") != self.environment_descriptor["platform"]):
                raise ValueError("prepared-image-source-or-oci-binding-mismatch")
            proof = {**copy.deepcopy(existing), "manifestDigest": manifest_digest, "revision": self.index["candidateCommit"],
                     "manifestRef": refs[manifest_name], "configRef": refs["config.raw.json"], "evidenceRefs": list(refs.values())}
            self.images[image] = proof
            return copy.deepcopy(proof)
        from image_provenance import read_docker_archive
        inspect = self.processes.run(["docker", "image", "inspect", image], label="inspect-platform-image", check=True)
        metadata = json.loads(inspect.stdout)[0]
        archive = self.private / ("platform-" + uuid.uuid4().hex + ".tar")
        with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as sink:
            self.processes.run(["docker", "save", image], label="save-platform-image-private", stdout=sink, check=True)
        destination = self.out / "images" / uuid.uuid4().hex
        proof = read_docker_archive(archive, image=image, expected_source=self.index["candidateCommit"],
                                    platform=self.environment_descriptor["platform"], inspect=metadata, destination=destination)
        archive.unlink()
        refs = {Path(v["path"]).name: reference(v["path"], self.out,
                "image-config" if Path(v["path"]).name == "config.raw.json" else "image-manifest") for v in proof["evidenceRefs"]}
        manifest_name = "oci-manifest.raw.json" if "oci-manifest.raw.json" in refs else "oci-root.raw.json"
        if manifest_name not in refs:
            raise ValueError("platform-image-oci-manifest-required")
        proof.update(revision=self.index["candidateCommit"], manifestRef=refs[manifest_name], configRef=refs["config.raw.json"],
                     evidenceRefs=list(refs.values()))
        self.images[image] = proof
        return copy.deepcopy(proof)

    def http_intent(self, suite, method, path, body):
        """HTTP请求发前只记安全动作；草稿创建不虚构目标CR创建意图。"""
        if id(suite) in self.proxied_suites and not getattr(self.http_context, "inside_proxy", False):
            return
        self.checkpoint()
        if isinstance(body, dict):
            for key in ["password", "currentPassword", "newPassword", "kubeconfig"]:
                if body.get(key):
                    self.register_secrets(body[key])
        if method == "POST" and path.endswith("/publications"):
            instance_id = path.split("/")[2]
            with self.http_lock:
                expected = self.instances.get(instance_id)
                if expected:
                    token = self.ledger.intent(expected["target"], "product-publish", expected["identity"], origin="platform-publication-intent")
                    self.publications.setdefault(instance_id, []).append(token)
                    proxy = getattr(suite, "proxy", None)
                    if proxy and proxy.target == expected["target"]:
                        proxy.control("register-create", {"intentId": token, "identity": expected["identity"]})

    def http_response(self, suite, method, path, body, value, *, status, headers):
        """平台确认UID是创建关系证据；事后同名目标GET只能验证，不能认领。"""
        if id(suite) in self.proxied_suites and not getattr(self.http_context, "inside_proxy", False):
            return
        for name, content in headers.items():
            if name.lower() == "set-cookie":
                self.register_secrets(content.split(";", 1)[0].split("=", 1)[-1])
                if method == "POST" and path == "/sessions" and isinstance(body, dict) and body.get("username") == "admin":
                    self.platform_auth[id(suite)] = content.split(";", 1)[0]
        if method == "POST" and path.endswith("/publications") and not 200 <= status < 300:
            instance_id = path.split("/")[2]
            for token in self.publications.get(instance_id, [])[-1:]:
                self.ledger.mark(token, "rejected", errorCode="HTTP_REJECTED")
        if not 200 <= status < 300 or not isinstance(value, dict):
            return
        with self.http_lock:
            target_uids = {v["clusterUID"]: k for k, v in self.environment_descriptor.get("targets", {}).items()}
            clusters = value.get("clusters", [])
            if "uid" in value and "id" in value and "credentialRevision" in value:
                clusters = [value]
            for cluster in clusters:
                if cluster.get("uid") in target_uids:
                    self.cluster_targets[cluster["id"]] = target_uids[cluster["uid"]]
            if "id" in value and "component" in value and "name" in value and not value.get("readOnly"):
                target = self.cluster_targets.get(value.get("clusterId"))
                if target:
                    agent = value["component"] == "Prometheus Agent"
                    info = dict(apiVersion="monitoring.coreos.com/v1alpha1" if agent else "monitoring.fjsxy.github.io/v1alpha1",
                                kind="PrometheusAgent" if agent else "KubeStateMetrics", namespace=value.get("crNamespace", "") if agent else "", name=value["name"])
                    expected = self.instances.setdefault(value["id"], dict(target=target, identity=info))
                    if expected != dict(target=target, identity=info):
                        raise ValueError("platform-confirmed-instance-identity-changed")
                    if value.get("uid") and value.get("currentVersionId"):
                        previous_uid = self.confirmed_instances.get(value["id"])
                        if previous_uid and previous_uid != value["uid"]:
                            raise KubeFailure("platform_confirmed_replacement_uid")
                        if not previous_uid:
                            token = self.ledger.intent(target, "product-confirm", info, origin="product-confirmed-instance")
                            self.ledger.confirm_instance(token, value)
                            self.confirmed_instances[value["id"]] = value["uid"]
                        for token in self.publications.get(value["id"], []):
                            self.ledger.mark(token, "identity_confirmed")
            if method == "POST" and path.endswith("/publications"):
                instance_id = path.split("/")[2]
                for token in self.publications.get(instance_id, [])[-1:]:
                    self.ledger.mark(token, "accepted", originRef=value.get("operationId", ""))
        if value.get("action") == "PUBLISH" and value.get("state") == "SUCCEEDED" and value.get("instanceId"):
            self.observe_confirmed_instance(suite, value["instanceId"])

    def observe_confirmed_instance(self, suite, instance_id):
        """发布实际成功后另读平台确认身份；只读fixture观测不改原业务请求授权结果。"""
        access = self.platform_accesses.get(id(suite))
        if not access or instance_id not in self.instances:
            return
        headers = {"Origin": access["proxy"], "Host": urllib.parse.urlsplit(access["proxy"]).netloc}
        if id(suite) in self.platform_auth:
            headers["Cookie"] = self.platform_auth[id(suite)]
        try:
            request = urllib.request.Request(access["upstream"] + "/api/v1/instances/" + instance_id, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                value = json.load(response)
                self.http_response(suite, "GET", "/instances/" + instance_id, None, value, status=response.code, headers=dict(response.headers))
        except (urllib.error.URLError, ValueError):
            # 不能凭目标GET补认领；稍后原确认仍不可读时，cleanup保留identity gap。
            return

    def fixture_gate_intent(self, suite, action, body):
        """release先持久intent；响应丢失也不能把可能已放行的屏障说成未release。"""
        if action == "release":
            self.fixture_gates.append(dict(at=stamp(), case=self.ledger.case, action="release-intent",
                stage=(body or {}).get("stage"), source=self.index["candidateCommit"]))
            atomic_json(self.out / "fixture-boundaries.json", self.fixture_gates)

    def fixture_gate_response(self, suite, action, body, value):
        """持久记录真实应用屏障及原writer链；没有此证据不得用当前404闭合可能已发gap。"""
        if action != "state" or not isinstance(value, dict):
            return
        for stage, gate in value.items():
            if stage not in ["beforeDispatch", "afterSendMarker"] or not gate.get("arrived"):
                continue
            deployment = json.loads(suite.k("get", "deployment", "monitor-mgr", "-n", suite.ns, "-o", "json").stdout)
            item = dict(case=self.ledger.case, at=stamp(), action="arrived", stage=stage, operationId=gate["operationId"],
                namespace=suite.ns, deploymentUID=deployment["metadata"]["uid"],
                podUID=getattr(suite, "platform_pod_uid", getattr(suite, "initial_uid", "")), source=self.index["candidateCommit"])
            if not any(all(old.get(k) == item[k] for k in ["case", "action", "stage", "operationId", "podUID"]) for old in self.fixture_gates):
                self.fixture_gates.append(item)
                atomic_json(self.out / "fixture-boundaries.json", self.fixture_gates)

    def stopped_writer_absence(self, suite, entry):
        """受控before-actual-send屏障专用：writer全退出后的当前缺失，不是原UID确认或从未发送。"""
        if suite.case not in ["P13.possibly-sent", "T08.primary"]:
            raise KubeFailure("writer_absence_fixture_not_allowed")
        gates = [v for v in self.fixture_gates if v["case"] == self.ledger.case and v["action"] == "arrived"
                 and v["stage"] == "afterSendMarker" and v["operationId"] == entry.get("originRef")]
        if not gates or any(v["action"] == "release-intent" and v["case"] == self.ledger.case and v["stage"] == "afterSendMarker" for v in self.fixture_gates):
            raise KubeFailure("original_unreleased_fixture_gate_missing")
        deployment = json.loads(suite.k("get", "deployment", "monitor-mgr", "-n", suite.ns, "-o", "json").stdout)
        if any(v["deploymentUID"] != deployment["metadata"]["uid"] for v in gates):
            raise KubeFailure("writer_deployment_replacement_uid")
        pods = json.loads(suite.k("get", "pods", "-n", suite.ns, "-l", "app=monitor-mgr", "-o", "json").stdout)["items"]
        replicas = json.loads(suite.k("get", "replicasets", "-n", suite.ns, "-o", "json").stdout)["items"]
        rsuids = {r["metadata"]["uid"] for r in replicas if any(o.get("uid") == deployment["metadata"]["uid"] and o.get("controller") for o in r["metadata"].get("ownerReferences", []))}
        if any(not any(o.get("uid") in rsuids and o.get("controller") for o in p["metadata"].get("ownerReferences", [])) for p in pods):
            raise KubeFailure("writer_pod_owner_unconfirmed")
        suite.k("scale", "deployment/monitor-mgr", "-n", suite.ns, "--replicas=0")
        suite.k("wait", "--for=delete", "pods", "-n", suite.ns, "-l", "app=monitor-mgr", "--timeout=100s")
        remaining = json.loads(suite.k("get", "pods", "-n", suite.ns, "-l", "app=monitor-mgr", "-o", "json").stdout)["items"]
        if remaining:
            raise KubeFailure("writer_pods_still_present")
        if self.kube.get_obj(entry["target"], entry["identity"]) is not None:
            raise KubeFailure("unconfirmed_same_name_object_exists")
        return dict(kind="writer-stopped-current-absence", originalIntentGapRetained=True, originalCreationUIDConfirmed=False,
                    neverSentClaimed=False, target=entry["target"], clusterUID=entry["clusterUID"], identity=entry["identity"],
                    namespace=suite.ns, deploymentUID=deployment["metadata"]["uid"], writerPodUIDs=[p["metadata"]["uid"] for p in pods],
                    allCurrentWriterPodsExited=True, observedReason="NotFound", observedAt=stamp(), barriers=gates)
    def _server(self, handler):
        """所有本地控制入口只监听loopback，生命周期仍归同一Session。"""
        class QuietServer(http.server.ThreadingHTTPServer):
            daemon_threads = True
            def __init__(self, *args):
                self.condition = threading.Condition()
                self.accepting = True
                self.handlers = 0
                super().__init__(*args)
            def get_request(self):
                request, address = super().get_request()
                request.settimeout(45)
                return request, address
            def process_request(self, request, address):
                with self.condition:
                    if not self.accepting:
                        self.shutdown_request(request)
                        return
                    self.handlers += 1
                try:
                    super().process_request(request, address)
                except BaseException:
                    with self.condition:
                        self.handlers -= 1
                        self.condition.notify_all()
                    raise
            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    with self.condition:
                        self.handlers -= 1
                        self.condition.notify_all()
            def wait_handlers(self, deadline):
                with self.condition:
                    while self.handlers and time.monotonic() < deadline:
                        self.condition.wait(max(.001, deadline-time.monotonic()))
                    return self.handlers == 0
            def handle_error(self, *_):
                pass
        server = QuietServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return "http://127.0.0.1:" + str(server.server_address[1])

    def platform_access(self, suite, upstream):
        """Node与Python均经原Service forward，代理只观察真实HTTP原响应身份。"""
        parent = self
        self.proxied_suites.add(id(suite))
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                path = self.path.removeprefix("/api/v1")
                parent.http_context.inside_proxy = True
                try:
                    parent.http_intent(suite, self.command, path, body)
                except BaseException:
                    self.send_error(503, "ACCEPTANCE_STOPPED")
                    return
                request = urllib.request.Request(upstream + self.path, data=raw if raw else None, method=self.command,
                    headers={k: v for k, v in self.headers.items() if k.lower() not in ["connection", "content-length"]})
                try:
                    response = urllib.request.urlopen(request, timeout=35)
                except urllib.error.HTTPError as error:
                    response = error
                content = response.read()
                try:
                    value = json.loads(content)
                except ValueError:
                    value = None
                parent.http_response(suite, self.command, path, body, value, status=response.code, headers=dict(response.headers))
                self.send_response(response.code)
                for key, value in response.headers.items():
                    if key.lower() not in ["connection", "transfer-encoding", "content-length"]:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                response.close()
            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_request
        address = self._server(Handler)
        self.platform_accesses[id(suite)] = dict(upstream=upstream, proxy=address)
        return address

    def _start_kube_ipc(self):
        """浏览器直接kubectl也必须回到同一UID通道，不能自行绕过ledger。"""
        parent = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if not secrets.compare_digest(request.get("token", ""), parent.ipc_token):
                    self.send_error(403)
                    return
                try:
                    args = request["args"]
                    config = KubeTransport.option(args, "--kubeconfig")
                    context = KubeTransport.option(args, "--context")
                    matches = [name for name, target in parent.environment_descriptor["targets"].items()
                               if config == target["kubeconfig"] and context == target["context"]]
                    if len(matches) != 1:
                        raise KubeFailure("ipc_explicit_target_required")
                    clean = []
                    skip = False
                    for arg in args:
                        if skip:
                            skip = False
                        elif arg in ["--kubeconfig", "--context"]:
                            skip = True
                        elif not arg.startswith(("--kubeconfig=", "--context=")):
                            clean.append(arg)
                    raw = base64.b64decode(request.get("stdin", ""))
                    with parent.kube_lock:
                        if any(arg.startswith("--dry-run") for arg in clean):
                            # 关键：浏览器原 YAML 不经过 JSON 重编码，更不能降级为真实 create。
                            result = parent.kube.client_dry_run(matches[0], clean, raw)
                        else:
                            data = json.loads(raw) if raw else None
                            result = parent.kube.command(matches[0], clean, data=data, check=False)
                    value = dict(returncode=result.returncode, stdout=base64.b64encode(result.stdout).decode(), stderr=base64.b64encode(result.stderr).decode())
                except BaseException as error:
                    value = dict(returncode=1, stdout="", stderr=base64.b64encode(("acceptance-kube:" + getattr(error, "code", "ipc-command-failed")).encode()).decode())
                encoded = json.dumps(value).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
        address = self._server(Handler)
        client = self.private / "kubectl"
        script = ("#!" + sys.executable + "\nimport base64,json,sys,urllib.request\n" +
                  "data=json.dumps({'token':" + repr(self.ipc_token) + ",'args':sys.argv[1:],'stdin':base64.b64encode(sys.stdin.buffer.read() if '-f' in sys.argv and '-' in sys.argv else b'').decode()}).encode()\n" +
                  "r=json.load(urllib.request.urlopen(urllib.request.Request(" + repr(address) + ",data=data),timeout=750))\n" +
                  "sys.stdout.buffer.write(base64.b64decode(r['stdout']));sys.stderr.buffer.write(base64.b64decode(r['stderr']));sys.exit(r['returncode'])\n")
        with os.fdopen(os.open(client, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700), "w") as stream:
            stream.write(script); stream.flush(); os.fsync(stream.fileno())
        self.browser_kubectl = str(client)

    def cleanup_case(self, suite):
        """原Suite清理统一委托；故障恢复先于资源GC，失败原件不被后续恢复涂绿。"""
        with self.cleanup_phase():
            drained = self.drain_servers()
            if drained:
                return dict(success=False, failures=drained, mode="handler-identity-confirmation-unconfirmed")
            # 原Service路径不变；排空后仅由主线程做剩余只读确认，不能与旧handler的UID确认/GC并发。
            access = self.platform_accesses.get(id(suite))
            if access:
                suite.base = access["upstream"]
                self.proxied_suites.discard(id(suite))
            failures = []
            identity_gaps = []
            writer_absences = []
            proxies = list(getattr(suite, "proxies", []))
            if hasattr(suite, "proxy"):
                proxies.append(suite.proxy)
            proxy_confirmed = set()
            for proxy in proxies:
                try:
                    for line in proxy.events().splitlines():
                        event = json.loads(line)
                        if event.get("stage") != "original_create_response":
                            continue
                        original = event["originalCreate"]
                        intent = self.ledger.entries[original["intentId"]]
                        if intent["case"] != self.ledger.case or intent["target"] != proxy.target or intent["identity"] != original["identity"] or intent["action"] != "product-publish":
                            raise ValueError("proxy-original-response-association-invalid")
                        token = self.ledger.intent(proxy.target, "proxy-confirm", original["identity"], origin="proxy-original-create-response")
                        info = original["identity"]
                        self.ledger.confirm(token, dict(apiVersion=info["apiVersion"], kind=info["kind"], metadata=dict(
                            name=info["name"], namespace=info["namespace"], uid=original["uid"], resourceVersion=original["resourceVersion"])))
                        self.ledger.mark(token, "confirmed", originRef=intent["id"])
                        proxy_confirmed.add(original["intentId"])
                        # 原产品gap保持，不以fixture观测改写产品DB/operation或声称已收到响应。
                        if intent["stage"] != "identity_confirmed":
                            self.ledger.uncertain(intent["id"])
                            identity_gaps.append(dict(id=intent["id"], productIdentityUncertain=True, fixtureOriginalResponse=token))
                except Exception:
                    failures.append(dict(errorCode="proxy_original_response_read_failed"))
            for restore in reversed(getattr(suite, "restorers", [])):
                try:
                    restore()
                except Exception:
                    failures.append(dict(errorCode="fixture_restore_failed"))
            suite.restorers = []
            if hasattr(suite, "proxy"):
                try:
                    suite.proxy.control("reset", {})
                    suite.proxy.close()
                except Exception:
                    failures.append(dict(errorCode="fixture_proxy_restore_failed"))
                del suite.proxy
            # 公开平台确认尽量先读，无法确认的可能已发窗口保留gap而不按名字认领。
            for instance_id, expected in list(self.instances.items()):
                if any(self.ledger.entries[t]["stage"] == "accepted" for t in self.publications.get(instance_id, [])):
                    try:
                        suite.call("GET", "/instances/" + instance_id)
                    except Exception:
                        failures.append(dict(errorCode="platform_original_uid_unconfirmed"))
            for instance_id, tokens in self.publications.items():
                for token in tokens:
                    entry = self.ledger.entries[token]
                    if entry["case"] == self.ledger.case and entry["stage"] in ["intent", "accepted", "identity_unconfirmed"] and token not in proxy_confirmed:
                        # 明确terminal且全部尝试未派发才可证明无目标写入；QUEUED/CHECKING不凭一次快照放行。
                        try:
                            operation_id = entry.get("originRef")
                            operation = suite.call("GET", "/operations/" + operation_id)
                            attempts = suite.call("GET", "/operations/" + operation_id + "/attempts")["attempts"]
                            assert operation["state"] in ["FAILED", "CANCELLED", "CANCELED", "WAITING_CONFIRMATION"] and attempts and all(not a["possiblySent"] for a in attempts)
                            self.ledger.mark(token, "confirmed_unsent")
                        except Exception:
                            try:
                                proof = self.stopped_writer_absence(suite, entry)
                                writer_absences.append(proof)
                                self.ledger.data.setdefault("writerCurrentAbsences", {})[token] = proof
                                self.ledger.flush()
                            except Exception:
                                failures.append(dict(id=token, errorCode="product_identity_unconfirmed"))
            if failures:
                return dict(success=False, failures=failures, identityGaps=identity_gaps, mode="identity-preserved-no-blind-gc")
            result = self.kube.recover_case(self.ledger.case)
            result["failures"].extend(failures)
            result["success"] = result["success"] and not failures
            result["identityGaps"] = identity_gaps
            result["writerStoppedCurrentAbsences"] = writer_absences
            return result

    def drain_servers(self):
        """先停接收再在lane内等已接收handler；普通run完成不等于原UID已确认。"""
        servers, self.servers = self.servers, []
        for server, _ in servers:
            with server.condition:
                server.accepting = False
        close_failures = []
        for server, thread in servers:
            try:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
            except BaseException:
                close_failures.append(dict(errorCode="http-listener-close-unconfirmed"))
        deadline = time.monotonic() + self.handler_drain_timeout
        if all(server.wait_handlers(deadline) for server, _ in servers):
            return close_failures
        self.abort_handlers = True
        failures = close_failures + [dict(errorCode="accepted-http-handler-drain-timeout")]
        try:
            # 复用已核原PID/birth/PGID恢复原语；不得用同名GET补创建UID。
            from recovery import stop_processes
            runner = ProcessRunner(self.private / "handler-process-stop")
            snapshot = {key: copy.deepcopy(value) for key, value in self.process_events.items() if value.get("stage") != "exited"}
            stopped = stop_processes(snapshot, runner.run,
                lambda value: atomic_json(self.private / "handler-process-stop.json", value))
            if not stopped["success"]:
                failures.append(dict(errorCode="handler-process-stop-unconfirmed"))
        except BaseException:
            failures.append(dict(errorCode="handler-process-stop-unconfirmed"))
        deadline = time.monotonic() + self.handler_abort_timeout
        if not all(server.wait_handlers(deadline) for server, _ in servers):
            self.retain_lane_until_exit = True
            failures.append(dict(errorCode="handler-still-running-lease-retained"))
        return failures

    def shutdown(self):
        """服务器先结束，最后核所有实际辅助进程退出；不重入原case finally。"""
        with self.cleanup_phase():
            failures = self.drain_servers()
            return failures + self.processes.close_auxiliary()
