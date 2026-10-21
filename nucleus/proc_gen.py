# -*- coding: utf-8 -*-
"""★335批刀8（A-11 根治件）：进程代次 —— **全项目唯一取数口**。

背景（334挂账⑤ · 334 五条最硬②）：日志行不带 PID/代次，活体归属无从自证 ——
隔离根跑出来的行、注入日期合成的行、他方工具进程的行，与活体行在日志里长得
一模一样，读方只能靠上下文猜。331刀5 已在 `SafeEvolutionExecutor` 里写了
`_m331_proc_gen`，但它是**模块私有**的：夜间编排 / 自检 / 派单要用它，就得
import 那个重模块（会把 LLM/沙箱子进程一大串依赖拉起来）。

故把实现搬到这里 —— **零依赖、无副作用、不 import 任何业务模块**；原
`_m331_proc_gen` 改为委托本模块，既有调用点一字不改（口径单一，不会漂）。

边界（照抄 331 的语义，**不许"优化"**）：
  * psutil 不可用 ⇒ `create_time=None`、`gen` 后缀记 `unknown` —— **不填 0**：
    0 会被读成"1970 年起的进程"，比"不知道"错得更彻底。
  * 结果进程内缓存：同一进程的 pid/create_time 不会变，不必反复问内核。
  * 本模块**不判断活体/工具**：它只说"我在哪个进程里"。谁是谁，由读日志的人
    按 `proc_gen` 对表（这正是 A-11 要的"可区分"）。
"""
import os
import time

from nucleus._silent_except import silent_exc

_CACHE: dict = {}


def get_proc_gen() -> dict:
    """本进程代次：`{"pid","create_time","create_str","gen"}`（取不到时刻记 unknown）。"""
    if _CACHE:
        return dict(_CACHE)
    _pid = os.getpid()
    _ct = None
    try:
        import psutil
        _ct = float(psutil.Process(_pid).create_time())
    except Exception as _e:
        # ★cw2 铁律150：不可用必须留痕；且**不得**退成 0（见模块 docstring）
        silent_exc(_e, where="nucleus.proc_gen::get_proc_gen")
        _ct = None
    _cs = time.strftime("%Y%m%d-%H%M%S", time.localtime(_ct)) if _ct else ""
    _gen = ("pid%d@%s" % (_pid, _cs)) if _cs else ("pid%d@unknown" % _pid)
    _CACHE.update({"pid": _pid, "create_time": _ct,
                   "create_str": _cs, "gen": _gen})
    return dict(_CACHE)


def proc_gen_str() -> str:
    """日志用单串：`pid<N>@<创建时刻>`（取不到时刻 ⇒ `pid<N>@unknown`）。"""
    return str(get_proc_gen().get("gen") or "unknown")


def reset_proc_gen_cache() -> None:
    """★测试/隔离验证专用：清缓存让下次重新问内核（不改任何业务状态）。"""
    _CACHE.clear()
