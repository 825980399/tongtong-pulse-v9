# -*- coding: utf-8 -*-
"""384 刀4：`dedup_key_v2` —— 按**真实方法名**产出，v1 双写不迁移。

## v2 到底补的是什么

v1 = `sha1(norm_file|method|issue_type)[:16]`，method 取 `patch["method"]` 字段。
历史条目里这个字段可能是推断名 / 带占位 / 与代码里真实 `def` 名不一致 ⇒ 同一个
问题被算成两条，去重失效。

v2 用 `_m323_ast_def_name(modified_code)` 解析出的**真实 def 名**，公式不变。
所以 `v1 == v2` 说明 method 字段本来就是真名；`v1 != v2` 才暴露问题。

## 三态 + 一条铁律

    same     真实名 == method 字段（仍写 v2，便于下游统一取 v2）
    differs  两者不同（v2 要暴露的就是这个）
    skipped  解析不出 def 名 ⇒ **不写 v2**

★铁律：`skipped` 时**绝不许**写一个占位 v2 —— 假键会让去重按错的组归并，
  比没有键更糟（与 352 批「不自洽的键比没键更糟」同一口径）。

★存量不迁移：只保证**新条目**双写。存量就地改写会让 v1 与池侧 `dedup_key`
  对不上，两侧对账直接断掉。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.patch_generator import make_dedup_key  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager as P  # noqa: E402

BASE = {
    "id": "t384",
    "file": "nucleus/x.py",
    "issue_type": "silent_exception",
    "method": "foo",
    "repair_source": "local_rule",
}


def _p(**over):
    d = dict(BASE)
    d.update(over)
    return d


def _v1(patch):
    return make_dedup_key(patch["file"], patch["method"], patch["issue_type"])


# ------------------------------------------------------------------ ① same
def test_m384_d4_same_when_method_is_real():
    """真实 def 名 == method 字段 ⇒ same，且 v2 与 v1 同值。"""
    p = _p(modified_code="def foo():\n    return 1\n")
    v1 = _v1(p)
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "same"
    assert r["real_method"] == "foo"
    assert r["v2"] == v1
    assert p["dedup_key_v2"] == v1


# --------------------------------------------------------------- ② differs
def test_m384_d4_differs_when_method_is_guess():
    """★核心：method 是推断名 ⇒ differs，v2 用真实名算（与 v1 不同）。"""
    p = _p(method="foo_guess", modified_code="def foo():\n    return 1\n")
    v1 = _v1(p)
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "differs"
    assert r["real_method"] == "foo"
    assert r["v2"] != v1
    assert r["v2"] == make_dedup_key(p["file"], "foo", p["issue_type"])


def test_m384_d4_method_empty_uses_real_name():
    """method 为空时也能产出 v2（用真实名），并留 why。"""
    p = _p(method="", modified_code="def bar():\n    pass\n")
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "differs"
    assert r["real_method"] == "bar"
    assert r["why"] == "method_empty_use_real"
    assert p["dedup_key_v2"] == make_dedup_key(p["file"], "bar", p["issue_type"])


# --------------------------------------------------- ③ skipped 且绝不写假键
def test_m384_d4_skipped_without_modified_code():
    p = _p()
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "skipped"
    assert r["why"] == "no_modified_code"
    assert "dedup_key_v2" not in p, "skipped 时绝不许写 v2（假键比没键更糟）"


def test_m384_d4_skipped_when_code_has_no_def():
    p = _p(modified_code="x = 1\ny = 2\n")
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "skipped"
    assert r["why"] == "no_def_in_code"
    assert "dedup_key_v2" not in p


def test_m384_d4_skipped_without_file():
    for bad in (None, {}, {"file": ""}, {"file": "   "}):
        r = P._m384_ensure_dedup_key_v2(bad)
        assert r["action"] == "skipped" and r["why"] == "missing_file"


# ------------------------------------------- ④ v1 不被覆盖 + 重缩进片段
def test_m384_d4_v1_preserved_when_writing_v2():
    """★双写：写 v2 不能动 v1（v1 是池侧对账的稳定键）。"""
    p = _p(method="foo_guess", modified_code="def foo():\n    return 1\n")
    v1 = _v1(p)
    p["dedup_key"] = v1
    P._m384_ensure_dedup_key_v2(p)
    assert p["dedup_key"] == v1, "v1 被改写了 ⇒ 与池侧对账会断"
    assert p["dedup_key_v2"] != v1


def test_m384_d4_handles_indented_snippet():
    """片段带缩进（方法体形态）也能解析出 def 名（走 `_m323_ast_def_name` 重缩进重试）。"""
    p = _p(method="baz", modified_code="    def baz(self):\n        return 2\n")
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["action"] == "same" and r["real_method"] == "baz"


def test_m384_d4_picks_first_def_only():
    """只取第一个 def（与 280 批「modified_code 首个 def 名==method」硬约束同口径）。"""
    p = _p(method="first", modified_code="def first():\n    pass\n\n\ndef second():\n    pass\n")
    r = P._m384_ensure_dedup_key_v2(p)
    assert r["real_method"] == "first"


# --------------------------------------------------------- ⑤ 自洽与接线
def test_m384_d4_v2_formula_is_shared():
    """v2 必须复用 `patch_generator.make_dedup_key`，不另写一份公式。"""
    src = io.open(os.path.join(ROOT, "nucleus/reasoning/PatchManager.py"),
                  encoding="utf-8").read()
    _seg = src[src.index("def _m384_ensure_dedup_key_v2"):
               src.index("def _m80_is_core_file")]
    assert "patch_generator import make_dedup_key" in _seg
    assert "hashlib" not in _seg, "v2 里不该自己算 sha1 —— 公式必须同源"


def test_m384_d4_wired_in_save_pending_patch():
    """★接线守卫：`save_pending_patch` 里必须真的调了它，并在 v1 之后。"""
    src = io.open(os.path.join(ROOT, "nucleus/reasoning/PatchManager.py"),
                  encoding="utf-8").read()
    assert "_m384_v2 = PatchManager._m384_ensure_dedup_key_v2(patch)" in src
    _i352 = src.index("_m352_dk = PatchManager._m352_ensure_dedup_key(patch)")
    _i384 = src.index("_m384_v2 = PatchManager._m384_ensure_dedup_key_v2(patch)")
    assert _i352 < _i384, "v2 必须在 v1 之后（v1 是入池必有键，v2 叠在其上）"
    assert 'patch["dedup_key_v2_status"] = _m384_v2["action"]' in src


def test_m384_d4_never_raises_on_weird_types():
    """字段类型畸形也不许抛（走 str() 兜底）。"""
    for weird in ({"file": 123, "modified_code": 456},
                  {"file": None, "modified_code": "def f():\n    pass\n"},
                  {"file": "a.py", "issue_type": None, "modified_code": "def f():\n    pass\n"}):
        r = P._m384_ensure_dedup_key_v2(weird)
        assert r["action"] in ("same", "differs", "skipped")
