"""★479：语义编码 sidecar（**必须用 3.12 跑**）。

## 为什么需要它

3.14t 是自由线程构建，`fastembed` / `onnxruntime` 没有 cp314t wheel ⇒
`VectorEncoder` 只能走关键词通道 ⇒ 语义缓存、知识检索、经验召回全部退化。
而 3.12 里模型齐备（`data/models/fast-bge-small-zh-v1.5/`，94MB ONNX），实测：
加载 0.11s(热)/1.56s(冷)、编码 0.004~0.006s、维度 512，
与存量 `data/knowledge/vectors.npz`（14899×512）最高相似 0.6525
⇒ **同一语义空间，存量向量无需重建**。

## 为什么用 localhost HTTP 而不是管道（★踩坑后的结论）

第一版用「长驻 + stdin/stdout 管道」，在 3.14t 下会死：
主进程退出时报 `OSError: [WinError 6] 句柄无效`（`Popen.__del__` 里），
生产里 sidecar 崩掉后再起不来，`VectorEncoder` 一直拿到空向量
⇒ 刷 `维度异常: 期望 (200,512)，实际 (0,)`。

**自由线程构建下长期持有 Popen 管道句柄不可靠。**
所以改成：管道**只用一次**（读端口号），之后全走 `127.0.0.1:<随机端口>` 的 HTTP。
HTTP 天然支持超时、不受句柄失效影响、进程死了立刻连接失败可重启。

## 协议

启动后在 stdout 打**一行**握手（随后不再用管道）::

    {"ok": true, "ready": true, "port": 51234, "dim": 512, "model": "...", "load_cost": 0.11}

之后：

    POST http://127.0.0.1:<port>/embed
      {"texts": ["文本1","文本2"], "batch_size": 32}
    → {"ok": true, "dim": 512, "vectors": [[...]], "count": N, "elapsed": 0.004}

    GET  http://127.0.0.1:<port>/ping
    → {"ok": true, "ready": true, "dim": 512}

## 用法

    python tools/semantic/encoder_sidecar.py              # HTTP 服务（长驻）
    python tools/semantic/encoder_sidecar.py --selftest   # 自检一次就退出
    python tools/semantic/encoder_sidecar.py --stdio      # 旧的管道模式（仅调试用）
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_CACHE = "data/models"
EXPECT_DIM = 512
MAX_TEXTS = 512          # 单请求上限，防一次塞爆
_ENC_LOCK = threading.Lock()   # fastembed 的 embed 不保证线程安全


def _load_model(model_name: str = DEFAULT_MODEL, cache_dir: str = DEFAULT_CACHE):
    """加载本地模型。**只读本地缓存**，不联网。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # ★关键：本地已有模型时绝不探远端（否则代理 502 会把启动拖死）
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from fastembed import TextEmbedding
    _t0 = time.time()
    _m = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=4)
    return _m, round(time.time() - _t0, 2)


def _encode(model, texts: list[str], batch_size: int = 32) -> dict:
    _t0 = time.time()
    if len(texts) > MAX_TEXTS:
        return {"ok": False, "dim": EXPECT_DIM, "vectors": [],
                "error": f"单请求文本数上限 {MAX_TEXTS}，收到 {len(texts)}"}
    with _ENC_LOCK:
        _vecs = [list(map(float, v))
                 for v in model.embed(texts, batch_size=batch_size)]
    return {
        "ok": True,
        "dim": len(_vecs[0]) if _vecs else EXPECT_DIM,
        "vectors": _vecs,
        "count": len(_vecs),
        "elapsed": round(time.time() - _t0, 4),
    }


class _Handler(BaseHTTPRequestHandler):
    """极简 JSON 服务。模型句柄挂在类属性上（由 main 注入）。"""

    model = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        """★静音默认日志（它会往 stderr 打，既是噪音也可能撑爆缓冲区）。"""
        return

    def _send(self, obj: dict, code: int = 200) -> None:
        try:
            _b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(_b)))
            self.end_headers()
            self.wfile.write(_b)
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="encoder_sidecar::_send", level="debug")

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/ping":
            self._send({"ok": True, "ready": self.model is not None,
                        "dim": EXPECT_DIM, "model": DEFAULT_MODEL})
        else:
            self._send({"ok": False, "error": "unknown path"}, 404)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/embed":
            self._send({"ok": False, "error": "unknown path"}, 404)
            return
        try:
            _n = int(self.headers.get("Content-Length") or 0)
            _body = self.rfile.read(_n) if _n > 0 else b"{}"
            _req = json.loads(_body.decode("utf-8", "replace") or "{}")
        except (ValueError, OSError) as _e:
            silent_exc(_e, where="encoder_sidecar::do_POST read", level="warning")
            self._send({"ok": False, "dim": EXPECT_DIM, "vectors": [],
                        "error": f"请求解析失败: {type(_e).__name__}"}, 400)
            return
        _texts = _req.get("texts") or []
        if not isinstance(_texts, list) or not _texts:
            self._send({"ok": False, "dim": EXPECT_DIM, "vectors": [],
                        "error": "texts 为空或非数组"}, 400)
            return
        try:
            _resp = _encode(self.model, [str(t) for t in _texts],
                            int(_req.get("batch_size") or 32))
        except Exception as _e:
            silent_exc(_e, where="encoder_sidecar::do_POST encode", level="warning")
            _resp = {"ok": False, "dim": EXPECT_DIM, "vectors": [],
                     "error": f"{type(_e).__name__}: {str(_e)[:200]}"}
        self._send(_resp)


def _serve_http() -> int:
    try:
        _model, _cost = _load_model()
    except Exception as _e:
        silent_exc(_e, where="encoder_sidecar::load", level="error")
        sys.stdout.write(json.dumps({
            "ok": False, "ready": False, "dim": EXPECT_DIM,
            "error": f"{type(_e).__name__}: {str(_e)[:200]}",
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 1

    _watch_parent()
    _Handler.model = _model
    try:
        _srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as _e:
        silent_exc(_e, where="encoder_sidecar::bind", level="error")
        sys.stdout.write(json.dumps({
            "ok": False, "ready": False, "dim": EXPECT_DIM,
            "error": f"端口绑定失败: {str(_e)[:150]}",
        }, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 1
    _srv.daemon_threads = True
    _port = int(_srv.server_address[1])

    # ★唯一的管道用途：把端口告诉父进程。之后父进程不再依赖管道。
    sys.stdout.write(json.dumps({
        "ok": True, "ready": True, "port": _port, "dim": EXPECT_DIM,
        "model": DEFAULT_MODEL, "load_cost": _cost, "mode": "http",
    }, ensure_ascii=False) + "\n")
    sys.stdout.flush()

    try:
        _srv.serve_forever(poll_interval=0.5)
    except (KeyboardInterrupt, OSError) as _e:
        silent_exc(_e, where="encoder_sidecar::serve_forever", level="warning")
    finally:
        try:
            _srv.server_close()
        except OSError as _e:
            silent_exc(_e, where="encoder_sidecar::server_close", level="debug")
    return 0


# 防御性分支的「同类只记一次」容器（避免每次探活都刷同一条）
_SEEN_DEFER: set = set()

# 看护间隔（秒）。生产 30s 足够；单测/调试可用环境变量调小以便验证。
try:
    _PPID_WATCH_SEC = float(os.environ.get("PULSE_SIDECAR_PPID_WATCH_SEC") or 30.0)
except (TypeError, ValueError) as _e:
    if "watch_sec" not in _SEEN_DEFER:
        _SEEN_DEFER.add("watch_sec")
        silent_exc(_e, where="encoder_sidecar::ppid_watch_sec 回落 30s",
                   level="debug")
    _PPID_WATCH_SEC = 30.0


def _pid_alive(pid: int) -> bool:
    """父进程是否还活着。**判不了就返回 True**（安全侧：绝不误杀）。"""
    if pid <= 0:
        return True
    try:
        import ctypes
        _k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _h = _k.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not _h:
            return False
        try:
            # ★必须再查退出码：Windows 上「已退出但句柄未释放」的僵尸进程
            #   仍能被 OpenProcess 打开 ⇒ 只看句柄会把死进程判成活的。
            _code = ctypes.c_ulong(0)
            if not _k.GetExitCodeProcess(_h, ctypes.byref(_code)):
                return True          # 查不到 ⇒ 当活着（安全侧）
            return _code.value == 259  # STILL_ACTIVE
        finally:
            _k.CloseHandle(_h)
    except (AttributeError, OSError, ValueError) as _e:
        # 判不了就当活着（安全侧：绝不误杀）。同类只记一次，否则跨平台
        # 场景（无 windll）会每次探活刷一条。
        if "pid_alive" not in _SEEN_DEFER:
            _SEEN_DEFER.add("pid_alive")
            silent_exc(_e, where="encoder_sidecar::_pid_alive 判不了 ⇒ 视为存活",
                       level="debug")
        return True


def _watch_parent() -> None:
    """★479：父进程退出则自杀 —— 防止每次重启留下一个 200~400MB 的孤儿。

    实测：三个残留 sidecar 合计 699MB（父进程分别是已死/已死/测试进程）。
    只在 `PULSE_SIDECAR_PPID` 被显式设置时启用；手工直接跑不启用（行为不变）。
    """
    try:
        _ppid = int(os.environ.get("PULSE_SIDECAR_PPID") or 0)
    except (TypeError, ValueError) as _e:
        if "ppid_parse" not in _SEEN_DEFER:
            _SEEN_DEFER.add("ppid_parse")
            silent_exc(_e, where="encoder_sidecar::PULSE_SIDECAR_PPID 非法 ⇒ 不启用看护",
                       level="debug")
        _ppid = 0
    if _ppid <= 0:
        return
    import threading

    def _loop() -> None:
        while True:
            time.sleep(_PPID_WATCH_SEC)
            if not _pid_alive(_ppid):
                sys.stderr.write(
                    f"[encoder_sidecar] 父进程 {_ppid} 已退出 ⇒ 自行结束\n")
                sys.stderr.flush()
                os._exit(0)

    threading.Thread(target=_loop, name="ppid-watch", daemon=True).start()


def _serve_stdio() -> int:
    """旧的管道模式（仅调试用；生产已改 HTTP）。"""
    try:
        _model, _cost = _load_model()
    except Exception as _e:
        silent_exc(_e, where="encoder_sidecar::load(stdio)", level="error")
        return 1
    sys.stdout.write(json.dumps({"ok": True, "ready": True, "dim": EXPECT_DIM,
                                 "model": DEFAULT_MODEL, "load_cost": _cost,
                                 "mode": "stdio"}, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    for _line in sys.stdin:
        _line = _line.strip()
        if not _line:
            continue
        try:
            _req = json.loads(_line)
            _resp = _encode(_model, [str(t) for t in (_req.get("texts") or [])],
                            int(_req.get("batch_size") or 32))
        except Exception as _e:
            silent_exc(_e, where="encoder_sidecar::stdio handle", level="warning")
            _resp = {"ok": False, "dim": EXPECT_DIM, "vectors": [],
                     "error": f"{type(_e).__name__}: {str(_e)[:200]}"}
        sys.stdout.write(json.dumps(_resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def _selftest() -> int:
    try:
        _model, _cost = _load_model()
        _r = _encode(_model, ["曈曈的语义内核如何工作", "hello world"])
    except Exception as _e:
        silent_exc(_e, where="encoder_sidecar::selftest", level="error")
        print(json.dumps({"ok": False, "error": f"{type(_e).__name__}: {str(_e)[:200]}"},
                         ensure_ascii=False))
        return 1
    print(json.dumps({"ok": _r["ok"], "dim": _r["dim"], "count": _r["count"],
                      "load_cost": _cost, "encode_cost": _r["elapsed"]},
                     ensure_ascii=False))
    return 0 if _r["ok"] else 1


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return _selftest()
    if "--stdio" in argv:
        return _serve_stdio()
    return _serve_http()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
