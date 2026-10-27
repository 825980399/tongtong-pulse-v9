# -*- coding: utf-8 -*-
"""★375批刀3：K2 试点接线 —— `submit_cpu_bound` 从「有键无色」变成真调用。

现状（本刀前）：`parallel_scheduler.submit_cpu_bound` 有定义、有委托实现，但**生产 0 调用**
（任务书写作 `submit_cpu_intensive`，实际入口名是 `submit_cpu_bound` —— 前提勘误）。
结果：Hybrid 进程池每拍都在跑（main.py 启动即 `_ensure_pools()`，活体实测 4 进程），
但统一门面从未被任何人走过，K2 口径在活体上一次真实读数都没有。

本刀只接**已经在跨进程跑的纯 CPU 任务**（`verify_patch_effect_in_process` /
`run_ab_test_in_process`），失败一律回落原自持池 ⇒ 功能与降级语义不变。

★不新增池：路由目标是既有的 Hybrid 池（`main.py` 启动时主动初始化），
本文件用 AST 数 `ProcessPoolExecutor` 实例化处数钉住这一点。
"""
import ast
import os
import pickle
import re
import threading

import pytest

from nucleus.reasoning.ReasoningWorkerPool import ReasoningWorkerPool as C
from nucleus.reasoning.ReasoningWorkerPool import m375_cpu_route_stats

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RWP = os.path.join(ROOT, "nucleus", "reasoning", "ReasoningWorkerPool.py")

# 本刀接线时的池实例化处数基线（AST 数 Call，注释/文档串不算）
POOL_BASELINE = 4


def _bare():
    """白盒裸实例：只要锁与计数位，不建池（避免用例启动 4 个进程）。"""
    _p = object.__new__(C)
    _p._lock = threading.Lock()
    _p._m375_route = {"routed": 0, "fallback": 0, "last": ""}
    return _p


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class _FakeOk:
    def __init__(self):
        self.seen = None

    def submit_cpu_bound(self, func, *args, **kwargs):
        self.seen = (func, args, kwargs)
        return "FUTURE"


class _FakeBoom:
    def submit_cpu_bound(self, *a, **k):
        raise RuntimeError("池已关闭")


@pytest.fixture()
def fake_sched(monkeypatch):
    """替换 `nucleus.parallel_scheduler.get_parallel_scheduler`（函数内 import，patch 模块属性即可）。"""
    import nucleus.parallel_scheduler as _ps

    def _install(obj):
        monkeypatch.setattr(_ps, "get_parallel_scheduler", lambda: obj)
        return obj

    return _install


# ------------------------------- ① 白名单与真实调用名对齐


def test_01_whitelist_is_immutable_and_complete():
    assert isinstance(C._M375_CPU_ROUTE_NAMES, frozenset), "白名单必须是不可变集合（类属性可被误改）"
    assert C._M375_CPU_ROUTE_NAMES == frozenset({
        "verify_patch_effect_in_process", "run_ab_test_in_process"})


def test_02_whitelist_names_really_submitted_by_callers():
    """白名单里的名字必须在真实调用侧出现过 —— 写错一个字母就等于永不接管。

    这是本刀最容易出的静默故障：路由「装好了」但 func_name 对不上，
    永远走不到 ⇒ 看起来接线了，实际一次都没跑。
    """
    _src = _read("nucleus/evolution/ParamPatchManager.py")
    for _name in sorted(C._M375_CPU_ROUTE_NAMES):
        # 真实调用形态：pool.submit("<name>", ...)（中间可能换行 + 缩进）
        _pat = re.compile(r'submit\(\s*"' + re.escape(_name) + r'"')
        assert _pat.search(_src), "调用侧没有 submit(\"<name>\") 形态：" + _name


# ------------------------------- ② 不接管：必须原路返回 None


def test_03_non_whitelist_not_taken_over(fake_sched):
    _fk = fake_sched(_FakeOk())
    _p = _bare()
    assert _p._m375_cpu_route("FrequencyCodec.encode_batch", ({},), {}) is None
    assert _fk.seen is None, "非白名单任务被误转发"
    assert _p._m375_route == {"routed": 0, "fallback": 0, "last": ""}


# ------------------------------- ③ 接管成功：转发正确 + 计数


def test_04_whitelist_routed_with_args_forwarded(fake_sched):
    _fk = fake_sched(_FakeOk())
    _p = _bare()
    _r = _p._m375_cpu_route("verify_patch_effect_in_process", ({"id": 1}, "log.txt"), {})
    assert _r == "FUTURE"
    _func, _args, _kw = _fk.seen
    assert _func is C._execute_reasoning_task, "必须转发子进程入口本身，不是包一层闭包"
    assert _args == ("verify_patch_effect_in_process", {"id": 1}, "log.txt")
    assert _kw.get("task_name") == "rw:verify_patch_effect_in_process"
    assert _p._m375_route["routed"] == 1
    assert _p._m375_route["last"] == "verify_patch_effect_in_process"


# ------------------------------- ④ 失败必须回落，不许把异常抛给调用方


def test_05_route_failure_falls_back_silently(fake_sched):
    fake_sched(_FakeBoom())
    _p = _bare()
    assert _p._m375_cpu_route("run_ab_test_in_process", ({"test_id": "t"},), {}) is None
    assert _p._m375_route["fallback"] == 1
    assert _p._m375_route["routed"] == 0
    assert _p._m375_route["last"] == "fallback:RuntimeError"


def test_06_route_never_raises_on_import_error(monkeypatch):
    """连 parallel_scheduler 都 import 不了时也不许抛（活体路径不能被观测件带崩）。"""
    import builtins

    _orig = builtins.__import__

    def _boom(name, *a, **k):
        if name == "nucleus.parallel_scheduler":
            raise ImportError("模拟缺失")
        return _orig(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    _p = _bare()
    assert _p._m375_cpu_route("run_ab_test_in_process", ({},), {}) is None
    assert _p._m375_route["fallback"] == 1


# ------------------------------- ⑤ 路由前提：子进程入口可 pickle


def test_07_execute_task_is_picklable():
    """进程池路由的硬前提：转发对象必须能被 pickle 到子进程。"""
    _b = pickle.dumps(C._execute_reasoning_task)
    assert pickle.loads(_b) is C._execute_reasoning_task


# ------------------------------- ⑥ 接线位置：必须在原路径之前


def test_08_route_called_before_self_pool_path():
    _t = _read("nucleus/reasoning/ReasoningWorkerPool.py")
    _i = _t.find("def submit(self, func_name")
    assert _i >= 0
    _j = _t.find("def _m375_cpu_route", _i)
    _body = _t[_i:_j if _j > 0 else len(_t)]
    _a = _body.find("self._m375_cpu_route(func_name, args, kwargs)")
    _b = _body.find("for _attempt in range(2)")
    assert _a >= 0, "submit 里没有接线点"
    assert _b > _a, "接线点必须在自持池提交之前（否则永远走不到）"
    assert "if _m375_fut is not None:" in _body, "缺接管判定"


def test_09_submit_cpu_bound_now_has_production_call():
    """「有键无色」已解决：本文件出现真调用形态 `.submit_cpu_bound(`（且不自建同名定义）。"""
    _t = _read("nucleus/reasoning/ReasoningWorkerPool.py")
    assert "def submit_cpu_bound(" not in _t, "本文件不该定义 submit_cpu_bound（门面在 parallel_scheduler）"
    assert _t.count(".submit_cpu_bound(") >= 1, "仍只有定义没有调用"
    assert "get_parallel_scheduler().submit_cpu_bound(" in _t


# ------------------------------- ⑦ 不新增池


def test_10_no_new_process_pool():
    _tree = ast.parse(_read("nucleus/reasoning/ReasoningWorkerPool.py"))
    _n = 0
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Call):
            _fn = _node.func
            _name = getattr(_fn, "id", None) or getattr(_fn, "attr", None)
            if _name == "ProcessPoolExecutor":
                _n += 1
    assert _n == POOL_BASELINE, f"ProcessPoolExecutor 实例化 {_n} != 基线 {POOL_BASELINE}（本刀不建池）"


# ------------------------------- ⑧ 观测出口三态


def test_11_stats_absent_when_singleton_missing():
    _s = m375_cpu_route_stats()
    assert _s["state"] in ("absent", "present")   # 同进程内可能已被别处建过
    assert isinstance(_s["names"], list) and len(_s["names"]) == 2
    if _s["state"] == "absent":
        assert _s["routed"] == 0 and _s["fallback"] == 0
