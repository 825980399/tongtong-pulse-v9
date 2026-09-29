# -*- coding: utf-8 -*-
"""第104批 T-104b（D157）：HTTPServer 队头阻塞验证。

离线单测：用 ThreadingHTTPServer 起一个含慢端点的服务，并发请求慢端点时，
快端点必须不被阻塞（先红后绿：若用单线程 HTTPServer，快端点会被慢端点拖住）。
T0 实测：生产仅 health_ui/web_chat 两处且均为 ThreadingHTTPServer，无裸 HTTPServer 残留。
"""
import time
import threading
import urllib.request

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import pytest


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(1.0)  # 模拟慢端点
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(url, timeout=5):
    return urllib.request.urlopen(url, timeout=timeout).read()


def test_threading_server_no_head_of_line_blocking(server):
    """慢端点进行中，快端点必须快速返回（无队头阻塞）。"""
    slow_start = time.time()
    slow_result = {}

    def slow():
        slow_result["v"] = _get(server + "/slow", timeout=5)

    st = threading.Thread(target=slow)
    st.start()
    # 立即请求快端点
    fast_start = time.time()
    fast = _get(server + "/fast", timeout=2)
    fast_elapsed = time.time() - fast_start
    st.join(timeout=5)
    slow_elapsed = time.time() - slow_start

    assert fast == b"ok"
    # 快端点应在慢端点完成前返回（远小于 1s 的慢端点耗时）
    assert fast_elapsed < 0.5, f"快端点被阻塞：{fast_elapsed:.2f}s"
    assert slow_elapsed >= 1.0, "慢端点应耗时约 1s"
    assert slow_result.get("v") == b"ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
