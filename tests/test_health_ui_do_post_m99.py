"""T-99c 门控测试：health_ui 面板「应用预设」POST 路径。

红（修复前）：HealthHandler 仅有 do_GET，无 do_POST；前端 fetch POST
/params/apply_preset 命中默认处理器 → 返回 501，预设无法应用。
绿（修复后）：新增 do_POST 处理 /params/apply_preset + 同源(CSRF)校验，
POST 返回 200，跨站 Origin 返回 403，且 GET 不再触发状态变更（404）。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from functions.health_ui import HealthHandler  # noqa: E402


def _start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class HealthUIDoPostTest(unittest.TestCase):
    def setUp(self):
        self.srv, self.port = _start_server()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _request(self, method, path, origin=None):
        url = "http://127.0.0.1:{}{}".format(self.port, path)
        req = urllib.request.Request(url, data=b"", method=method)
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore")

    def test_do_post_method_exists(self):
        self.assertTrue(hasattr(HealthHandler, "do_POST"), "HealthHandler 缺少 do_POST 方法（T-99c 未修复）")

    def test_apply_preset_post_not_501(self):
        status, body = self._request("POST", "/params/apply_preset?key=__m99_test_key__")
        self.assertNotEqual(status, 501, "POST /params/apply_preset 仍返回 501（do_POST 未生效）")
        self.assertEqual(status, 200, "同源 POST 应用预设应返回 200，实际 {}".format(status))
        data = json.loads(body)
        self.assertIn("success", data, "响应体应含 success 字段")

    def test_apply_preset_csrf_rejected(self):
        status, _ = self._request("POST", "/params/apply_preset?key=x", origin="http://evil.example.com")
        self.assertEqual(status, 403, "跨站 Origin 必须被拒绝(403)，实际 {}".format(status))

    def test_apply_preset_get_is_404(self):
        status, _ = self._request("GET", "/params/apply_preset?key=x")
        self.assertEqual(status, 404, "GET 不应触发状态变更，应返回 404，实际 {}".format(status))


if __name__ == "__main__":
    unittest.main()
