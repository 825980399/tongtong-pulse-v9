"""★479：语义桥客户端（3.14t 侧）—— 把 3.12 sidecar 伪装成 fastembed 的 `TextEmbedding`。

## 用法（在 VectorEncoder 里就一行）

::

    from nucleus.semantic.bridge_encoder import get_bridge
    _b = get_bridge(cache_dir="data/models")
    if _b is not None and _b.available:
        self._model = _b        # 鸭子类型：.embed(texts, batch_size=N)
        self._state = "ready"

`BridgeEncoder.embed()` 与 fastembed 的签名/返回**同构**（`list[list[float]]`），
所以 `VectorEncoder.encode()` 连一行都不用改 —— 维度校验、L2 归一化、
批量大小全部照旧生效。这是本模块刻意追求的最小侵入。

## 为什么走子进程 + HTTP（★两次踩坑后的最终形态）

3.14t 是自由线程构建，`fastembed`/`onnxruntime` 没有 cp314t wheel；
而 3.12 里模型与 ONNX 齐备，实测编码 0.004s、维度 512、
与存量向量库（14899×512）最高相似 0.6525 ⇒ 同一语义空间，无需重建。

**第一版用长驻 stdin/stdout 管道，在 3.14t 下不可靠**：
  · 主进程退出时 `Popen.__del__` 报 `OSError: [WinError 6] 句柄无效`；
  · 生产里 sidecar 崩后再起不来，`VectorEncoder` 持续拿到空向量
    ⇒ 刷 `维度异常: 期望 (200,512)，实际 (0,)`（实测 45 条）。
  · 而且 `readline()` 是阻塞的，sidecar 卡住会把调用线程一起挂死。

现在：**管道只用于一次性握手（读端口号）**，之后全走
`http://127.0.0.1:<port>/embed`。HTTP 天然支持超时、不受句柄失效影响、
进程死了立刻 `URLError` 可重启。

## 可靠性（每条都对应一次真实故障）

  · 懒启动：首次 `embed()` 才 spawn，失败不拖慢框架启动；
  · **超时**：`urlopen(timeout=30)`，绝不无限阻塞；
  · **stderr 落文件**（`logs/encoder_sidecar.err.log`）：不 PIPE，避免缓冲区
    撑满导致 sidecar 阻塞；失败时把末几行贴进日志，不再是诊断盲区；
  · **显式禁用代理**：localhost 绝不能走系统代理（fastembed 就是被代理 502 拖死的）；
  · 保活：进程死了下次调用自动重启（`_MAX_RESTART=5`）；
  · 连续失败 ≥3 次 ⇒ `available=False`，本次运行内放弃桥，回落关键词通道；
  · 所有异常吞掉并 `silent_exc`，**绝不向上抛**（语义降级不该影响主链路）。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any

from nucleus._silent_except import silent_exc

_DEFAULT_SCRIPT = os.path.join("tools", "semantic", "encoder_sidecar.py")
_DEFAULT_ERR_LOG = os.path.join("logs", "encoder_sidecar.err.log")
_EXPECT_DIM = 512
_MAX_RESTART = 5
_FAIL_LIMIT = 3

_lock = threading.RLock()
# 用字符串注解：类还没定义，避免任何运行时求值风险
_singleton: BridgeEncoder | None = None

# localhost 必须绕过系统代理（否则可能被代理拦成 502）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _c
        return getattr(_c, name, default)
    except (ImportError, AttributeError) as _e:
        silent_exc(_e, where=f"bridge_encoder::_cfg({name})", level="debug")
        return default


class BridgeEncoder:
    """把 3.12 编码 sidecar 包成 fastembed 风格的对象（HTTP 传输）。"""

    def __init__(self, python_exe: str | None = None, script: str | None = None,
                 timeout: float = 30.0, err_log: str | None = None):
        self._py = str(python_exe or _cfg(
            "SEMANTIC_BRIDGE_PYTHON", r"D:\Program Files\Python312\python.exe"))
        self._script = str(script or _cfg("SEMANTIC_BRIDGE_SCRIPT", _DEFAULT_SCRIPT))
        self._timeout = float(timeout)
        self._err_log = str(err_log or _cfg("SEMANTIC_BRIDGE_ERR_LOG", _DEFAULT_ERR_LOG))
        self._proc: subprocess.Popen | None = None
        self._err_fh = None
        self._base = ""
        self._port = 0
        self._ready = False
        self._dim = _EXPECT_DIM
        self._restarts = 0
        self._fails = 0
        self._last_error = ""
        self._state_reason = "未启动"

    # ---------------- 生命周期 ----------------
    def _spawn(self) -> bool:
        """启动 sidecar，读一行握手拿端口。**调用方必须已持锁**。"""
        if not os.path.isfile(self._script):
            self._last_error = f"sidecar 脚本不存在: {self._script}"
            self._state_reason = self._last_error
            return False
        if not os.path.isfile(self._py):
            self._last_error = f"桥解释器不存在: {self._py}"
            self._state_reason = self._last_error
            return False

        # ★stderr 落文件，不 PIPE：①不会撑满缓冲区阻塞 sidecar ②失败时可回读诊断
        try:
            _d = os.path.dirname(self._err_log)
            if _d:
                os.makedirs(_d, exist_ok=True)
            # 长持句柄（要交给 Popen 当 stderr，不能用 with 立即关）⇒ 故意不加 with
            self._err_fh = open(  # noqa: SIM115
                self._err_log, "a", encoding="utf-8", errors="replace")
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::spawn errlog", level="debug")
            self._err_fh = None

        try:
            # ★479：把本进程 PID 传给 sidecar，让它在我们退出后自杀
            #   （否则每次重启留一个 200~400MB 的孤儿，实测累积 699MB）。
            _env = dict(os.environ)
            _env["PULSE_SIDECAR_PPID"] = str(os.getpid())
            self._proc = subprocess.Popen(
                [self._py, self._script],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=(self._err_fh or subprocess.DEVNULL),
                env=_env,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::spawn", level="warning")
            self._last_error = f"{type(_e).__name__}: {str(_e)[:150]}"
            self._state_reason = self._last_error
            return False

        # 握手只有一行（读端口），之后不再用管道
        try:
            _line = self._proc.stdout.readline() if self._proc.stdout else ""
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::handshake", level="warning")
            _line = ""
        if not _line:
            self._last_error = f"sidecar 未输出握手（进程可能立即退出）{self._err_tail()}"
            self._state_reason = self._last_error
            self._kill()
            return False
        try:
            _h = json.loads(_line)
        except (json.JSONDecodeError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::handshake parse", level="warning")
            _h = {}
        if not (_h.get("ok") and _h.get("ready")):
            self._last_error = str(_h.get("error") or "握手失败")
            self._state_reason = self._last_error
            self._kill()
            return False
        self._port = int(_h.get("port") or 0)
        if not self._port:
            self._last_error = f"握手缺 port（mode={_h.get('mode')}）"
            self._state_reason = self._last_error
            self._kill()
            return False
        self._base = f"http://127.0.0.1:{self._port}"
        self._dim = int(_h.get("dim") or _EXPECT_DIM)
        self._ready = True
        self._state_reason = (f"就绪（model={_h.get('model')}, dim={self._dim}, "
                             f"port={self._port}, load={_h.get('load_cost')}s）")
        return True

    def _err_tail(self, lines: int = 3) -> str:
        """回读 sidecar 的 stderr 末几行（诊断用，失败也不抛）。"""
        try:
            if not os.path.isfile(self._err_log):
                return ""
            with open(self._err_log, encoding="utf-8", errors="replace") as _f:
                _tail = _f.readlines()[-lines:]
            return " | stderr: " + "".join(_tail).strip()[:300] if _tail else ""
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::_err_tail", level="debug")
            return ""

    def _kill(self) -> None:
        """关掉子进程与句柄（幂等）。"""
        _p, self._proc = self._proc, None
        self._ready = False
        self._base = ""
        self._port = 0
        if _p is not None:
            try:
                _p.terminate()
                _p.wait(timeout=3)
            except (OSError, ValueError, subprocess.TimeoutExpired) as _e:
                silent_exc(_e, where="bridge_encoder::kill term", level="debug")
                try:
                    _p.kill()
                except (OSError, ValueError) as _e2:
                    silent_exc(_e2, where="bridge_encoder::kill force", level="debug")
            try:
                if _p.stdout:
                    _p.stdout.close()
            except (OSError, ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::kill stdout", level="debug")
        _fh, self._err_fh = self._err_fh, None
        if _fh is not None:
            try:
                _fh.close()
            except (OSError, ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::kill errlog", level="debug")

    def close(self) -> None:
        with _lock:
            self._kill()
            self._state_reason = "已关闭"

    # ---------------- 状态 ----------------
    @property
    def available(self) -> bool:
        if self._fails >= _FAIL_LIMIT:
            return False
        return self._ready or self._ensure()

    def status(self) -> dict:
        return {
            "ready": self._ready, "dim": self._dim, "port": self._port,
            "restarts": self._restarts, "fails": self._fails,
            "reason": self._state_reason, "last_error": self._last_error,
            "python": self._py, "script": self._script, "err_log": self._err_log,
        }

    def _ensure(self) -> bool:
        """确保 sidecar 活着（必要时重启，有次数上限）。**自身加锁**。"""
        with _lock:
            if self._ready and self._proc is not None and self._proc.poll() is None:
                return True
            if self._proc is not None:
                # 进程已死或状态不明：清掉再重启
                self._kill()
            if self._restarts >= _MAX_RESTART:
                self._state_reason = f"重启达上限 {_MAX_RESTART}，本次运行放弃桥"
                return False
            _ok = self._spawn()
            if _ok:
                self._restarts = 0
            else:
                self._restarts += 1
            return _ok

    # ---------------- 编码（fastembed 同构接口） ----------------
    def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """与 `fastembed.TextEmbedding.embed` 同构：返回 `list[list[float]]`。

        失败返回空列表 —— `VectorEncoder.encode()` 会因维度不符判失败并回落关键词，
        不会抛异常。
        """
        _list = [str(t) for t in (texts or [])]
        if not _list:
            return []
        with _lock:
            if not self._ensure():
                return []
            _url = f"{self._base}/embed"
            try:
                _body = json.dumps(
                    {"texts": _list, "batch_size": int(batch_size)},
                    ensure_ascii=False).encode("utf-8")
                _req = urllib.request.Request(
                    _url, data=_body, method="POST",
                    headers={"Content-Type": "application/json; charset=utf-8"})
                with _OPENER.open(_req, timeout=self._timeout) as _r:
                    _raw = _r.read()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                    ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::embed http", level="warning")
                self._note_fail(f"{type(_e).__name__}: {str(_e)[:150]}{self._err_tail()}")
                self._kill()
                return []
            try:
                _res = json.loads(_raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::embed parse", level="warning")
                self._note_fail(f"响应非 JSON（{len(_raw)} 字节）")
                return []
            if not _res.get("ok"):
                self._note_fail(str(_res.get("error") or "sidecar 返回 ok=false"))
                return []
            _vecs = _res.get("vectors") or []
            _dim = int(_res.get("dim") or self._dim)
            if len(_vecs) != len(_list) or (len(_vecs) and len(_vecs[0]) != _dim):
                self._note_fail(
                    f"维度/条数不符: 期望 {len(_list)}x{_dim}，"
                    f"实际 {len(_vecs)}x{len(_vecs[0]) if _vecs else 0}")
                return []
            self._fails = 0
            return _vecs

    def _note_fail(self, why: str) -> None:
        self._fails += 1
        self._last_error = why
        self._state_reason = f"失败 {self._fails}/{_FAIL_LIMIT}: {why}"
        if self._fails >= _FAIL_LIMIT:
            # 达到上限后本次运行放弃桥，避免每批都去 spawn 拖慢主链路
            self._kill()
            try:
                from nucleus.logger import get_logger
                get_logger("semantic.bridge").warning(
                    "[语义桥] 连续失败 %d 次，本次运行放弃桥接（回落关键词通道）: %s",
                    self._fails, why)
            except (ImportError, AttributeError, RuntimeError) as _e:
                silent_exc(_e, where="bridge_encoder::note_fail log", level="debug")


def get_bridge(cache_dir: str = "") -> BridgeEncoder | None:
    """取全局桥单例；开关关闭或依赖缺失返回 None。"""
    _ = cache_dir  # 缓存路径由 sidecar 内部 DEFAULT_CACHE 决定
    if not bool(_cfg("ENABLE_M479_SEMANTIC_BRIDGE", False)):
        return None
    global _singleton
    with _lock:
        if _singleton is None:
            try:
                _singleton = BridgeEncoder()
            except (OSError, ValueError, RuntimeError) as _e:
                silent_exc(_e, where="bridge_encoder::get_bridge", level="warning")
                return None
        return _singleton


def reset_bridge_for_test() -> None:
    """单测用：清掉单例（不要在业务代码里调）。"""
    global _singleton
    with _lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None
