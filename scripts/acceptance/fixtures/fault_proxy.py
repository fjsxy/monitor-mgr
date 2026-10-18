"""只供独占测试 fixture 使用的 TLS Kubernetes API 故障代理。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid


MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
METHODS = MUTATING | {"GET", "HEAD", "OPTIONS"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length", "host"}


class ConfigError(Exception):
    pass


class RuleError(Exception):
    pass


def private_info(path):
    p = Path(path)
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise ConfigError("fixture private file must be owned by runtime UID and mode 0600")
    return info


def private_text(path):
    p = Path(path)
    info = private_info(p)
    if info.st_size > 65536:
        raise ConfigError("fixture private file is too large")
    return p.read_text()


def clean_path(target):
    parts = urllib.parse.urlsplit(target)
    if (parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/")
            or parts.path.startswith("//") or any(ord(c) < 32 for c in target)):
        raise RuleError("fixture requires an origin-form API path")
    return parts.path


@dataclass
class Rule:
    action: str
    method: str
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event: threading.Event = field(default_factory=threading.Event)
    claimed: bool = False
    completed: bool = False
    released: bool = False
    cancelled: bool = False
    fault_applied: bool = False
    stage: str = "armed"
    matching_requests: int = 0
    upstream_status: int | None = None

    def public(self):
        return {"id": self.id, "action": self.action, "method": self.method, "path": self.path,
                "claimed": self.claimed, "completed": self.completed, "released": self.released,
                "cancelled": self.cancelled, "faultApplied": self.fault_applied, "stage": self.stage,
                "matchingRequests": self.matching_requests, "upstreamStatus": self.upstream_status}


class FaultState:
    def __init__(self):
        self.lock = threading.Lock()
        self.rule = None
        self.request_number = 0
        self.block_reads = False

    def snapshot(self):
        with self.lock:
            return {"rule": self.rule.public() if self.rule else None, "requestCount": self.request_number}

    def arm(self, body):
        if set(body) != {"action", "method", "path"}:
            raise RuleError("fixture rule fields rejected")
        action, method, path = body["action"], body["method"], body["path"]
        if action not in {"drop_after_upstream", "drop_and_block_reads", "pause_before_send"} or method not in METHODS:
            raise RuleError("fixture rule action or method rejected")
        if not isinstance(path, str) or clean_path(path) != path or "?" in path:
            raise RuleError("fixture rule requires an exact path without query")
        if action in {"drop_after_upstream","drop_and_block_reads"} and method not in MUTATING:
            raise RuleError("fixture response drop requires a mutating HTTP method")
        with self.lock:
            if self.rule is not None and not self.rule.completed:
                raise RuleError("fixture active rule must be reset first")
            self.rule = Rule(action, method, path)
            return self.rule.public()

    def claim(self, method, target):
        path = clean_path(target)
        is_dry_run = "dryRun" in urllib.parse.parse_qs(urllib.parse.urlsplit(target).query,
                                                      keep_blank_values=True)
        with self.lock:
            self.request_number += 1
            number = self.request_number
            rule = self.rule
            if rule is None or rule.cancelled or rule.method != method or rule.path != path or is_dry_run:
                return number, None
            rule.matching_requests += 1
            if rule.claimed:
                return number, None
            rule.claimed = True
            rule.stage = "paused_before_send" if rule.action == "pause_before_send" else "forwarding"
            rule.fault_applied = rule.action == "pause_before_send"
            return number, rule

    def release(self, rule_id):
        with self.lock:
            if not self.rule or self.rule.id != rule_id or self.rule.action != "pause_before_send" or self.rule.cancelled:
                raise RuleError("fixture pause rule is not active")
            self.rule.released = True
            self.rule.event.set()
            return self.rule.public()

    def reset(self):
        with self.lock:
            old = self.rule
            if old:
                old.cancelled = True
                old.event.set()
            self.rule = None
            self.block_reads = False

    def wait(self, rule, timeout):
        awakened = rule.event.wait(timeout)
        with self.lock:
            if not awakened or rule.cancelled or not rule.released:
                rule.completed = True
                rule.stage = "pause_aborted" if rule.cancelled else "pause_timeout"
                return False, rule.stage
            rule.stage = "forwarding"
            return True, rule.stage

    def complete(self, rule, status):
        if rule is None:
            return False
        with self.lock:
            rule.upstream_status = status
            rule.completed = True
            drop = (rule.action in {"drop_after_upstream","drop_and_block_reads"} and not rule.cancelled and status is not None
                    and 200 <= status < 300)
            if rule.action in {"drop_after_upstream","drop_and_block_reads"}:
                rule.fault_applied = drop
            if drop and rule.action=="drop_and_block_reads":
                self.block_reads=True
            rule.stage = "response_dropped" if drop else "completed_without_drop"
            return drop


class EventLog:
    def __init__(self, path):
        p = Path(path)
        if p.exists():
            private_info(p)
        fd = os.open(p, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        self.stream = os.fdopen(fd, "a", encoding="utf-8")
        self.lock = threading.Lock()
        self.closed = False

    def emit(self, number, method, path, stage, rule=None, status=None, uid=None, rv=None):
        # 严格字段白名单：不接收任意异常、请求头、body 或原始 URI。
        data = {"at": datetime.now(timezone.utc).isoformat(), "proxyRequestId": number,
                "method": method, "path": path, "stage": stage, "ruleId": rule.id if rule else None,
                "upstreamStatus": status, "metadataUID": uid, "metadataRV": rv}
        with self.lock:
            if not self.closed:
                self.stream.write(json.dumps(data, ensure_ascii=False) + "\n")
                self.stream.flush()
                os.fsync(self.stream.fileno())

    def close(self):
        with self.lock:
            self.closed = True
            self.stream.close()


class VerifiedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, backend, context, timeout):
        super().__init__(backend["host"], backend["port"], timeout=timeout, context=context)
        self.backend_server_name = backend["server_name"]

    def connect(self):
        # 固定物理目标与独立校验名称；不读代理环境变量、不做重定向或重试。
        raw = socket.create_connection((self.host, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.backend_server_name)
        except Exception:
            raw.close()
            raise


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # stdlib 默认 traceback 可能带入请求内容，因此fixture禁止默认错误输出。
        pass


class TLSServer(FixtureServer):
    def get_request(self):
        raw, address = super().get_request()
        raw.settimeout(5)
        try:
            return self.frontend_context.wrap_socket(raw, server_side=True), address
        except Exception:
            raw.close()
            raise


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.json_response(code, {"code": "FIXTURE_HTTP_REJECTED"})

    def json_response(self, status, body):
        data = json.dumps(body).encode()
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ssl.SSLError):
            pass


def metadata(body, headers):
    try:
        if "json" not in headers.get("Content-Type", ""):
            return None, None
        if headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            return None, None
        document = json.loads(body)
        meta = document.get("metadata", {})
        uid = meta.get("uid") or document.get("details", {}).get("uid")
        rv = meta.get("resourceVersion")
        safe = lambda value: value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) else None
        return safe(uid), safe(rv)
    except (ValueError, TypeError, AttributeError, OSError, EOFError):
        return None, None


class APIHandler(QuietHandler):
    def do_GET(self):
        self.forward()

    do_HEAD = do_OPTIONS = do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

    def forward(self):
        app = self.server.app
        self.connection.settimeout(app.limits["io_timeout_seconds"])
        try:
            path = clean_path(self.path)
            if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Length", []) not in ([], [self.headers.get("Content-Length")]):
                self.json_response(400, {"code": "FIXTURE_UNSUPPORTED_REQUEST_FRAMING"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > app.limits["max_body_bytes"]:
                self.json_response(413, {"code": "FIXTURE_BODY_LIMIT"})
                return
            if "watch" in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query):
                self.json_response(400, {"code": "FIXTURE_WATCH_NOT_SUPPORTED"})
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self.json_response(400, {"code": "FIXTURE_INCOMPLETE_REQUEST"})
                return
            number, rule = app.state.claim(self.command, self.path)
        except (RuleError, ValueError, OSError):
            self.json_response(400, {"code": "FIXTURE_REQUEST_REJECTED"})
            return
        app.log.emit(number, self.command, path, "frontend_received", rule)
        with app.state.lock:
            blocked=app.state.block_reads and self.command=="GET"
        if blocked:
            app.log.emit(number,self.command,path,"controlled_read_rejected",status=403)
            self.json_response(403,{"code":"FIXTURE_READ_BLOCKED"})
            return
        if rule and rule.action == "pause_before_send":
            app.log.emit(number, self.command, path, "paused_before_send", rule)
            proceed, stage = app.state.wait(rule, app.limits["pause_timeout_seconds"])
            if not proceed:
                app.log.emit(number, self.command, path, stage, rule)
                self.json_response(503 if stage == "pause_aborted" else 504, {"code": "FIXTURE_PAUSE_ENDED_WITHOUT_SEND"})
                return
        conn = VerifiedHTTPSConnection(app.backend, app.backend_context, app.limits["io_timeout_seconds"])
        upstream_completed = False
        response_status = uid = rv = None
        try:
            conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", app.backend["server_name"])
            connection_headers = {x.strip().lower() for x in self.headers.get("Connection", "").split(",")}
            for name, value in self.headers.items():
                lower = name.lower()
                if (lower in HOP_HEADERS | connection_headers | {"authorization", "cookie", "x-fault-control", "forwarded"}
                        or lower.startswith(("impersonate-", "x-remote-", "x-forwarded-"))):
                    continue
                conn.putheader(name, value)
            conn.putheader("Content-Length", str(len(body)))
            conn.putheader("Connection", "close")
            app.log.emit(number, self.command, path, "upstream_send_started", rule)
            conn.endheaders(body)
            response = conn.getresponse()
            response_body = response.read(app.limits["max_response_bytes"] + 1)
            if len(response_body) > app.limits["max_response_bytes"]:
                raise OSError("fixture response limit")
            uid, rv = metadata(response_body, response.headers)
            response_status = response.status
            upstream_completed = True
            app.log.emit(number, self.command, path, "upstream_completed", rule, response.status, uid, rv)
            drop = app.state.complete(rule, response.status)
            if drop:
                app.log.emit(number, self.command, path, "response_dropped", rule, response.status, uid, rv)
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self.close_connection = True
            self.send_response(response.status)
            connection_headers = {x.strip().lower() for x in response.getheader("Connection", "").split(",")}
            for name, value in response.getheaders():
                if name.lower() not in HOP_HEADERS | connection_headers:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response_body)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            if upstream_completed:
                # 前端断开不能抹掉已取得的上游成功身份，也不能在已发header后再写502。
                app.log.emit(number, self.command, path, "frontend_response_io_failure", rule,
                             response_status, uid, rv)
                self.close_connection = True
            else:
                app.state.complete(rule, None)
                app.log.emit(number, self.command, path, "upstream_io_failure", rule)
                self.json_response(502, {"code": "FIXTURE_UPSTREAM_FAILURE"})
        finally:
            conn.close()


class ControlHandler(QuietHandler):
    def do_GET(self):
        self.control()

    do_POST = do_GET

    def control(self):
        app = self.server.app
        self.connection.settimeout(5)
        supplied = self.headers.get("X-Fault-Control", "")
        if not hmac.compare_digest(supplied.encode(), app.control_token.encode()):
            self.json_response(403, {"code": "FIXTURE_CONTROL_DENIED"})
            return
        try:
            path = clean_path(self.path)
            if self.command == "GET" and path == "/state":
                self.json_response(200, app.state.snapshot())
                return
            if self.command != "POST" or self.headers.get("Transfer-Encoding"):
                raise RuleError("fixture control request rejected")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 4096:
                raise RuleError("fixture control body limit")
            body = json.loads(self.rfile.read(size) or b"{}")
            if path == "/rule":
                self.json_response(200, {"rule": app.state.arm(body)})
            elif path == "/release" and set(body) == {"ruleId"}:
                self.json_response(200, {"rule": app.state.release(body["ruleId"])})
            elif path == "/reset" and body == {}:
                app.state.reset()
                self.json_response(200, app.state.snapshot())
            else:
                raise RuleError("fixture control route rejected")
        except (RuleError, ValueError, TypeError, OSError):
            self.json_response(409, {"code": "FIXTURE_CONTROL_REJECTED"})


class ProxyFixture:
    @classmethod
    def from_file(cls, path):
        try:
            return cls(json.loads(private_text(path)))
        except ConfigError:
            raise
        except Exception:
            raise ConfigError("fixture configuration rejected") from None

    def __init__(self, cfg):
        self.backend = cfg["backend"]
        front = cfg["frontend"]
        control = cfg["control"]
        self.limits = {"io_timeout_seconds": 20, "pause_timeout_seconds": 30,
                       "max_body_bytes": 8 * 1024 * 1024, "max_response_bytes": 16 * 1024 * 1024}
        self.limits.update(cfg.get("limits", {}))
        if (control.get("bind", "127.0.0.1") != "127.0.0.1" or not self.backend["host"]
                or not self.backend["server_name"] or not 1 <= int(self.backend["port"]) <= 65535):
            raise ConfigError("fixture endpoint rejected")
        if not all(isinstance(self.limits[k], (int, float)) and 0 < self.limits[k] <= 300
                   for k in ("io_timeout_seconds", "pause_timeout_seconds")):
            raise ConfigError("fixture time limit rejected")
        if not all(isinstance(self.limits[k], int) and 0 < self.limits[k] <= 64 * 1024 * 1024
                   for k in ("max_body_bytes", "max_response_bytes")):
            raise ConfigError("fixture byte limit rejected")
        private_text(self.backend["client_key_file"])
        private_text(front["key_file"])
        self.control_token = private_text(control["token_file"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", self.control_token):
            raise ConfigError("fixture control token rejected")
        self.backend_context = ssl.create_default_context(cafile=self.backend["ca_file"])
        self.backend_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.backend_context.load_cert_chain(self.backend["client_cert_file"], self.backend["client_key_file"])
        frontend_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        frontend_context.minimum_version = ssl.TLSVersion.TLSv1_2
        frontend_context.load_cert_chain(front["cert_file"], front["key_file"])
        frontend_context.load_verify_locations(front["client_ca_file"])
        frontend_context.verify_mode = ssl.CERT_REQUIRED
        self.state = FaultState()
        self.log = EventLog(cfg["event_log_file"])
        self.api_server = None
        try:
            self.api_server = TLSServer((front.get("bind", "0.0.0.0"), int(front["port"])), APIHandler)
            self.api_server.frontend_context = frontend_context
            self.control_server = FixtureServer(("127.0.0.1", int(control["port"])), ControlHandler)
        except Exception:
            if self.api_server:
                self.api_server.server_close()
            self.log.close()
            raise
        self.api_server.app = self.control_server.app = self
        self.api_address = self.api_server.server_address
        self.control_address = self.control_server.server_address
        self.threads = []

    def start(self):
        for server in (self.api_server, self.control_server):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def stop(self):
        self.state.reset()
        for server in (self.api_server, self.control_server):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.log.close()


def control_call(base_url, token_file, action, body=None):
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ConfigError("control URL must be a localhost port-forward")
    routes = {"state": ("GET", "/state"), "arm": ("POST", "/rule"),
              "release": ("POST", "/release"), "reset": ("POST", "/reset")}
    method, path = routes[action]
    token = private_text(token_file).strip()
    request = urllib.request.Request(base_url.rstrip("/") + path,
                                     data=None if method == "GET" else json.dumps(body or {}).encode(),
                                     method=method, headers={"X-Fault-Control": token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        raise RuleError("fixture control request rejected") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--config", required=True)
    control = sub.add_parser("control")
    control.add_argument("--url", required=True)
    control.add_argument("--token-file", required=True)
    control.add_argument("--action", choices=["state", "arm", "release", "reset"], required=True)
    control.add_argument("--rule-file")
    control.add_argument("--rule-id")
    args = parser.parse_args()
    try:
        if args.command == "control":
            body = json.loads(Path(args.rule_file).read_text()) if args.rule_file else None
            if args.rule_id:
                body = {"ruleId": args.rule_id}
            print(json.dumps(control_call(args.url, args.token_file, args.action, body), ensure_ascii=False))
            return 0
        app = ProxyFixture.from_file(args.config).start()
        done = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: done.set())
        signal.signal(signal.SIGINT, lambda *_: done.set())
        try:
            done.wait()
        finally:
            app.stop()
        return 0
    except Exception:
        # 禁止把配置/证书/网络异常的原始内容写到控制台。
        print("fixture rejected configuration or control request", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
