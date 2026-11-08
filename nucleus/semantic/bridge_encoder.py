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

## 为什么走子进程

3.14t 是自由线程构建，`fastembed`/`onnxruntime` 没有 cp314t wheel；
而 3.12 里模型与 ONNX 齐备，实测编码 0.004s、维度 512、
与存量向量库（14899×512）最高相似 0.6525 ⇒ 同一语义空间，无需重建。

## 可靠性

  · 懒启动：首次 `embed()` 才 spawn，失败不拖慢启动；
  · 保活：进程死了下次调用自动重启（有 `_max_restart` 上限防雪崩）；
  · 连续失败 ≥3 次 ⇒ `available=False`（调用方自然回落关键词通道）；
  · 所有异常吞掉并 `silent_exc` 留痕，**绝不向上抛**（语义降级不该影响主链路）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any

from nucleus._silent_except import silent_exc

_DEFAULT_SCRIPT = os.path.join("tools", "semantic", "encoder_sidecar.py")
_EXPECT_DIM = 512
_MAX_RESTART = 3
_FAIL_LIMIT = 3

_lock = threading.RLock()
_singleton: BridgeEncoder | None = None


def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _c
        return getattr(_c, name, default)
    except (ImportError, AttributeError) as _e:
        silent_exc(_e, where=f"bridge_encoder::_cfg({name})", level="debug")
        return default


class BridgeEncoder:
    """把 3.12 编码 sidecar 包成 fastembed 风格的对象。"""

    def __init__(self, python_exe: str | None = None, script: str | None = None,
                 timeout: float = 30.0):
        self._py = str(python_exe or _cfg(
            "SEMANTIC_BRIDGE_PYTHON", r"D:\Program Files\Python312\python.exe"))
        self._script = str(script or _cfg("SEMANTIC_BRIDGE_SCRIPT", _DEFAULT_SCRIPT))
        self._timeout = float(timeout)
        self._proc: subprocess.Popen | None = None
        self._ready = False
        self._dim = _EXPECT_DIM
        self._restarts = 0
        self._fails = 0
        self._last_error = ""
        self._state_reason = "未启动"

    # ---------------- 生命周期 ----------------
    def _spawn(self) -> bool:
        """启动 sidecar 并完成握手。**调用方必须已持锁**。"""
        if not os.path.isfile(self._script):
            self._last_error = f"sidecar 脚本不存在: {self._script}"
            self._state_reason = self._last_error
            return False
        if not os.path.isfile(self._py):
            self._last_error = f"桥解释器不存在: {self._py}"
            self._state_reason = self._last_error
            return False
        try:
            self._proc = subprocess.Popen(
                [self._py, self._script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::spawn", level="warning")
            self._last_error = f"{type(_e).__name__}: {str(_e)[:150]}"
            self._state_reason = self._last_error
            return False

        # 首行握手：成功是 {"ok":true,"ready":true,...}，加载失败是 {"ok":false,...} 后退出
        try:
            _line = self._proc.stdout.readline() if self._proc.stdout else ""
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::handshake", level="warning")
            _line = ""
        if not _line:
            self._last_error = "sidecar 未输出握手（进程可能立即退出）"
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
        self._dim = int(_h.get("dim") or _EXPECT_DIM)
        self._ready = True
        self._state_reason = (f"就绪（model={_h.get('model')}, dim={self._dim}, "
                             f"load={_h.get('load_cost')}s）")
        return True

    def _kill(self) -> None:
        """关掉子进程（调用方持有锁或确认独占）。"""
        _p, self._proc = self._proc, None
        self._ready = False
        if _p is None:
            return
        try:
            if _p.stdin:
                _p.stdin.close()
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="bridge_encoder::kill stdin", level="debug")
        try:
            _p.terminate()
            _p.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired) as _e:
            silent_exc(_e, where="bridge_encoder::kill term", level="debug")
            try:
                _p.kill()
            except (OSError, ValueError) as _e2:
                silent_exc(_e2, where="bridge_encoder::kill force", level="debug")

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
            "ready": self._ready, "dim": self._dim,
            "restarts": self._restarts, "fails": self._fails,
            "reason": self._state_reason, "last_error": self._last_error,
            "python": self._py, "script": self._script,
        }

    def _ensure(self) -> bool:
        """确保 sidecar 活着（必要时重启，有次数上限）。**自身加锁**。"""
        with _lock:
            if self._ready and self._proc is not None and self._proc.poll() is None:
                return True
            if self._proc is not None and self._proc.poll() is not None:
                # 进程已死：清掉再重启
                self._ready = False
                self._proc = None
            if self._restarts >= _MAX_RESTART and not self._ready:
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
            _p = self._proc
            if _p is None or _p.stdin is None or _p.stdout is None:
                return []
            try:
                _p.stdin.write(json.dumps(
                    {"texts": _list, "batch_size": int(batch_size)},
                    ensure_ascii=False) + "\n")
                _p.stdin.flush()
                _line = _p.stdout.readline()
            except (OSError, ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::embed io", level="warning")
                self._note_fail(f"{type(_e).__name__}: {str(_e)[:150]}")
                self._kill()
                return []
            if not _line:
                self._note_fail("sidecar 无响应（EOF）")
                self._kill()
                return []
            try:
                _r = json.loads(_line)
            except (json.JSONDecodeError, ValueError) as _e:
                silent_exc(_e, where="bridge_encoder::embed parse", level="warning")
                self._note_fail(f"响应非 JSON: {str(_line)[:80]}")
                return []
            if not _r.get("ok"):
                self._note_fail(str(_r.get("error") or "sidecar 返回 ok=false"))
                return []
            _vecs = _r.get("vectors") or []
            _dim = int(_r.get("dim") or self._dim)
            if len(_vecs) != len(_list) or (len(_vecs) and len(_vecs[0]) != _dim):
                self._note_fail(f"维度/条数不符: 期望 {len(_list)}x{_dim}")
                return []
            self._fails = 0
            return _vecs

    def _note_fail(self, why: str) -> None:
        self._fails += 1
        self._last_error = why
        self._state_reason = f"失败 {self._fails}/{_FAIL_LIMIT}: {why}"
        if self._fails >= _FAIL_LIMIT:
            # 达到上限后永久放弃（本次进程生命周期内），避免每批都去 spawn 拖慢主链路
            self._kill()
            try:
                from nucleus.logger import get_logger
                get_logger("semantic.bridge").warning(
                    "[语义桥] 连续失败 %d 次，本次运行放弃桥接（回落关键词通道）: %s",
                    self._fails, why)
            except (ImportError, AttributeError, RuntimeError) as _e:
                silent_exc(_e, where="bridge_encoder::note_fail log", level="debug")


def get_bridge(cache_dir: str = "") -> BridgeEncoder | None:
    """取全局桥单例；开关关闭或依赖缺失返回 None。

    `cache_dir` 仅为调用方便（真实缓存路径由 sidecar 内部的 DEFAULT_CACHE 决定）。
    """
    _ = cache_dir  # 保留参数以备将来按 cache 分实例
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


_ = sys  # 保留：某些环境下需要 sys 做路径推断
