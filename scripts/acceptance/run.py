#!/usr/bin/env python3
"""一期唯一验收入口：精确列举、真实执行、独立校验、安全导出及原身份恢复。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from types import SimpleNamespace
import uuid

import registry
from session import BatchSession, atomic_json, stamp
from transport import ACTIVE
from execution import ExecutionSession

ROOT = Path(__file__).resolve().parents[2]


def source_state():
    """读取真实 Git 身份；正式结果不使用树相同或 bridge 代替同一个候选 SHA。"""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.PIPE).strip()
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "dirty": bool(git("status", "--porcelain"))}


def ref(path, root, kind="safe-json"):
    """公开引用只声明本 run 内原始文件，不通过修改原件凑 hash。"""
    path, root = Path(path), Path(root)
    return {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "visibility": "public", "kind": kind}


def run_batch(a):
    """完整期望 index 先于环境读取、等锁和任何 Suite 构造。"""
    full = registry.entries(a.profile, a.supplemental)
    selected = registry.entries(a.profile, a.supplemental, a.focus)
    actual = source_state()
    session = ExecutionSession(a.output, expected=[{"variantId": e["variantId"], "layer": e["layer"]} for e in full],
                           run_id=a.run_id, profile=a.profile, candidate=a.candidate, source=actual)
    session.index.update(manifestHash=registry.MANIFEST_HASH, supplemental=a.supplemental,
                         executionBoundary="acceptance", productGate=True, focused=bool(a.focus))
    session.flush()
    failure = None
    current = selected[0]
    cleanup = {"success": True, "scope": "本批原UID资源与辅助进程；环境由独立recover收据清理"}
    active = ACTIVE.set(session)
    try:
        # 关键：损坏/过期环境也保留完整未执行键；读取成功不代表允许按旧 kubeconfig 操作集群。
        environment = json.loads(Path(a.environment_json).read_text()) if a.environment_json else {}
        session.lane_path = a.api_lane or environment.get("apiLane")
        session.start()
        session.index["stage"] = "source_verification"
        if actual["commit"] != a.candidate or actual["dirty"] or not re.fullmatch(r"[a-f0-9]{40}", a.candidate) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,53}", a.run_id):
            raise ValueError("candidate-source-or-dirty")
        raw = subprocess.check_output(["git", "cat-file", "commit", a.candidate], cwd=ROOT)
        commit_file = session.out / "source" / "candidate.commit"
        commit_file.parent.mkdir(mode=0o700)
        commit_file.write_bytes(raw)
        session.index["sourceCommitRef"] = ref(commit_file, session.out, "source-commit")
        host_os = "darwin" if sys.platform == "darwin" else sys.platform
        host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
        session.index["environment"] = {"runtimeOS": host_os, "runtimeArch": host_arch, "targets": []}
        if (a.profile == "LOCAL_FULL" and (host_os, host_arch) != ("darwin", "arm64") or
                a.profile in ["CI_FAST", "CI_SMOKE"] and (host_os, host_arch) != ("linux", "amd64")):
            raise ValueError("actual-host-profile-mismatch")
        session.flush()
        q = [e for e in selected if e["layer"] == "Q"]
        session.run_go([e for e in q if not e["variantId"].startswith("REG21-")])
        session.run_protocol_q([e for e in q if e["variantId"].startswith("REG21-")])
        failed_q = any(r["outcome"] == "failed" for r in session.index["results"])
        if failed_q and a.fail_fast:
            raise ValueError("actual-q-failed")
        non_q = [e for e in selected if e["layer"] != "Q"]
        if non_q:
            import prepare
            if environment.get("source", environment.get("candidate")) != a.candidate or environment.get("profile") != a.profile:
                raise ValueError("environment-source-or-profile-mismatch")
            def environment_event(event):
                path = session.private / "environment-ledger.jsonl"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a") as stream:
                    stream.write(json.dumps(event) + "\n"); stream.flush(); os.fsync(stream.fileno())
                if "descriptor" in event:
                    atomic_json(session.private / "environment.json", event["descriptor"])
            def execute(argv, **kwargs):
                return session.processes.run(argv, defer_stop=not kwargs.get("label", "").startswith("build-"), **kwargs)
            try:
                descriptor = (prepare.prepare_environment(environment, execute=execute, ledger=environment_event)
                              if environment.get("lifecycle") == "prepare" else prepare.verify_environment(environment, execute=execute))
            except BaseException as error:
                if getattr(error, "descriptor", None):
                    atomic_json(session.private / "environment.json", error.descriptor)
                raise
            session.configure(descriptor)
            for index, entry in enumerate(non_q):
                current = entry
                session.checkpoint()
                session.index["stage"] = entry["layer"] + ":" + entry["variantId"]
                session.flush()
                if entry["variantId"].startswith("REG21-"):
                    import protocol_cases
                    case_out = session.out / "cases" / (entry["layer"] + "." + entry["variantId"])
                    request = dict(runId=a.run_id, profile=a.profile, candidateCommit=a.candidate, testedTree=actual["tree"],
                        runnerCommit=actual["commit"], dirty=False, source=actual, targets=descriptor["targets"], target="control",
                        apiLane=session.lane_path, kubectl=[descriptor["kubectl"]], output=str(case_out), environment=session.index["environment"])
                    ctx = SimpleNamespace(request=request, processes=session.processes, lane_fd=session.lane.fileno() if session.lane else None)
                    value = protocol_cases.run_case(ctx, entry)
                    original = {**session.header(entry), **value, "runtimeOS": "linux", "runtimeArch": host_arch}
                    session.store(entry, original, case_out, original_file=case_out / "receipt.json")
                elif entry["layer"] == "B" and entry["variantId"] in ["REG18-PATTERN-011", "REG19-HTTP-I64-001"]:
                    import derived_browser
                    derived_browser.derive(session, entry, a.parent_index)
                else:
                    run_business_child(session, entry, index, a.supplemental)
                record = next(r for r in session.index["results"] if r["variantId"] == entry["variantId"] and r["layer"] == entry["layer"])
                if record.get("receipt"):
                    actual_receipt = json.loads((session.out / record["receipt"]["path"]).read_text())
                    if actual_receipt.get("cleanup", {}).get("success") is not True:
                        raise ValueError("actual-case-cleanup-failed")
                if record["outcome"] != "passed" and a.fail_fast:
                    raise ValueError("actual-case-failed")
    except BaseException as error:
        failure = error
        record = next(r for r in session.index["results"] if r["variantId"] == current["variantId"] and r["layer"] == current["layer"])
        if record["outcome"] == "not_run":
            session.result(current["variantId"], current["layer"], outcome="environment_failed",
                           errorCode="setup-or-source-failed")
    finally:
        try:
            auxiliary = session.shutdown()
        except BaseException as error:
            failure = failure or error
            auxiliary = [dict(errorCode="batch-auxiliary-cleanup-unconfirmed")]
        if auxiliary:
            cleanup.update(success=False, failures=auxiliary)
        for child in session.index.get("caseRuns", []):
            receipt = session.out / child["output"] / "receipt.json"
            try:
                confirmed = child.get("stage") == "exited" and json.loads(receipt.read_text()).get("cleanup", {}).get("success") is True
            except (OSError, ValueError, TypeError):
                confirmed = False
            if not confirmed:
                cleanup["success"] = False
                cleanup.setdefault("failures", []).append(dict(variantId=child["variantId"], errorCode="case-cleanup-or-recovery-unconfirmed"))
        cleanup["environmentRetainedForArtifactValidation"] = bool(session.environment_descriptor.get("targets"))
        try:
            session.close(failure, cleanup)
        finally:
            ACTIVE.reset(active)
    success = failure is None and session.index["state"] == "completed" and cleanup["success"]
    gate_result = None
    if success and not a.focus:
        import gate
        try:
            gate_result = gate.validate(session.out / "index.json", a.candidate, a.profile, a.supplemental)
        except Exception as error:
            success = False
            gate_result = dict(success=False, code=getattr(error, "code", "strict-gate-failed"), productGate=False)
        atomic_json(session.out / "validation.json", gate_result)
    print(json.dumps({"runId": a.run_id, "profile": a.profile, "success": success,
                      "state": session.index["state"], "expected": len(full), "productGate": bool(gate_result and gate_result.get("productGate"))}))
    return 0 if success else 1


def run_business_child(session, entry, index, supplemental):
    """主批次先持久case意图，再用同租约真实child隔离storage全局覆盖与所有辅助进程。"""
    case_out = session.out / "cases" / (entry["layer"] + "." + entry["variantId"])
    child_id = session.index["runId"][:35] + "-c" + str(index)
    request = dict(entry=dict(variantId=entry["variantId"], layer=entry["layer"]), profile=session.index["profile"],
        candidate=session.index["candidateCommit"], tree=session.index["testedTree"], parentRunId=session.index["runId"], runId=child_id,
        output=str(case_out), supplemental=supplemental, environment=session.environment_descriptor, apiLane=session.lane_path)
    path = session.private / ("case-" + uuid.uuid4().hex + ".json")
    atomic_json(path, request)
    record = dict(variantId=entry["variantId"], layer=entry["layer"], output=str(case_out.relative_to(session.out)),
                  ledgerPath=str((case_out / "ledger.json").relative_to(session.out)),
                  requestPath=str(path.relative_to(session.out)),
                  requestSHA256=hashlib.sha256(path.read_bytes()).hexdigest(), stage="intent", startedAt=stamp())
    session.index.setdefault("caseRuns", []).append(record)
    def manifest():
        atomic_json(session.out / "case-runs.json", dict(schemaVersion=1, runId=session.index["runId"], profile=session.index["profile"],
            candidateCommit=session.index["candidateCommit"], testedCommit=session.index["testedCommit"], testedTree=session.index["testedTree"],
            runnerCommit=session.index["runnerCommit"], dirty=session.index["dirty"], source=dict(commit=session.index["testedCommit"], tree=session.index["testedTree"], dirty=False),
            targets=session.index["environment"]["targets"], entries=session.index["caseRuns"]))
    manifest()
    session.flush()
    command = [sys.executable, str(Path(__file__)), "internal-case", "--profile", session.index["profile"],
               "--candidate", session.index["candidateCommit"], "--output", str(case_out), "--request", str(path),
               "--request-sha256", record["requestSHA256"]]
    options = {}
    if session.lane:
        command += ["--inherited-lane-fd", str(session.lane.fileno())]
        options["pass_fds"] = (session.lane.fileno(),)
    # child接管信号finally；父不能在其正常GC期间因第二次信号重入/强杀。
    try:
        with session.cleanup_phase():
            result = session.processes.run(command, label="business-case-child", timeout=2400,
                                           cooperative_signals=lambda: session.signals, **options)
    except BaseException:
        # 关键：原child被KILL或timeout时没有finally成功；先独立恢复，父仍保有整批租约。
        recover_business_child(session, entry)
        raise
    receipt = case_out / "receipt.json"
    try:
        record.update(stage="exited", commandExitCode=result.returncode, finishedAt=stamp())
        manifest()
        if not receipt.is_file():
            raise ValueError("business-child-original-receipt-missing")
        original = json.loads(receipt.read_text())
        child_index = case_out / "index.json"
        child_state = json.loads(child_index.read_text()) if child_index.is_file() else {}
        if (child_state.get("runId") != child_id or child_state.get("candidateCommit") != session.index["candidateCommit"] or
                child_state.get("cleanup", {}).get("success") is not True or original.get("cleanup", {}).get("success") is not True):
            raise ValueError("business-child-finally-unconfirmed")
    except BaseException:
        recover_business_child(session, entry)
        raise
    canaries = case_out / ".private" / "canaries.json"
    if canaries.is_file():
        session.register_secrets(*json.loads(canaries.read_text()))
    if result.returncode and original.get("outcome") == "passed":
        raise ValueError("business-child-exit-result-mismatch")
    session.store(entry, original, case_out, original_file=receipt)


def recover_business_child(session, entry):
    """新的恢复child只写独立目录；恢复成功也不能改原child的失败/无finally记录。"""
    from transport import ProcessRunner
    destination = session.out.with_name(session.out.name + "-recovery-" + uuid.uuid4().hex[:12])
    command = [sys.executable, str(Path(__file__)), "recover", "--output", str(session.out),
               "--environment-json", str(session.private / "environment.json"), "--candidate", session.index["candidateCommit"],
               "--recovery-output", str(destination)]
    options = {}
    if session.lane:
        command += ["--inherited-lane-fd", str(session.lane.fileno())]
        options["pass_fds"] = (session.lane.fileno(),)
    # 不向旧run的process ledger追加恢复自身；原child的无finally及原identity gap均保持不变。
    runner = ProcessRunner(session.private / "recovery-launch" / uuid.uuid4().hex)
    with session.cleanup_phase():
        try:
            result = runner.run(command, label="independent-business-recovery", timeout=3600, **options)
            path = destination / "recovery.json"
            report = json.loads(path.read_text()) if path.is_file() else {}
            success = result.returncode == 0 and report.get("success") is True
        except BaseException:
            success = False
    session.index.setdefault("independentRecoveries", []).append(dict(variantId=entry["variantId"], layer=entry["layer"],
        recoveryOutputName=destination.name, success=success, productGate=False, originalResultUnchanged=True))
    session.flush()
    return success


def internal_case(a):
    """内部child不声称完整profile成功；原业务原件由父index逐键核验。"""
    actual = source_state()
    full = registry.entries(a.profile)
    session = ExecutionSession(a.output, expected=[dict(variantId=e["variantId"], layer=e["layer"]) for e in full],
        run_id="internal-case", profile=a.profile, candidate=a.candidate, source=actual,
        inherited_lane_fd=a.inherited_lane_fd)
    session.index.update(executionScope="internal-case", productGate=False)
    failure = None
    code = 1
    token = ACTIVE.set(session)
    try:
        path = Path(a.request)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            mode = os.fstat(stream.fileno())
            if not stat.S_ISREG(mode.st_mode) or mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) != 0o600:
                raise ValueError("case-request-private-mode-required")
            raw = stream.read()
        if hashlib.sha256(raw).hexdigest() != a.request_sha256:
            raise ValueError("case-request-binding-mismatch")
        request = json.loads(raw)
        if (actual["commit"] != a.candidate or actual["dirty"] or request["candidate"] != a.candidate or request["tree"] != actual["tree"]
                or request["profile"] != a.profile or request["output"] != str(session.out)):
            raise ValueError("internal-source-or-request-mismatch")
        session.index["runId"] = request["runId"]
        session.lane_path = request["apiLane"]
        session.start()
        descriptor = request["environment"]
        if descriptor["source"] != a.candidate or descriptor["profile"] != a.profile:
            raise ValueError("internal-environment-source-mismatch")
        session.configure(descriptor)
        entries = registry.entries(a.profile, request["supplemental"])
        entry = next(e for e in entries if e["variantId"] == request["entry"]["variantId"] and e["layer"] == request["entry"]["layer"])
        from business import run_case
        code = run_case(session, entry)
    except BaseException as error:
        failure = error
    finally:
        try:
            auxiliary = session.shutdown()
        except BaseException as error:
            failure = failure or error
            auxiliary = [dict(errorCode="internal-auxiliary-cleanup-unconfirmed")]
        receipt = session.out / "receipt.json"
        try:
            cleanup = json.loads(receipt.read_text()).get("cleanup", {})
        except (OSError, ValueError, TypeError):
            cleanup = dict(success=False, scope="internal-case-no-original-receipt")
        if auxiliary:
            cleanup["success"] = False
            cleanup.setdefault("failures", []).extend(auxiliary)
        try:
            session.close(failure, cleanup)
        finally:
            ACTIVE.reset(token)
    return code if failure is None else 1


def main():
    """所有对外子命令使用同一 profile/候选及外置目录契约。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--profile", choices=registry.PROFILES, required=True)
    listing.add_argument("--supplemental", action="store_true")
    listing.add_argument("--focus", nargs="+")
    run = commands.add_parser("run")
    run.add_argument("--profile", choices=registry.PROFILES, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--candidate", required=True)
    run.add_argument("--environment-json")
    run.add_argument("--api-lane")
    run.add_argument("--supplemental", action="store_true")
    run.add_argument("--focus", nargs="+")
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument("--parent-index")
    internal = commands.add_parser("internal-case")
    internal.add_argument("--profile", choices=registry.PROFILES, required=True)
    internal.add_argument("--candidate", required=True)
    internal.add_argument("--output", required=True)
    internal.add_argument("--request", required=True)
    internal.add_argument("--request-sha256", required=True)
    internal.add_argument("--inherited-lane-fd", type=int)
    recovering = commands.add_parser("recover")
    recovering.add_argument("--output", required=True)
    recovering.add_argument("--environment-json", required=True)
    recovering.add_argument("--candidate", required=True)
    recovering.add_argument("--recovery-output", required=True)
    recovering.add_argument("--environment-only", action="store_true")
    recovering.add_argument("--inherited-lane-fd", type=int)
    for name in ["validate-results", "export"]:
        p = commands.add_parser(name)
        p.add_argument("--index", required=True)
        p.add_argument("--candidate", required=True)
        p.add_argument("--profile", choices=registry.PROFILES, required=True)
        p.add_argument("--supplemental", action="store_true")
        if name == "export":
            p.add_argument("--destination", required=True)
            p.add_argument("--diagnostic", action="store_true")
    try:
        if a := parser.parse_args():
            if a.command == "list":
                print(json.dumps(registry.entries(a.profile, a.supplemental, a.focus), ensure_ascii=False, indent=2))
                return 0
            if a.command == "run":
                return run_batch(a)
            if a.command == "internal-case":
                return internal_case(a)
            if a.command == "recover":
                import recovery
                return recovery.recover(a)
            if a.command == "validate-results":
                import gate
                print(json.dumps(gate.validate(a.index, a.candidate, a.profile, a.supplemental)))
                return 0
            if a.command == "export":
                import exporter
                canary_path = Path(a.index).parent / ".private" / "canaries.json"
                canaries = json.loads(canary_path.read_text()) if canary_path.is_file() else []
                print(json.dumps(exporter.export(a.index, a.destination, a.candidate, a.profile, a.supplemental,
                                                  diagnostic=a.diagnostic, canaries=canaries)))
                return 0
    except Exception as error:
        print(json.dumps({"success": False, "code": getattr(error, "code", "runner-input-or-protocol-error")}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
