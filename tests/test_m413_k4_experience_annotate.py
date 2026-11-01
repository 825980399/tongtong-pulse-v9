# -*- coding: utf-8 -*-
"""413 批刀4 单测：经验池模板标记的**假阳性**修正。

413 T0 实测的三个坑（每条都对应一组判据）：
  1. `query_experiences` 返回 `dict(exp)` **副本** ⇒ 在副本上改标记，
     `_save()` 写回原件，**一条都落不了盘**（实测 is_template=0）；
  2. `_save()` 遇写盘守卫是**静默 return**（不抛异常）⇒ 旧 `annotate()`
     报 `written: true`，是"动作已发出 ≠ 结果已发生"的标准案例；
  3. 框架进程会周期性 `_save()` ⇒ 外部脚本写进去也会被覆盖
     ⇒ 只能挂在 04:00 编译点由框架进程自己写。
"""

from __future__ import annotations

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.observability import experience_governance as eg  # noqa: E402


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read() \
        .replace("\r\n", "\n")


class _CopyPool:
    """复刻**真池**语义：`query_experiences` 返回 `dict(exp)` 副本。"""

    def __init__(self, rows, with_apply=True):
        self.rows = [dict(x) for x in rows]
        self.saved = 0
        self._with_apply = with_apply
        if with_apply:
            self.annotate_flags = self._annotate_flags

    def query_experiences(self, limit=10):
        return [dict(x) for x in self.rows]

    def _annotate_flags(self, flags):
        _n = 0
        for _e in self.rows:
            _u = flags.get(str(_e.get("id") or ""))
            if not _u:
                continue
            _hit = False
            for _k, _v in _u.items():
                if _v is None:
                    if _k in _e:
                        _e.pop(_k, None)
                        _hit = True
                elif _e.get(_k) != _v:
                    _e[_k] = _v
                    _hit = True
            if _hit:
                _n += 1
        return _n

    def _save(self):
        self.saved += 1


def _rows():
    return ([{"id": "t%d" % i, "summary": "模板A"} for i in range(5)]
            + [{"id": "u%d" % i, "summary": "各不相同%d" % i} for i in range(3)]
            + [{"id": "n1", "summary": ""}])


# ============================================================ A 副本语义

def test_a1_copy_semantics_pool_still_lands_flags():
    """★核心判据：池返回副本时，回写层必须把标记写到**原对象**上。"""
    _p = _CopyPool(_rows())
    _r = eg.annotate(_p, dry_run=False, min_dup=3)
    assert _r["written"] is True, _r.get("reason")
    assert _r["landed"] == 5, "读回必须看到 5 条模板标记"
    assert sum(1 for x in _p.rows if x.get("is_template")) == 5
    assert _r["applied"] == 5, "回写层只该动差异条目"


def test_a2_without_apply_entry_it_must_not_claim_success():
    """★先红证明：没有回写入口时，旧实现会报 written=True（其实是 0 落盘）。

    现在必须诚实报 verify_mismatch —— 这就是"结果已发生"才叫成功。
    """
    _p = _CopyPool(_rows(), with_apply=False)
    _r = eg.annotate(_p, dry_run=False, min_dup=3)
    assert _r["written"] is False, "改副本 + save 原对象 ⇒ 不许报成功"
    assert _r["reason"].startswith("verify_mismatch"), _r["reason"]
    assert _r["landed"] == 0
    assert sum(1 for x in _p.rows if x.get("is_template")) == 0


def test_a3_real_pool_returns_copies_not_references():
    """锁住真池语义 —— 这条一变，`annotate` 的回写设计就失效了。"""
    from nucleus.mnemosyne.experience_pool import get_experience_pool
    _p = get_experience_pool()
    _inner = getattr(_p, "_experiences", None) or []
    if not _inner:
        pytest.skip("生产经验池为空，跳过副本语义断言")
    _rows = _p.query_experiences(limit=100000)
    assert _rows, "查询结果不应为空"
    assert _rows[0] is not _inner[0], "query_experiences 必须返回副本（不是原引用）"


# ============================================================ B 静默失败

def test_b1_silent_save_failure_is_caught(monkeypatch):
    """`_save()` 什么都不做（等价于守卫静默 return）⇒ 必须读回校验抓到。"""

    class _SilentSave(_CopyPool):
        def _save(self):
            super()._save()
            # 故意不落任何东西：模拟守卫静默 return

    _p = _SilentSave(_rows(), with_apply=False)
    _r = eg.annotate(_p, dry_run=False, min_dup=3)
    assert _r["written"] is False
    assert _r["reason"].startswith("verify_mismatch")


def test_b2_write_guard_refusal_is_reported_not_swallowed(monkeypatch):
    """守卫拒写 ⇒ `written=False` + `write_guard_refused`（不是"成功"）。"""
    import nucleus.data.write_guard as wg
    monkeypatch.setattr(wg, "guard_write", lambda *a, **k: False, raising=False)
    _p = _CopyPool(_rows())
    _p._pool_file = os.path.join(_ROOT, "data", "experience", "experience_pool.json")
    _r = eg.annotate(_p, dry_run=False, min_dup=3)
    assert _r["ok"] is False
    assert _r["written"] is False
    assert _r["reason"] == "write_guard_refused"
    assert _r["applied"] == 5, "回写先在内存完成，落盘才被拦 —— 两个事实都要报"


# ============================================================ C 不删条目

def test_c1_annotate_never_deletes_rows():
    _p = _CopyPool(_rows())
    _before = len(_p.rows)
    eg.annotate(_p, dry_run=False, min_dup=3)
    assert len(_p.rows) == _before, "治理只打标记，绝不删条目"


def test_c2_non_template_rows_get_no_key():
    _p = _CopyPool(_rows())
    eg.annotate(_p, dry_run=False, min_dup=3)
    for _x in _p.rows:
        if str(_x.get("id") or "").startswith("u"):
            assert "is_template" not in _x, "非模板条不该被写上 is_template"


def test_c3_dry_run_changes_nothing():
    _p = _CopyPool(_rows())
    _r = eg.annotate(_p, dry_run=True, min_dup=3)
    assert _r["written"] is False and _r["reason"] == "dry_run"
    assert all("is_template" not in x for x in _p.rows)
    assert _p.saved == 0


# ============================================================ D 夜摘要接线

def _night_src():
    return _src("nucleus/evolution/night_orchestration.py")


def test_d1_night_digest_has_experience_segment():
    _s = _night_src()
    _i = _s.find("九、经验池模板标记")
    assert _i > 0, "夜摘要必须有经验池段"
    assert _i < _s.find('_digest_path, "w"'), "段必须在写盘之前"


def test_d2_segment_runs_without_a_new_scheduler():
    _s = _night_src()
    _i = _s.find("九、经验池模板标记")
    _seg = _s[_i - 1200:_i + 1800]
    for _bad in ("threading.Thread", "Timer(", "sched.", "asyncio.create_task"):
        assert _bad not in _seg, "410/412/413 红线：不新建调度器"


def test_d3_segment_asks_for_a_real_write():
    _s = _night_src()
    _i = _s.find("九、经验池模板标记")
    _seg = _s[_i - 1200:_i + 1800]
    assert "annotate(dry_run=False)" in _seg, "04:00 段必须是真写，不是 dry-run"
    assert "landed" in _seg, "段里要展示读回数，否则又变成「看起来成功」"


# ============================================================ E 工具探针

def test_e1_probe_pool_replicates_copy_semantics():
    """★412 的探针假池返回原引用 ⇒ 把真 bug 全放过去了（假绿）。"""
    _t = ast.parse(_src("tools/m412_experience_governance.py"))
    _cls = next(n for n in ast.walk(_t)
                if isinstance(n, ast.ClassDef) and n.name == "_Pool")
    _names = {n.name for n in _cls.body if isinstance(n, ast.FunctionDef)}
    assert "annotate_flags" in _names, "探针假池必须提供与真池同名的回写入口"
    _q = next(n for n in _cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "query_experiences")
    _seg = ast.get_source_segment(_src("tools/m412_experience_governance.py"), _q) or ""
    assert "dict(x)" in _seg, "探针假池必须返回副本，否则复刻不了真池"


def test_e2_probe_checks_landed():
    _s = _src("tools/m412_experience_governance.py")
    assert "_r2.get(\"landed\") == 5" in _s, "探针要钉读回数，不能只看 written"


# ============================================================ F 行尾

def test_f1_touched_files_keep_their_line_endings():
    for _rel, _crlf in (("nucleus/mnemosyne/experience_pool.py", True),
                        ("nucleus/observability/experience_governance.py", False),
                        ("tools/m412_experience_governance.py", False)):
        _b = io.open(os.path.join(_ROOT, _rel), "rb").read()
        _c = _b.count(b"\r\n")
        _l = _b.count(b"\n")
        if _crlf:
            assert _c == _l > 0, "%s 应为纯 CRLF" % _rel
        else:
            assert _c == 0, "%s 应为纯 LF" % _rel
