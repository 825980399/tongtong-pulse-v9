# -*- coding: utf-8 -*-
"""★356刀11（洞鉴 DJ4-03）：`pending_patches.json` **唯一写口**。

洞鉴实测生产态有 **3 个写者 / 11 个写点**：
  · `PatchManager`（9 个写点，全部汇入 `_save_patch_list`）
  · `PatchAutoApprover.prune_pending`（裸 `open(...,'w')` 直写，非原子）
  · `patch_execution_dispatch`（TTL 回写，tmp+replace）
三者**没有共享锁**，读-改-写窗口可交错：

    A 读 [1,2] → B 读 [1,2] → A 写 [1,2,3] → B 写 [1,2,4]   ⇒ 条目 3 **无声丢失**

本模块把三条路收敛成一个入口 `write_pending()`：
  ① 进程内 `RLock` 串行（同进程多线程是真实形态：主线程+巡逻+审批+夜间 TTL）；
  ② 临时文件 + `os.replace` 原子替换（不留半截 JSON）；
  ③ **写后读回校验条数**（写进去的和读回来的一致才算成功）。

★**能力边界**：锁是**进程内**的。另一个 Python 进程直接写同一文件不在本锁范围
（那类写入应由 `nucleus/data/write_guard` 拦：普通脚本默认被拒）。跨进程文件锁
需要 msvcrt/fcntl 平台分叉，本批不做——生产只有曈曈一个进程写 pending。
"""
import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PendingWrite")

#: ★356刀11：唯一写口锁（RLock —— `write_pending` 可能被同线程嵌套调用）
PENDING_WRITE_LOCK = threading.RLock()

#: 相对路径（与 PatchManager / dispatch 各自拼的路径同源）
PENDING_REL = os.path.join("data", "patches", "pending_patches.json")

#: 判据：写后读回的条数必须与写入一致
_READBACK_RETRY = 1

#: ★403刀4：os.replace 重试（Windows 上多进程同时写同一文件常撞 WinError 5/32）
_REPLACE_RETRIES = 3
_REPLACE_RETRY_GAP_S = 0.1


def _replace_with_retry(tmp_path: str, target_path: str,
                        retries: int = _REPLACE_RETRIES,
                        gap_s: float = _REPLACE_RETRY_GAP_S) -> None:
    """原子替换带重试：Windows 读者持句柄时 `os.replace` 会抛 WinError 5/32。

    重试用完后仍失败 ⇒ 抛最后一次异常（由调用方决定是报错还是保留 tmp 供取证）。
    """
    _last = None
    for _i in range(max(1, int(retries))):
        try:
            os.replace(tmp_path, target_path)
            return
        except OSError as _e:
            _last = _e
            if _i < max(1, int(retries)) - 1:
                time.sleep(gap_s)
    if _last is not None:
        raise _last
    raise OSError("replace failed")


def pending_path(root: str | None = None) -> str:
    """pending 文件路径（`root` 仅供隔离注入；不传就是生产路径）。"""
    if root:
        return os.path.join(root, PENDING_REL)
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        PENDING_REL)


def _rows_of(payload: Any) -> int | None:
    """取 payload 的条目数：list 取 len；dict 取 patches/pending 列表。"""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for _k in ("patches", "pending", "items"):
            _v = payload.get(_k)
            if isinstance(_v, list):
                return len(_v)
        return None
    return None


def _rows_on_disk(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as _f:
            return _rows_of(json.load(_f))
    except (OSError, ValueError) as _e:
        silent_exc(_e, "pending_write._rows_on_disk 读回", level="warning")
        return None


def write_pending(payload: Any, *, path: str | None = None,
                  root: str | None = None, writer: str = "",
                  verify_rows: bool = True) -> dict:
    """把 `payload` 写进 pending_patches.json（**唯一写口**）。

    Args:
        payload: list 或带 `patches`/`pending` 列表的 dict。
        path: 显式目标路径（隔离验证用）；不传按 `root` 或生产路径。
        root: 项目根（隔离注入）。
        writer: 写者名（留痕用，必填才有意义）。
        verify_rows: 是否做写后读回条数校验（默认 True）。

    Returns:
        `{"ok": bool, "rows": int|None, "writer": str, "why": str, "path": str}`
        失败**不抛**（写盘失败不得反过来打断调用方主流程），由调用方按需处理。
    """
    _p = path or pending_path(root)
    _rows = _rows_of(payload)
    _t0 = time.time()
    with PENDING_WRITE_LOCK:
        _tmp = None
        try:
            _dir = os.path.dirname(_p) or "."
            if not os.path.isdir(_dir):
                os.makedirs(_dir, exist_ok=True)
            _tmp = "%s.m356.%d.tmp" % (_p, os.getpid())
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(payload, _f, ensure_ascii=False, indent=1)
                _f.flush()
                os.fsync(_f.fileno())
            _replace_with_retry(_tmp, _p)   # ★403刀4：3 次重试，间隔 100ms
            _tmp = None
        except (OSError, TypeError, ValueError) as _e:
            silent_exc(_e, where="pending_write.write_pending[%s]" % (writer or "?"),
                       level="warning")
            _module_logger.warning(
                "[356单写者] 写失败 writer=%s path=%s why=%s: %s",
                writer or "?", _p, type(_e).__name__, _e)
            if _tmp and os.path.exists(_tmp):
                try:
                    os.unlink(_tmp)
                except OSError as _ue:
                    silent_exc(_ue, "pending_write.write_pending 清理", level="debug")
            return {"ok": False, "rows": _rows, "writer": writer,
                    "why": "%s: %s" % (type(_e).__name__, _e), "path": _p}

        _back = _rows_on_disk(_p) if verify_rows else _rows
        _ok = (not verify_rows) or (_back == _rows)
        if not _ok:
            # ★读回不一致 = 写进去的和落盘的不是一回事（多写者/外部改动）。
            #   不重试覆盖（可能覆盖别人的正确写入），只如实告警并把读数交回调用方。
            _module_logger.warning(
                "[356单写者] 读回不一致 writer=%s 写入=%s 读回=%s path=%s",
                writer or "?", _rows, _back, _p)
        _module_logger.info(
            "[356单写者] writer=%s rows=%s 读回=%s ok=%s 耗时=%.1fms",
            writer or "?", _rows, _back, _ok, (time.time() - _t0) * 1000.0)
    return {"ok": bool(_ok), "rows": _back, "writer": writer,
            "why": "" if _ok else "readback_mismatch", "path": _p}


def update_pending(mutator, *, path: str | None = None, root: str | None = None,
                   writer: str = "") -> dict:
    """★356刀11：**读-改-写全在锁内**的入口（真正的 RMW 原子性）。

    为什么单有 write_pending 不够：它只串行「写」，若调用方**在锁外读**，
    仍然是经典的 lost update：
        A 读[1,2] → B 读[1,2] → A 写[1,2,3] → B 写[1,2,4]  ⇒ 3 丢失
    本函数把「读 → 交给 mutator 改 → 写 → 读回」整段放进同一把 RLock。

    Args:
        mutator: fn(payload) -> payload（原地改或返回新对象都可；返回 None 视为原地改）。
        path/root/writer: 同 write_pending。

    Returns:
        write_pending 的返回体 + before（改前条数）。
    """
    _p = path or pending_path(root)
    with PENDING_WRITE_LOCK:
        _before = None
        _payload = None
        try:
            with open(_p, encoding="utf-8") as _f:
                _payload = json.load(_f)
            _before = _rows_of(_payload)
        except FileNotFoundError as _fnf356:
            # 首写时文件还不存在是**预期**路径，但仍要留痕（铁律176：纯赋值也是静默）
            silent_exc(_fnf356, where="pending_write.update_pending[%s] 首写" % (writer or "?"),
                       level="debug")
            _payload = []
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="pending_write.update_pending[%s] 读" % (writer or "?"),
                       level="warning")
            return {"ok": False, "rows": None, "writer": writer,
                    "why": "read_failed", "path": _p, "before": None}
        _new = mutator(_payload)
        if _new is not None:
            _payload = _new
        _r = write_pending(_payload, path=_p, writer=writer)
        _r["before"] = _before
        return _r


def write_stats() -> dict:
    """写口自检（供测试/诊断）：锁是否被占、路径是什么。"""
    return {
        "schema": "pending_write/356.v1",
        "rel": PENDING_REL.replace(os.sep, "/"),
        "path": pending_path(),
        "locked": PENDING_WRITE_LOCK._is_owned(),   # noqa: SLF001 —— 只读诊断
        "retry": _READBACK_RETRY,
    }
