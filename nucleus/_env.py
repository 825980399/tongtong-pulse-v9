# -*- coding: utf-8 -*-
"""398 刀2：环境判定与写盘守卫的**唯一收口**。

## 为什么要有这个模块

`_is_test_env` 在本仓被复制了 **7 份**（含 1 份叫 `_in_test_env`）、`_writable` **3 份**。
同一个事实用 7 份代码表达，改一处漏六处是必然的 —— 本项目已经栽过：
`DailyScheduler` 那份**没加** try/except，其余 6 份有；判断顺序也不一致
（4 份先判 `sys.modules`，2 份先判环境变量）。语义相同就该只有一份实现。

## 收口边界（★不是所有同名都该合并）

===========================  ======  ==========================================
组                           实测     结论
===========================  ======  ==========================================
`_is_test_env`/`_in_test_env`  7      ✅ 收口 —— 语义完全相同
`_writable`                    3      部分收口 —— 2 处模块级同义并入；
                                      `model_self_updater._writable()` 是无参
                                      方法（判 `self.base_dir()`），保留
`_atomic*`                 0 个叫    ❌ **不收口**（见下）
                           `_atomic`，
                           实为 5 种
`ledger_path`                   5      ❌ **不收口**（见下）
===========================  ======  ==========================================

## 不收口清单（写在这里，免得下一批又当成"重复"来合并）

**`_atomic*` —— 五种不同签名，合并必改行为：**

* ``_atomic_write_json(path, payload) -> bool``（``audit_problem_pool.py:942``）
  带 346 隔离域过滤 + ``guard_write`` 守卫 + ``sort_keys=True``；
* ``_m335_atomic_write_json(path, obj) -> None``（``patch_execution_dispatch.py:122``）
  **无守卫、无 sort_keys、返回 None**、tmp 后缀 ``.tmp335``
  ⇒ 与上一条合并会直接改掉落盘行为（守卫从有到无）；
* ``_atomic_save(data, path=None) -> str``（``task_ledger.py:144``）返回写到的路径；
* ``_atomic_write(path, doc) -> bool``（``verifiable_recompute.py:122``、
  ``ContextSnapshot.py:991``、``PulseInstinctSnapshot.py:162``）各自处理自家文档形态；
* ``_atomic_write_with_rotation(...)``（``PulseSnapshot.py:1725``）带轮转。

**`ledger_path` —— 五处 = 五个独立账本：**

审计问题池 / DL3 决策 / 任务态 / 成熟度 / 期望，各自有独立的路径策略与文件名。
把它们合并会把 5 个独立账本的路径策略耦合成一个 —— 与铁律 271
「分子分母必须同源」同一条道理的反面：**不同源的账本不能共用路径函数**。
"""
from __future__ import annotations

import os
import sys

from nucleus._silent_except import silent_exc

__all__ = ["is_test_env", "is_production_data_path", "should_refuse_write"]

#: 项目根（`nucleus/_env.py` 上两层）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_test_env() -> bool:
    """是否处于 pytest 环境。

    判据（两个都要看，只判一个会漏）：
      * ``"pytest" in sys.modules`` —— 覆盖"pytest 已导入但当前没在跑用例"；
      * ``PYTEST_CURRENT_TEST`` 环境变量 —— 覆盖 xdist 子进程等场景。
    """
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus._env::is_test_env")
        return False


def is_production_data_path(path: str) -> bool:
    """``path`` 是否落在本仓生产 ``data/`` 下。

    ★异常时返回 **True**（保守方向）：判不了就当作生产区，宁可拒写也不污染。
    这与原先 ``maturity_ledger._is_production_data_path`` /
    ``model_self_updater._is_production_path`` 的异常分支一致，不是新口径。
    """
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _r = _PROJECT_ROOT.replace("\\", "/").lower()
        return _p.startswith(_r + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus._env::is_production_data_path")
        return True


def should_refuse_write(path: str, *, tag: str = "") -> bool:
    """测试环境 + 生产 ``data/`` ⇒ ``True``（调用方应拒写）。

    ``tag`` 只用于 stderr 前缀，保持各调用方原有的可辨识度
    （``[task_ledger]`` / ``[maturity_ledger]`` …），不改变任何判定。
    """
    if is_test_env() and is_production_data_path(path):
        sys.stderr.write(f"[{tag or '_env'}] 测试环境跳过生产写入: {path}\n")
        return True
    return False
