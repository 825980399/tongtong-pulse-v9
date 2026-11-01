# -*- coding: utf-8 -*-
"""417 刀4 单测：手动 deferred 验证工具自身。

判据取向：
  ① 四项核心检查在**隔离目录**里必须全 PASS（工具自己报绿不算，本单测独立复核）；
  ② 隔离用的 `set_data_dir()` 必须**还原**（409 批踩过「只改一半」的坑）——
     跑完之后 `narrative_store.data_root()` 必须回到仓库根；
  ③ ②（模板标记）在生产池上**只读**：调用前后 `experience_pool.json` 的
     sha256 必须不变（铁律 22：运行进程持有的数据文件不能由外部写）；
  ④ `annotate` 的真写判据必须落到**新实例读回**，不是「内存里改到了」；
  ⑤ 工具的 `_rec/_skip` 账本自洽（PASS/FAIL/SKIP 数 == 结果条数）。

不覆盖（有意）：`check_1b_production`（会真写 `data/narrative/`）与
`check_3_o3_report`（单跑 ~47s，且读生产审计文件）—— 两者由工具本体在
批内手工跑一次并留 `tmp/m417_k4_verify.json` 存证，不进回归集。
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TOOL = ROOT / "tools" / "m417_manual_deferred_verify.py"


def _load_tool():
    _spec = importlib.util.spec_from_file_location("m417_k4_tool", _TOOL)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


@pytest.fixture()
def tool():
    _m = _load_tool()
    _m.RESULTS.clear()
    yield _m
    _m.RESULTS.clear()


def _last(tool) -> dict:
    assert tool.RESULTS, "工具一条结果都没产出"
    return tool.RESULTS[-1]


# ============================================================ ① 隔离落盘
def test_10_isolated_persist_ok(tool, tmp_path):
    tool.check_1_isolated(str(tmp_path))
    _r = _last(tool)
    _d = _r["detail"]
    assert _r["verdict"] == "PASS", _d
    assert _d["ok"] is True and _d["path_match"] is True
    assert _d["rows"] == 1 and _d["bad_lines"] == 0 and _d["bytes"] > 0
    assert Path(_d["path"]).is_file()
    # 文件名口径：narrative_YYYYMMDD.jsonl
    _name = Path(_d["path"]).name
    assert _name.startswith("narrative_") and _name.endswith(".jsonl")
    assert len(_name) == len("narrative_YYYYMMDD.jsonl")


# ============================================================ ② 真调用方
def test_21_real_caller_persist_ok(tool, tmp_path):
    tool.check_1c_real_caller(str(tmp_path))
    _r = _last(tool)
    _d = _r["detail"]
    assert _r["verdict"] == "PASS", _d
    assert _d["ok"] is True and _d["rows"] == 1
    # ★探针事件形状必须与 `_on_record` 同形（含 `timestamp`），否则 events_of_day 取不到
    assert _d["narrative_events"] == 3, "事件没被 digest 计进当日：%r" % (_d,)
    assert _d["events_landed"] == 3
    assert _d["delta_has_4th"] is True
    assert set(_d["delta_keys"]) == {"nodes", "llm_ratio", "patches", "narrative_events"}
    assert set(_d["digest_keys"]) == {"headline", "headline_basis", "intent",
                                      "intent_reason", "delta", "evidence_refs"}


# ============================================================ ③ 模板标记真写
def test_30_template_annotate_writes_and_rereads(tool, tmp_path):
    tool.check_2b_templates_write(str(tmp_path))
    _r = _last(tool)
    _d = _r["detail"]
    assert _r["verdict"] == "PASS", _d
    _a = _d["annotate"]
    assert _a["written"] is True, "只报 written=False（`_save()` 可能静默 return）"
    assert _a["landed"] == _a["template_rows"] == 4
    assert _a["applied"] == 4
    # 换新实例从盘上读回：4 条已标 + 1 条未标
    assert _d["reread_total"] == 5
    assert _d["reread_marked"] == 4 and _d["reread_plain"] == 1
    assert _d["weights_marked"] == [0.3] and _d["weights_plain"] == [1.0]
    # 幂等：第二次 changed=0
    assert _d["annotate_2nd"]["changed"] == 0
    assert _d["annotate_2nd"]["reason"] == "no_change"
    assert _d["file_bytes"] > 0


# ============================================================ ④ 写→读回
def test_40_roundtrip_ok(tool, tmp_path):
    tool.check_4_roundtrip(str(tmp_path))
    _r = _last(tool)
    _d = _r["detail"]
    assert _r["verdict"] == "PASS", _d
    assert _d["write_ok"] is True and _d["state"] == "ok"
    assert _d["rows"] == 1 and _d["days_with_file"] == 1 and _d["bad_lines"] == 0
    assert _d["headline"] == "[k4-4] 往返探针"
    assert _d["events_readback"] == 3 and _d["events_truncated"] is False
    assert _d["narrative_events"] == 3
    assert _d["nodes_delta"] == {"cur": 12, "prev": 10, "delta": 2,
                                 "source": "probe", "reason": ""}


# ============================================================ ⑤ 沙箱必须还原
def test_50_sandbox_restores_data_root(tool, tmp_path):
    from nucleus.observability import narrative_store as _ns
    _before = _ns.data_root()
    tool.check_1_isolated(str(tmp_path))
    assert _ns.data_root() == _before, "set_data_dir 没还原（读侧会继续指向 tmp）"
    tool.check_4_roundtrip(str(tmp_path))
    assert _ns.data_root() == _before
    # 还原后落盘路径必须回到仓库根的 data/narrative
    assert Path(_ns.narrative_dir()) == ROOT / "data" / "narrative"


# ============================================================ ⑥ 生产池只读
def test_60_production_pool_untouched(tool):
    """`check_2_templates_readonly` 不许改生产池一个字节（铁律 22）。"""
    _pf = ROOT / "data" / "experience" / "experience_pool.json"
    if not _pf.is_file():
        pytest.skip("生产池不存在（干净沙箱）")
    _h0 = hashlib.sha256(_pf.read_bytes()).hexdigest()
    _m0 = _pf.stat().st_mtime_ns
    tool.check_2_templates_readonly()
    _d = _last(tool)["detail"]
    assert _last(tool)["verdict"] == "PASS", _d
    # dry_run 必须显式声明没写
    assert _d["dry_run"]["written"] is False
    assert _d["dry_run"]["reason"] == "dry_run"
    assert hashlib.sha256(_pf.read_bytes()).hexdigest() == _h0, "生产池被改了（sha 变了）"
    assert _pf.stat().st_mtime_ns == _m0, "生产池被写过（mtime 变了）"


# ============================================================ ⑦ 账本自洽
def test_70_ledger_self_consistent(tool, tmp_path):
    tool.check_1_isolated(str(tmp_path))
    tool.check_1c_real_caller(str(tmp_path))
    tool.check_2b_templates_write(str(tmp_path))
    tool.check_4_roundtrip(str(tmp_path))
    tool._skip("x", "假跳过项", "unit-test")
    _v = [x["verdict"] for x in tool.RESULTS]
    assert len(_v) == 5, _v
    assert _v == ["PASS", "PASS", "PASS", "PASS", "SKIP"], _v
    assert all(x["id"] for x in tool.RESULTS)
    assert all(isinstance(x["detail"], dict) for x in tool.RESULTS)


# ============================================================ ⑧ 工具静态口径
def test_80_tool_has_safety_flags():
    _src = _TOOL.read_text(encoding="utf-8")
    assert "--skip-prod" in _src, "缺 --skip-prod 逃生口"
    assert "--keep" in _src, "缺 --keep（不删生产探测行）"
    # 生产池绝不能被 annotate 直写：每个 annotate 调用要么带 pool=（隔离池），
    # 要么显式 dry_run=True（只读）。两者都没有 = 会写生产池。
    try:
        import ast as _ast
    except ImportError:  # pragma: no cover
        pytest.skip("no ast")
    for _n in _ast.walk(_ast.parse(_src)):
        if not (isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Attribute)
                and _n.func.attr == "annotate"):
            continue
        _kw = {k.arg: k.value for k in _n.keywords}
        _ro = isinstance(_kw.get("dry_run"), _ast.Constant) and _kw["dry_run"].value is True
        assert "pool" in _kw or _ro, \
            "第 %d 行的 annotate 既没传 pool 也没 dry_run=True ⇒ 会写生产池" % _n.lineno


# ============================================================ ⑨ 能当脚本直接跑
def test_85_runs_as_script():
    """`python tools/xxx.py` 必须能跑起来（模块级 import 不许在 sys.path 注入之前）。

    ★这条是被真事故逼出来的：`from nucleus._silent_except import silent_exc` 原先写在
      `sys.path.insert(0, ROOT)` **之前**。pytest 里 ROOT 早就在 sys.path 里 ⇒ 全绿；
      命令行直接跑 ⇒ `ModuleNotFoundError: No module named 'nucleus'`。
      子进程 + `--help` 是最便宜的复现方式，1 秒内出结果。
    """
    import subprocess
    _env = dict(os.environ)
    _env.pop("PYTHONPATH", None)
    _r = subprocess.run([sys.executable, str(_TOOL), "--help"],
                        capture_output=True, text=True, encoding="utf-8", timeout=120,
                        cwd=str(ROOT), env=_env)
    assert _r.returncode == 0, "脚本不能直接跑：\n%s\n%s" % (_r.stdout[-800:], _r.stderr[-1500:])
    assert "--skip-prod" in _r.stdout and "--keep" in _r.stdout
