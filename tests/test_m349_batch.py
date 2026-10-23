# -*- coding: utf-8 -*-
"""第 349 批测试：裁决①②落地 + shadow→live 第一环 + 判据/线程/消费面收口。

覆盖（全部**纯函数或 tmp_path 隔离**，不依赖现场数据 —— 铁律179）：
  · 刀1  `patch_generator`：dedup_key 与池同构 / 六字段证据门 / 三态统计分母 / 回复解析
  · 刀2  `official_verdict_read`：三态读 / NOT_RUN 语义 / 计数分档 / 写侧禁令
  · 刀4  `zero_break_judge`：破零四条件 / 字符串 "false" 不算 True / 召回率分母只含豁免
  · 刀10② `audit_problem_pool.norm_file_field`：盘符绝对 / `./` / 幂等 / 异盘保留
  · 刀6② `LogAnalyzer.dump_two_domains`：清单落盘
  · 刀8① `external_heartbeat.read_alert_tail`：**只读尾部**且末条正确
  · 刀8① `w1_consumers`：三条 landed 全 True
  · 刀11 `subproc_verify_pool`：互斥评估 / tmp 判定 / 空任务表
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== 刀1：补丁生成器 ====================
def test_m349_dedup_key_matches_pool():
    """生成器侧 dedup_key 必须与问题池**同一算法**（否则同一缺陷两个身份）。"""
    from nucleus.audit_problem_pool import make_dedup_key as pool_key
    from nucleus.evolution.patch_generator import make_dedup_key as gen_key

    _a = gen_key("organs/body/PulseLiver.py", "run", "dead_code")
    _b = pool_key("organs/body/PulseLiver.py", "run", "dead_code")
    assert _a == _b
    assert len(_a) == 16


def test_m349_evidence_six_fields():
    """六字段证据门：缺一个就拒；dedup_key 还必须**可复算自洽**。"""
    from nucleus.evolution.patch_generator import check_evidence_six, make_dedup_key

    _good = {"file": "a/b.py", "method": "run", "issue_type": "dead_code",
             "original_code": "    return 1\n", "modified_code": "    return 2\n",
             "reason": "改成 2"}
    _good["dedup_key"] = make_dedup_key(_good["file"], _good["method"], _good["issue_type"])
    assert check_evidence_six(_good)["ok"] is True

    _miss = dict(_good)
    _miss.pop("reason")
    _r = check_evidence_six(_miss)
    assert _r["ok"] is False and "reason" in _r["missing"]

    _badkey = dict(_good, dedup_key="0" * 16)
    _r2 = check_evidence_six(_badkey)
    assert _r2["ok"] is False
    assert "dedup_key" in _r2["missing"]


def test_m349_generation_stats_denominator():
    """合格率分母**不含** generation_failed（模型没答 ≠ 答错）。"""
    from nucleus.evolution.patch_generator import (
        STATE_DRAFT_READY,
        STATE_GATE_REJECTED,
        STATE_GENERATION_FAILED,
        generation_stats,
    )

    _rows = [{"state": STATE_DRAFT_READY}, {"state": STATE_GATE_REJECTED},
             {"state": STATE_GENERATION_FAILED}, {"state": STATE_GENERATION_FAILED}]
    _st = generation_stats(_rows)
    assert _st["counts"][STATE_GENERATION_FAILED] == 2
    assert _st["qualified_rate_denominator"] == 2
    assert _st["qualified_rate"] == pytest.approx(0.5)


def test_m349_parse_llm_reply_fallbacks():
    """回复解析三级降级：整段 JSON → `{...}` 截取 → 失败（不猜）。"""
    from nucleus.evolution.patch_generator import parse_llm_reply

    assert parse_llm_reply('{"anchor": "a", "replacement": "b", "reason": "c"}')["ok"] is True
    _wrap = '好的，下面是结果：\n{"anchor": "a", "replacement": "b", "reason": "c"}\n以上。'
    assert parse_llm_reply(_wrap)["ok"] is True
    assert parse_llm_reply("完全不是 JSON")["ok"] is False


# ==================== 刀2：official_verdict 读接线 ====================
def test_m349_read_verdict_three_states(tmp_path):
    """ABSENT / unreadable / ok 三态可区分，且值域外**不猜成最接近档**。"""
    from nucleus.evolution.official_verdict_read import read_verdict

    _gate = os.path.join(str(tmp_path), "data", "reports", "gate")
    _fp = os.path.join(_gate, "official_verdict.json")
    assert read_verdict(str(tmp_path))["state"] == "absent"

    os.makedirs(_gate, exist_ok=True)
    with open(_fp, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert read_verdict(str(tmp_path))["state"] == "unreadable"

    with open(_fp, "w", encoding="utf-8") as f:
        json.dump({"official_verdict": "OPEN"}, f)
    _r = read_verdict(str(tmp_path))
    assert _r["state"] == "ok" and _r["verdict"] == "OPEN"

    with open(_fp, "w", encoding="utf-8") as f:
        json.dump({"official_verdict": "MAYBE"}, f)
    _r2 = read_verdict(str(tmp_path))
    # ★值域外 ⇒ state=unreadable 且**verdict 不猜成最接近档**（不是 OPEN/CLOSED/NOT_RUN）
    assert _r2["state"] == "unreadable"
    assert _r2["verdict"] not in ("OPEN", "CLOSED", "NOT_RUN")
    assert "verdict_out_of_domain" in str(_r2.get("why") or "")


def test_m349_not_run_not_counted_as_unavailable():
    """NOT_RUN 必须单列，**不得**计入 UNAVAILABLE（两种事实不同措辞）。"""
    from nucleus.evolution.official_verdict_read import verdict_counts

    _rows = ["NOT_RUN", "UNAVAILABLE", "OPEN", "WEIRD"]
    _c = verdict_counts(_rows)
    assert _c.get("unavailable_excludes_not_run") is True
    # 分档键是大写 verdict 字面（COUNT_BUCKETS），不与 `not_run` 小写别名字段混用
    assert _c["NOT_RUN"] == 1
    assert _c["UNAVAILABLE"] == 1
    assert _c["out_of_domain"] == 1


def test_m349_write_side_bans_present():
    from nucleus.evolution.official_verdict_read import ban_ids, is_write_side_banned

    _ids = ban_ids()
    assert len(_ids) >= 7
    # 返回 `(banned, why)` 元组：布尔在第一位，理由单列（铁律153 不用理由串找关键字）
    assert is_write_side_banned("no_auto_dispatch")[0] is True
    assert is_write_side_banned("totally_made_up_action")[0] is False


# ==================== 刀4：破零口径 ====================
def test_m349_zero_break_four_conditions():
    """四条件同立才算破零；`applied="false"`（字符串）**不算** True。"""
    from nucleus.evolution.zero_break_judge import judge_zero_break

    _good = {"executed": 1, "applied": True, "status": "runtime_verified",
             "verified": True, "file": "organs/body/PulseLiver.py"}
    assert judge_zero_break(_good)["zero_broken"] is True

    _str_false = dict(_good, applied="false")
    assert judge_zero_break(_str_false)["zero_broken"] is False

    _iso = dict(_good, file="D:/x/tmp/p349_root/a.py")
    assert judge_zero_break(_iso)["zero_broken"] is False


def test_m349_recall_audit_denominator_only_exemptions():
    """召回率分母**只含真豁免**：被判死的（判对了）不得进分母。"""
    from nucleus.evolution.zero_break_judge import recall_audit_sample

    _samples = [
        {"id": "e1", "exempted_by": "protocol_iface", "dead_code_true": False},
        {"id": "d1", "exempted_by": "tier:hard_strict", "dead_code_true": True},
    ]
    _r = recall_audit_sample(_samples)
    assert _r["sample_size"] == 1
    assert _r["non_exempt_count"] == 1
    assert _r["recall"] == pytest.approx(1.0)


# ==================== 刀10②：file 字段归一 ====================
def test_m349_norm_file_field():
    from nucleus.audit_problem_pool import norm_file_field

    _abs = "D:\\xinrenlei\\tongtong-pulse-v9\\organs\\body\\PulseLiver.py"
    assert norm_file_field(_abs) == "organs/body/PulseLiver.py"
    assert norm_file_field("./organs/body/PulseLiver.py") == "organs/body/PulseLiver.py"
    # 幂等
    assert norm_file_field("organs/body/PulseLiver.py") == "organs/body/PulseLiver.py"
    # 异盘/别目录不硬切，只统一分隔符
    assert norm_file_field("E:\\other\\x.py") == "E:/other/x.py"
    # 大小写不敏感（Windows 路径语义）
    assert norm_file_field("d:\\XinRenLei\\TongTong-Pulse-V9\\a.py") == "a.py"


def test_m349_entry_file_raw_kept(tmp_path, monkeypatch):
    """归一后原值留在 `file_raw`；开关关掉则逐字回到改前。"""
    import config
    from nucleus.audit_problem_pool import entry_from_issue

    _abs = "D:\\xinrenlei\\tongtong-pulse-v9\\organs\\body\\PulseLiver.py"
    _iss = {"file": _abs, "method": "run", "type": "dead_code", "severity": "medium"}
    _e = entry_from_issue(_iss)
    assert _e["file"] == "organs/body/PulseLiver.py"
    assert _e["file_raw"] == _abs

    monkeypatch.setattr(config, "ENABLE_FILE_FIELD_NORMALIZE", False, raising=False)
    _e2 = entry_from_issue(_iss)
    assert _e2["file"] == _abs and "file_raw" not in _e2


# ==================== 刀6②：两套文件域清单 ====================
def test_m349_dump_two_domains(tmp_path):
    from nucleus.evolution.LogAnalyzer import LogAnalyzer

    _p = os.path.join(str(tmp_path), "two_domain_diff.json")
    _r = LogAnalyzer.dump_two_domains(out_path=_p, project_root=ROOT)
    assert _r["persisted"] is True
    with open(_p, "r", encoding="utf-8") as f:
        _d = json.load(f)
    assert _d["schema"] == "two_domain_diff/349.v1"
    assert isinstance(_d["only_tool"], list) and isinstance(_d["only_wl"], list)
    assert _d["declared"]["only_tool_count"] == len(_d["only_tool"])


# ==================== 刀8①：只读尾部 + 消费方 ====================
def test_m349_read_alert_tail_reads_tail_only(tmp_path):
    """造 2000 行告警流，只取尾部 50 行：**bytes_read 远小于文件**，且末条 ts 正确。"""
    from nucleus.world_model.external_heartbeat import read_alert_tail

    _d = os.path.join(str(tmp_path), "data", "reports")
    os.makedirs(_d, exist_ok=True)
    _p = os.path.join(_d, "alerts.jsonl")
    _last_ts = 1700000000.0
    with open(_p, "w", encoding="utf-8") as f:
        for _i in range(2000):
            f.write(json.dumps({"ts": _last_ts - (1999 - _i), "msg": "x" * 40}) + "\n")
    _size = os.path.getsize(_p)
    _r = read_alert_tail(root=str(tmp_path), max_lines=50)
    assert _r["available"] is True
    assert _r["tail_lines_kept"] == 50
    assert _r["bytes_read"] < _size
    assert abs(float(_r["last_ts"]) - _last_ts) < 1e-6


def test_m349_read_alert_tail_missing(tmp_path):
    from nucleus.world_model.external_heartbeat import read_alert_tail

    _r = read_alert_tail(root=str(tmp_path))
    assert _r["available"] is False and "不存在" in str(_r.get("reason"))


def test_m349_w1_consumers_all_landed():
    from nucleus.world_model.external_heartbeat import w1_consumers

    _rows = w1_consumers()["consumers"]
    assert len(_rows) == 3
    assert all(r.get("landed") is True for r in _rows)
    assert any(r.get("landed_at") == "349批刀8" for r in _rows)


# ==================== 刀11：子进程池 ====================
def test_m349_subproc_mutex_and_tmp_path():
    from nucleus.evolution.subproc_verify_pool import _is_tmp_path, evaluate_mutex

    assert _is_tmp_path("D:/proj/tmp/x/a.json") is True
    assert _is_tmp_path("D:/proj/data/reports/a.json") is False
    _m = evaluate_mutex(ROOT)
    assert _m["needs_mutex"] is False and _m["overlap"] == []


def test_m349_subproc_empty_tasks():
    from nucleus.evolution.subproc_verify_pool import run_verify_tasks

    _r = run_verify_tasks([])
    assert _r["results"] == [] and _r["stats"]["n"] == 0
