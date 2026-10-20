#!/usr/bin/env python3
"""原业务 suites 的单例调用边界；父批次负责精确索引，子进程隔离全局fixture覆盖。"""
import argparse
import hashlib
import http.cookiejar
import importlib
import json
import os
from pathlib import Path
import re
import stat
import time
import traceback

from execution import reference
import exporter
import gate
from session import atomic_json, stamp


def plan(entry):
    """只选择已有业务函数及其原前置，不重写产品case断言。"""
    runner, variant = entry["runner"], entry["variantId"]
    if variant in ["REG18-PATTERN-011", "REG19-HTTP-I64-001"] and entry["layer"] == "B":
        return {"derivedFrom": "U02.primary" if variant == "REG18-PATTERN-011" else "U05.primary"}
    if variant.startswith("REG21-"):
        return {"protocol": True}
    import platform_cases
    if variant in platform_cases.K_CASES + platform_cases.B_CASES:
        # 关键：继承owner只说明责任来源；平台case入口不能随后续票的同名覆盖而消失。
        platform_cases._entry(entry)
        return {"module": "agent", "platform": True}
    if runner == "run":
        return {"module": "agent", "platform": True}
    if runner == "review":
        return {"module": "workbench_cases", "browser": True}
    if runner == "workbench":
        return {"module": "workbench_cases", "browser": entry["layer"] == "B"}
    if runner not in ["clusters", "ksm", "lifecycle", "agent", "agent_storage"]:
        raise ValueError("business-runner-not-registered")
    return {"module": runner, "browser": entry["layer"] == "B"}


def capture_failure(session, failure):
    """异常原值只落私有0600；公开诊断只保留安全类型和仓库相对frame。"""
    root = Path(__file__).resolve().parents[2]
    frames = []
    for frame in traceback.extract_tb(failure.__traceback__):
        path = Path(frame.filename)
        if path.is_absolute() and path.is_relative_to(root) and path.suffix == ".py":
            frames.append(dict(file=str(path.relative_to(root)), line=frame.lineno,
                               function=frame.name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", frame.name) else "<module>"))
    name = type(failure).__name__
    safe = dict(exceptionType=name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", name) else "Exception",
                repositoryFrames=frames, privateCaptureSucceeded=False)
    try:
        atomic_json(session.private / "business-exception.json", {
            "exceptionType": name, "traceback": "".join(traceback.format_exception(failure))})
        safe["privateCaptureSucceeded"] = True
    except Exception:
        # 关键：二次诊断失败与原业务失败并列，不能覆盖原errorCode或丢掉已有安全frame。
        safe["diagnosticErrorCode"] = "private-exception-capture-failed"
    return dict(errorCode=getattr(failure, "code", "business-assertion-or-setup-failed"), errorDiagnostics=safe)


def arguments(session, entry):
    """所有路径来自已验证的私有descriptor，SMOKE不生成假的data字段。"""
    descriptor = session.environment_descriptor
    targets = descriptor["targets"]
    role = "lifecycle" if entry["runner"] == "lifecycle" and entry["variantId"] in importlib.import_module("lifecycle").FIXTURE_CASES else None
    if entry["variantId"] == "REG20-STORAGE-001.dispatch-race":
        role = "lifecycle"
    if entry["variantId"] == "I03.primary":
        role = "rpc"
    image = descriptor["fixtureImages"][role] if role else descriptor["image"]
    args = dict(session=session, source=session.index["candidateCommit"], profile=session.index["profile"],
                output=str(session.out / "business"), prefix=session.index["runId"], port=18080,
                kubectl=descriptor["kubectl"], image=image, fixture_image=descriptor.get("fixtureImages", {}).get("lifecycle"),
                rpc_image=descriptor.get("fixtureImages", {}).get("rpc"), api_lane=session.lane_path,
                environment_descriptor=descriptor, allow_dirty_runner=False, allow_source_bridge=False,
                initialize=True, prepare_target=True, fail_fast=True, layer=entry["layer"], cases=[entry["variantId"]])
    for name in ["control", "data"]:
        target = targets.get(name)
        args[name + "_config"] = target["kubeconfig"] if target else None
        args[name + "_context"] = target["context"] if target else None
        args[name + "_pod_config"] = target["podConfig"] if target else None
    return argparse.Namespace(**args)


def _reference_values(value):
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield value
        else:
            for child in value.values():
                yield from _reference_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_values(child)


def _default_kind(path):
    """缺省类型与ExecutionSession.store一致，不按producer文件名猜特殊类型。"""
    suffix = Path(path).suffix
    return ("screenshot" if suffix in [".png", ".jpg", ".webp"] else
            "safe-summary" if suffix in [".log", ".txt", ".md"] else
            "go-events" if suffix == ".jsonl" else "safe-json")


def _refs(root, declarations=(), canaries=()):
    """显式typed声明先核原bytes；generic只补未声明原件，不能另造semantic kind。"""
    encoded = tuple(v.encode() if isinstance(v, str) else v for v in canaries)
    reader = exporter.DiagnosticEvidence(root, encoded, {})
    declared = list(_reference_values(declarations))
    # 关键：先验证显式kind，再处理合法的path/hash缺省引用；声明冲突不能靠排序覆盖。
    for value in sorted(declared, key=lambda ref: "kind" not in ref):
        path = value.get("path")
        exporter._public_path(path)
        kind = value.get("kind", reader.files.get(path, {}).get("kind", _default_kind(path)))
        reader.read(dict(path=path, sha256=value["sha256"], kind=kind,
                         visibility=value.get("visibility", "public")))
    values = dict(reader.files)
    # fwalk给出真实目录fd；guard先于open，末端O_NOFOLLOW，私有目录不读。
    for directory, directories, names, fd in os.fwalk(root, follow_symlinks=False):
        directories[:] = [name for name in directories if name != ".private"]
        for name in sorted(names):
            path = Path(directory) / name
            if name in ["results.json", "receipt.json"] or path.suffix not in [".json", ".jsonl", ".png", ".log", ".txt"]:
                continue
            relative = str(path.relative_to(root))
            exporter._public_path(relative)
            if relative in values:
                continue
            try:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    gate.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid-artifact-type")
                    data = stream.read()
            except OSError:
                raise gate.GateError("missing-or-symlink-artifact") from None
            kind = _default_kind(path)
            exporter._safe(relative, data, kind, "public", encoded)
            values[relative] = dict(path=relative, sha256=hashlib.sha256(data).hexdigest(),
                                    visibility="public", kind=kind)
    return [values[path] for path in sorted(values)]


def _canonical_refs(value, references):
    """只替换已完整验证的suite根声明；productImage的child根引用不进入此映射。"""
    by_path = {ref["path"]: ref for ref in references}
    def replace(child):
        if isinstance(child, dict):
            if "path" in child and "sha256" in child:
                actual = by_path[child["path"]]
                gate.require(actual["sha256"] == child["sha256"], "changed-artifact")
                return dict(actual)
            return {key: replace(item) for key, item in child.items()}
        return [replace(item) for item in child] if isinstance(child, list) else child
    return replace(value)


def _browser_report(suite, entry):
    """从真实报告重新收集selector，不能根据文件存在就判浏览器已执行。"""
    selectors = entry["testSelectors"]
    candidates = []
    for path in sorted(suite.out.rglob("*.json")):
        if ".private" in path.relative_to(suite.out).parts:
            continue
        try:
            value = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            continue
        if not isinstance(value, dict) or "suites" not in value:
            continue
        specs = []
        def visit(node):
            specs.extend(node.get("specs", []))
            for child in node.get("suites", []):
                visit(child)
        visit(value)
        def executed(selector):
            found = [s for s in specs if s.get("title", "").split(" ", 1)[0] == selector or
                     s.get("title", "").startswith(selector + ".")]
            return bool(found) and all(s.get("tests") and all(t.get("status") == "expected" and t.get("results") and
                all(r.get("status") == "passed" and not r.get("error") and not r.get("errors") for r in t["results"])
                for t in s["tests"]) for s in found)
        # 关键：--list报告只有title；必须存在逐测试真实passed事件，且唯一匹配本case。
        if not value.get("errors") and all(executed(selector) for selector in selectors):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("actual-browser-report-missing-or-ambiguous")
    return reference(candidates[0], suite.out, "browser-report")


def run_case(session, entry):
    """单一child只调用一个原case；所有部分setup与异常都在同一finally内。"""
    selected = plan(entry)
    if "module" not in selected:
        raise ValueError("non-business-case-in-business-child")
    session.ledger.case = entry["layer"] + ":" + entry["variantId"]
    original = session.header(entry)
    original.update(outcome="failed", processLocation="host-driver/platform-pod", runtimeOS="linux",
                    runtimeArch=session.environment_descriptor["platform"].split("/")[1])
    suite = None
    error = None
    try:
        module = importlib.import_module(selected["module"])
        if selected["module"] == "agent_storage":
            # 关键：该原suite覆盖agent.run_case；仅在这个独立child内生效，绝不污染下一例Agent。
            module.base.run_case = module.storage_case
        args = arguments(session, entry)
        suite = module.Suite(args)
        suite.case = entry["variantId"]
        suite.traces = []; suite.targetns = None; suite.target = "control"
        suite.extra_namespaces = []; suite.created_crs = []; suite.cross_namespace_fixture = None
        suite.cookies = http.cookiejar.CookieJar()
        suite.projection_markers = []; suite.instances = []; suite.proxies = []
        suite.pending_ksms = {}; suite.ksm_identities = []
        def run_captured(command, *, cwd, env, private_path, timeout):
            Path(private_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            result = session.processes.run(command, cwd=cwd, env=env, timeout=timeout, label="original-platform-case")
            Path(private_path).write_bytes(result.stdout + result.stderr); Path(private_path).chmod(0o600)
            return result
        suite.run_captured = run_captured
        if selected.get("platform"):
            import platform_cases
            platform_cases.before_deploy(suite, entry)
        before = {t: suite.inventory(t) for t in suite.target_names} if entry["runner"] == "clusters" else None
        original["productImage"] = session.image_metadata(args.image)
        deployed = suite.deploy(entry["variantId"], 0)
        original["platform"] = dict(deployed, nodeName=deployed["node"], serviceName="monitor-mgr",
                                     clusterUID=session.environment_descriptor["targets"]["control"]["clusterUID"])
        if selected.get("platform"):
            original["evidence"] = platform_cases.run_case(suite, entry)
        elif selected["module"] == "clusters":
            original["evidence"] = suite.run_case(entry["variantId"])
            assert before == {t: suite.inventory(t) for t in suite.target_names}, "注册/凭据操作改变了观测CR"
            original["observabilityInventoryUnchanged"] = True
        elif selected["module"] == "workbench_cases":
            if selected["browser"]:
                from workbench_browser import run_browser
                original["evidence"] = run_browser(suite, entry["variantId"], 0)
            else:
                original["evidence"] = {"R02.primary": suite.r02, "R04.primary": suite.r04, "R07.pod": suite.r07, "R08.primary": suite.r08}[entry["variantId"]](0)
        elif selected["browser"] and selected["module"] == "ksm":
            from ksm_browser import run_browser
            original["evidence"] = run_browser(suite)
        else:
            target = "data" if entry["variantId"] in ["C04.agent-crd", "C04.ksm-crd", "A05.primary", "C05.primary"] else "control"
            suite.prepare_target(0, target)
            if entry["runner"] == "lifecycle" and entry["variantId"] in module.FIXTURE_CASES or entry["variantId"] == "REG20-STORAGE-001.dispatch-race":
                suite.connect_gate()
            if selected["module"] in ["agent", "agent_storage"]:
                if selected["browser"]:
                    from agent_browser import run_browser
                    original["evidence"] = run_browser(suite, entry["variantId"])
                elif entry["variantId"].startswith("REG18-PROJECTION-013."):
                    from agent_projection import projection_case
                    original["evidence"] = projection_case(suite, entry["variantId"])
                else:
                    original["evidence"] = importlib.import_module("agent").run_case(suite, entry["variantId"])
            else:
                original["evidence"] = suite.run_case(entry["variantId"])
        if entry["layer"] == "B":
            original["browserEventRef"] = _browser_report(suite, entry)
        original["outcome"] = "passed"
    except BaseException as failure:
        error = failure
        original["errorCode"] = getattr(failure, "code", "business-assertion-or-setup-failed")
        original.update(capture_failure(session, failure))
    finally:
        with session.cleanup_phase():
            if suite is not None:
                try:
                    original["cleanup"] = suite.cleanup()
                except Exception:
                    original["cleanup"] = dict(success=False, failures=[dict(errorCode="case-cleanup-exception")])
                original["traces"] = suite.traces
                session.register_secrets(*getattr(suite, "projection_markers", []))
                try:
                    declarations = {key: original[key] for key in ["evidence", "browserEventRef"] if key in original}
                    evidence_refs = _refs(suite.out, declarations, getattr(session, "canaries", ()))
                    original.update(_canonical_refs(declarations, evidence_refs))
                    original["evidenceRefs"] = evidence_refs
                except (gate.GateError, OSError) as failure:
                    # 关键：证据拒绝仍走原shutdown/cleanup；失败声明不进入公开receipt。
                    if error is None:
                        error = failure
                        original.update(capture_failure(session, failure))
                    original["outcome"] = "failed"
                    original["evidenceCollectionFailed"] = True
                    original.pop("evidence", None); original.pop("browserEventRef", None)
                    original["evidenceRefs"] = []
                # 先归一到child根，再封存实际原件，父wrapper保持这些原件字节不变。
                def rebase(value):
                    if isinstance(value, dict):
                        if "path" in value and "sha256" in value:
                            path = session.out / value["path"]
                            if not path.is_file():
                                return {**value, "path": str((suite.out / value["path"]).relative_to(session.out))}
                            return value
                        return {k: rebase(v) for k, v in value.items()}
                    return [rebase(v) for v in value] if isinstance(value, list) else value
                original = rebase(original)
            else:
                original["cleanup"] = session.kube.recover_case(session.ledger.case)
            auxiliary = session.shutdown()
            if auxiliary:
                original["cleanup"]["success"] = False
                original["cleanup"].setdefault("failures", []).extend(auxiliary)
            if not original["cleanup"]["success"]:
                original["outcome"] = "failed"
            if not original.get("evidenceRefs"):
                proof = session.out / "case-boundary.json"
                atomic_json(proof, dict(outcome=original["outcome"], errorCode=original.get("errorCode"), cleanup=original["cleanup"]))
                original["evidenceRefs"] = [reference(proof, session.out)]
            original["finishedAt"] = stamp()
            try:
                session.safe(original)
            except ValueError:
                original = {**session.header(entry), "outcome": "failed", "errorCode": "public-evidence-sensitive-canary", "cleanup": original["cleanup"], "evidenceRefs": []}
            atomic_json(session.out / "receipt.json", original)
    return 0 if error is None and original["outcome"] == "passed" else 1
