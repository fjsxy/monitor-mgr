#!/usr/bin/env python3
"""只提供原型静态文件；不提供业务 API，不读取集群或凭据。"""
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
import argparse
import webbrowser

parser = argparse.ArgumentParser(description="启动 monitor-mgr 一期页面原型")
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--no-open", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parent
url = f"http://127.0.0.1:{args.port}/phase1-ui/?variant=A"
handler = partial(SimpleHTTPRequestHandler, directory=str(root))
with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
    print(f"页面原型：{url}", flush=True)
    print("所有数据与操作均为模拟；关闭后数据丢弃。Ctrl+C 停止。", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
