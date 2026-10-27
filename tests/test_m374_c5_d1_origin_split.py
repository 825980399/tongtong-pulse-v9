# -*- coding: utf-8 -*-
"""374 刀5 单测：D1 检测器区分「器官间直调」与「基础设施工厂」。

票 `T-370-检测器器官直调误报-1`（P2 · 374 批 C 组）。

【票面】`_check_cross_module_singleton_call` 只要命中 `get_xxx().method()` 就判
「跨模块直调」，不区分`另一个器官`与`基础设施工厂`——实测 33 条命中，来源 100%
是 `nucleus/*`（`get_runtime_tempo` / `get_kal` / `get_experience_pool` …），
`organs/*` 一条没有。器官用这些单例是**正门**用法，不是绕过脉冲总线。

【判据】按 `get_xxx` 的**来源模块**分档：
  · `organs.*`  ⇒ 真·跨器官直调 ⇒ 报
  · 其他已知包  ⇒ 基础设施工厂   ⇒ 不报
  · 解析不出    ⇒ **保守上报**（不赦免未知，否则等于开黑洞）

验六件事：
① 基础设施来源不报；② 器官来源必报（正控，防"一刀切全赦免"）；
③ 相对导入按包解析（器官内部互调不许因"相对路径看不懂"而漏报）；
④ 来源解析不出仍报；⑤ 356 的两条排除不回归（契约三件套 / self 调用）；
⑥ 全树形态断言：凡被报出者，来源必须是器官或未解析（**不用绝对条数当判据**）。
"""
import os

import pytest

import nucleus.self_inspector as SI


def _inst():
    _o = object.__new__(SI.SelfInspector)
    _o._generate_fix_suggestion = lambda *a, **k: "sug"
    return _o


def _organ_tree(monkeypatch, tmp_path, rel, text):
    """造「仓内相对路径」形态的假器官文件：chdir 到根，返回相对路径。

    相对导入的包名只能按**仓库相对路径**解析；绝对路径跨盘时 relpath 会抛
    ValueError。生产里扫描器给的就是相对路径，所以这里复刻那个形态。
    """
    _p = tmp_path / rel
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return rel


def _mkfile(tmp_path, rel, text):
    """造一个真实的源文件（import 解析读的是磁盘上的文件）。"""
    _p = tmp_path / rel
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(text, encoding="utf-8")
    return str(_p)


def _body(call):
    return f"def f(self):\n    {call}\n"


def _run(file_path, call):
    _iss = []
    _inst()._check_cross_module_singleton_call(
        _body(call), file_path, "PulseX", "f", 1, _iss)
    return _iss


# ------------------------------------------------- ① 基础设施工厂不报
def test_01_infra_factory_not_reported(tmp_path):
    _f = _mkfile(tmp_path, "organs/body/PulseX.py",
                 "from nucleus.runtime_tempo import get_runtime_tempo\n"
                 "from nucleus.knowledge_access_layer import get_kal\n")
    assert _run(_f, "get_runtime_tempo().get_background_tempo()") == []
    assert _run(_f, "get_kal().get_node('n1')") == []


def test_02_relative_import_of_infra_not_reported(monkeypatch, tmp_path):
    """`from ...nucleus.x import get_y` 这种相对形态也要认出来源。"""
    _f = _organ_tree(monkeypatch, tmp_path, "organs/brain/PulseY.py",
                 "from ...nucleus.probe_strategy import get_probe_strategy_memory\n")
    assert _run(_f, "get_probe_strategy_memory().compute_strategy()") == []


# ----------------------------------------------------- ② 器官来源必报
def test_03_organ_origin_still_reported(tmp_path):
    """★正控：真正的跨器官直调必须继续报（误报降 ≠ 真风险赦免）。"""
    _f = _mkfile(tmp_path, "organs/body/PulseX.py",
                 "from organs.brain.PulseCortex import get_cortex_organ\n")
    _iss = _run(_f, "get_cortex_organ().think()")
    assert len(_iss) == 1, _iss
    assert _iss[0]["type"] == "cross_module_singleton_call"
    assert "来源=organs.brain.PulseCortex" in _iss[0]["description"]


# --------------------------------------- ③ 相对导入按包解析（防漏报）
def test_04_relative_import_inside_organs_reported(monkeypatch, tmp_path):
    _f = _organ_tree(monkeypatch, tmp_path, "organs/brain/PulseY.py",
                 "from .PulseCortex import get_cortex_organ\n")
    _iss = _run(_f, "get_cortex_organ().think()")
    assert len(_iss) == 1, "相对导入被解析成非器官 ⇒ 真风险漏报"
    assert "来源=organs.brain.PulseCortex" in _iss[0]["description"]


def test_05_relative_import_two_levels_up(monkeypatch, tmp_path):
    """`from ..core import get_x`（organs/brain → organs）⇒ 仍属器官包。"""
    _f = _organ_tree(monkeypatch, tmp_path, "organs/brain/PulseY.py",
                 "from ..core import get_core_organ\n")
    _iss = _run(_f, "get_core_organ().run()")
    assert len(_iss) == 1, _iss
    assert "来源=organs.core" in _iss[0]["description"]


# ------------------------------------------------ ④ 解析不出仍保守上报
def test_06_unresolved_origin_still_reported(tmp_path):
    _iss = _run(str(tmp_path / "no_such_file.py"), "get_mystery().do()")
    assert len(_iss) == 1 and "来源=未解析" in _iss[0]["description"]


def test_07_not_imported_getter_still_reported(tmp_path):
    """文件里没 import 过它（动态取）⇒ 来源未解析 ⇒ 仍报。"""
    _f = _mkfile(tmp_path, "organs/body/PulseX.py", "import os\n")
    _iss = _run(_f, "get_dynamic_thing().do()")
    assert len(_iss) == 1 and "来源=未解析" in _iss[0]["description"]


def test_08_broken_file_returns_empty_map(tmp_path):
    _f = _mkfile(tmp_path, "organs/body/Bad.py", "def (:\n")
    assert SI._m374_import_map(_f) == {}
    assert len(_run(_f, "get_x().y()")) == 1


def test_13_relative_import_unresolvable_is_reported(tmp_path):
    """仓外文件里的相对导入 ⇒ 包名不可解析 ⇒ **不猜** ⇒ 未解析 ⇒ 保守上报。

    猜成裸模块名（`PulseCortex`）就会落到「非 organs ⇒ 基础设施豁免」，
    真跨器官调用从此静默。这条钉的就是那个下坠方向。
    """
    _f = _mkfile(tmp_path, "elsewhere/PulseZ.py",
                 "from .PulseCortex import get_cortex_organ\n")
    _iss = _run(_f, "get_cortex_organ().think()")
    assert len(_iss) == 1, _iss
    assert "来源=未解析" in _iss[0]["description"]


# --------------------------------------------- ⑤ 356 两条排除不回归
def test_09_contract_singletons_still_exempt(tmp_path):
    _f = _mkfile(tmp_path, "organs/body/PulseX.py",
                 "from nucleus.InsightBoard import get_insight_board\n"
                 "from nucleus.PulseBus import get_event_bus\n")
    assert _run(_f, "get_insight_board().post(t=1)") == []
    assert _run(_f, "get_event_bus().publish(1)") == []


def test_10_self_call_never_reported(tmp_path):
    _f = _mkfile(tmp_path, "organs/body/PulseX.py", "")
    _iss = []
    _inst()._check_cross_module_singleton_call(
        "def f(self):\n    self.node_pool.get_stats()\n",
        _f, "PulseX", "f", 1, _iss)
    assert not _iss


# ------------------------------- ⑥ 全树形态断言（不拿绝对条数当判据）
def test_11_full_tree_shape_only_organ_or_unresolved():
    """扫真实 `organs/`：**凡被报出者，来源必须是 `organs.*` 或未解析**。

    不断言「必须 0 条」——那是把测试绑在 live 语料上（将来真出现跨器官调用时
    应当照报）。这里断言的是**判据形态**：基础设施来源一条都不许出现。
    """
    _o = _inst()
    _bad = []
    _n = 0
    for _d, _sub, _fs in os.walk("organs"):
        _sub[:] = [x for x in _sub if x != "__pycache__"]
        for _fn in sorted(_fs):
            if not _fn.endswith(".py"):
                continue
            _p = os.path.join(_d, _fn).replace("\\", "/")
            with open(_p, encoding="utf-8", errors="replace") as _f:
                _src = _f.read()
            _iss = []
            _o._check_cross_module_singleton_call(_src, _p, _fn, "<file>", 1, _iss)
            for _i in _iss:
                _n += 1
                _dsc = _i["description"]
                _origin = _dsc.split("来源=")[-1].split("）")[0]
                if _origin != "未解析" and not _origin.startswith("organs."):
                    _bad.append((_i["file"], _origin))
    assert not _bad, f"基础设施工厂仍被误报：{_bad[:5]}"
    print(f"[374刀5] 全树共命中 {_n} 条，全部为器官来源或未解析")


def test_12_import_map_cached_per_path(tmp_path):
    """同一文件只解析一次（检测器按方法调用，缓存是性能前提）。"""
    _f = _mkfile(tmp_path, "organs/body/PulseX.py",
                 "from nucleus.runtime_tempo import get_runtime_tempo\n")
    _a = SI._m374_import_map(_f)
    assert _a is SI._m374_import_map(_f), "未命中 lru_cache"
    assert _a["get_runtime_tempo"] == "nucleus.runtime_tempo"


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    SI._m374_import_map.cache_clear()
