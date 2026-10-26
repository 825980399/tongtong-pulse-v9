# -*- coding: utf-8 -*-
"""369 刀5（N4 测试基建收口：六件动态路径写面沙箱化 + 35 件盘点）定向单测。

覆盖任务书刀5 要求的 5 例：
  ① 六件注入后写入路径全部落在沙箱根下（逐件断言路径前缀）；
  ② m49 归因落表（不是"瞬删瞬建"，是 dump_samples._roll 滚动保留 7 份）；
  ③ m53 破坏性用例隔离（注入覆盖其删除面，生产 data/ 零删除）；
  ④ 35 件盘点清单与实测全集一一对应；
  ⑤ 既有已重定向件与设计内件不回归（_M366_WRITE_REDIRECTS / _M366_DESIGNED_SUBDIRS 仍在）。

★本刀不改任何生产代码；注入一律走**生产侧已留好的注入口**（铁律158）。
"""
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

WM = os.path.join(_ROOT, "logs", "full_regression", "n4_writemap")

SIX = (
    "test_dialog_evol_m84.py",
    "test_evolution_backup_filter_m60.py",
    "test_t101a_low_risk_release.py",
    "test_t105a_release_loop.py",
    "test_tool_wiring_m49.py",
    "test_patch_self_apply_m53.py",
)


@pytest.fixture(scope="module")
def conftest_mod():
    import conftest

    return conftest


# ------------------------------------------------------------------ ①

def test_six_targets_registered(conftest_mod):
    """①前：六件全部登记在白名单里（漏一件就说明清单不全）。"""
    targets = conftest_mod._M369_DYNAMIC_TARGETS
    missing = [t for t in SIX if t not in targets]
    assert not missing, "白名单缺件：%s" % missing


def test_inject_all_puts_every_write_under_sandbox(conftest_mod, tmp_path):
    """① 六件注入后，每个写口的**绝对路径**都必须落在沙箱根下。

    ★逐件断言前缀，而不是只看"注入没报错" —— 注入成功但路径没进沙箱是本刀最大的风险。
    """
    root = conftest_mod.m369_sandbox_root(str(tmp_path))
    done = conftest_mod.m369_inject_all(str(tmp_path))
    assert done, "注入器未产生任何注入点"
    for k, v in done.items():
        assert os.path.isabs(v), "%s 注入值不是绝对路径：%s" % (k, v)
        assert os.path.abspath(v).startswith(os.path.abspath(root)), \
            "%s 的写口 %s 未落在沙箱根 %s 下" % (k, v, root)
    # 关键三族必须在列
    for k in ("env.PULSE_ISOLATION_ROOT",
              "verification_learning_hub._hub",
              "PatchAutoApprover._ISO_BASE_DIR",
              "unresolved_samples._dir_of"):
        assert k in done, "缺关键注入点：%s" % k


def test_unresolved_dir_of_returns_sandbox(conftest_mod, tmp_path):
    """① 附：_dir_of 被注入后，写与 _roll 删除都在沙箱内（m49 删除面的收口点）。"""
    conftest_mod.m369_inject_all(str(tmp_path))
    import nucleus.self_awareness.unresolved_samples as us

    d = us._dir_of()
    assert os.path.abspath(d).startswith(
        os.path.abspath(conftest_mod.m369_sandbox_root(str(tmp_path))))
    # 目录中不应残留生产路径特征
    assert "tongtong-pulse-v9" not in d.replace("\\", "/") or \
        os.path.abspath(d).startswith(os.path.abspath(str(tmp_path)))


# ------------------------------------------------------------------ ②

def test_m49_attribution_is_roll_not_churn():
    """② m49 的「脏」必须是**归因落表**的：来自 dump_samples._roll 滚动保留，不是瞬删瞬建。"""
    p = os.path.join(WM, "test_tool_wiring_m49.py.json")
    if not os.path.exists(p):
        pytest.skip("无 m49 写面记录")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    removed = (d.get("session", {}).get("tree", {}) or {}).get("removed") or []
    assert removed, "m49 写面记录里应有删除明细（归因对象）"
    # 删除目标必须都是 unresolved_samples_*（⇒ 指向 _roll，不是别的清理）
    for r in removed:
        assert "unresolved_samples_" in r, \
            "m49 删除了预期外的文件（归因不成立）：%s" % r
    # 与 _roll 的 _KEEP=7 语义一致：只删 .jsonl 样本
    import nucleus.self_awareness.unresolved_samples as us

    assert us._KEEP >= 1, "_roll 保留份数语义应存在"


def test_m49_attribution_recorded_in_targets(conftest_mod):
    """② 附：归因结论必须写进 _M369_DYNAMIC_TARGETS 的理由字段（可查，不靠口头）。"""
    reason, _files = conftest_mod._M369_DYNAMIC_TARGETS["test_tool_wiring_m49.py"]
    assert "_roll" in reason or "滚动" in reason, \
        "m49 的理由须点名 _roll 滚动，而不是含糊写成『瞬删瞬建』"


# ------------------------------------------------------------------ ③

def test_m53_is_whitelisted_and_destructive_flagged(conftest_mod):
    """③ m53（破坏性）必须在白名单内，且理由标注「破坏性/隔离执行」。"""
    t = conftest_mod._M369_DYNAMIC_TARGETS
    assert "test_patch_self_apply_m53.py" in t
    reason, files = t["test_patch_self_apply_m53.py"]
    assert "破坏性" in reason or "隔离" in reason
    assert "top_types" in files, "m53 的删除面应点名 top_types_*"


def test_m53_delete_face_covered_by_injection(conftest_mod, tmp_path):
    """③ 附：m53 的删除面（top_types_*）由 PULSE_ISOLATION_ROOT 改道覆盖。

    ★结构上保证零删除：guard_artifact_dir 在隔离声明下会把产物目录整体改道，
      m53 触发的滚动删除随之落在沙箱内。
    """
    conftest_mod.m369_inject_all(str(tmp_path))
    assert os.environ.get("PULSE_ISOLATION_ROOT"), "隔离 root 未设置"
    import nucleus.domain_guard as dg

    assert dg.isolation_declared(), "隔离声明未命中 ⇒ 改道不会生效"
    _new, why = dg.guard_artifact_dir("data/reports/memory")
    assert why == "isolation_declared", "产物目录未改道：%s" % why
    assert os.path.abspath(_new).startswith(
        os.path.abspath(conftest_mod.m369_sandbox_root(str(tmp_path))))


# ------------------------------------------------------------------ ④

def test_inventory_matches_writemap_universe():
    """④ 盘点清单件数必须等于写面图实测全集（不缺件、不虚增）。"""
    if not os.path.isdir(WM):
        pytest.skip("无写面图目录")
    n_files = len([f for f in os.listdir(WM) if f.endswith(".json")])
    p = os.path.join(_ROOT, "tmp", "m369_b7_35inventory.json")
    if not os.path.exists(p):
        pytest.skip("盘点清单尚未生成（先跑 tmp/audit_369_b7_35.py）")
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    assert len(rows) == n_files, \
        "盘点清单 %d 件 != 写面图 %d 件" % (len(rows), n_files)
    # 真写面 10 件必须都能在清单里查到，且逐件带标记字段
    real = [r for r in rows
            if (r["added"] or r["removed"] or r["modified"])]
    assert len(real) == 10, "真写面应为 10 件（N4 续三口径），实际 %d" % len(real)
    for r in real:
        assert "marked_production_data" in r, "盘点缺『是否带标记』字段：%s" % r["test_file"]


# ------------------------------------------------------------------ ⑤

def test_existing_redirects_and_designed_subdirs_intact(conftest_mod):
    """⑤ 既有已重定向件与设计内件不得被本刀破坏。"""
    table = conftest_mod._M366_WRITE_REDIRECTS
    rels = {t[2] for t in table}
    for expect in ("data/reports/converge_voided.jsonl",
                   "data/evolution/evolution_budget.json"):
        assert expect in rels, "既有重定向表缺项：%s" % expect
    assert conftest_mod._M366_DESIGNED_SUBDIRS, "设计内目录清单被清空"
    assert "data/.t_face_roster/" in conftest_mod._M366_DESIGNED_SUBDIRS
    # 纯函数仍可算路径
    plan = conftest_mod.m366_redirect_plan("/tmp/sbx")
    assert plan and all(p[2].startswith("/tmp/sbx") for p in plan)


def test_no_production_code_changed():
    """⑤ 附：本刀不得改动生产代码（只动 tests/conftest.py）。"""
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    r = subprocess.run(["git", "status", "--porcelain", "--",
                        "nucleus/", "organs/", "config.py", "main.py"],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    changed = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    assert not changed, "生产面出现改动（本刀只应动 tests/conftest.py）：%s" % changed[:5]
