# -*- coding: utf-8 -*-
"""★375批刀8：进化费用额度的**额度在位 + 跨期重置**判据。

## 任务书前提勘误

任务书写「`EVOLUTION_BUDGET_DAILY_CNY`/`WEEKLY_CNY` = None ⇒ 不熔断，失控烧钱风险；
补默认额度阈值」。实测：**额度早在 340B 刀7（`278137c5`，2026-10-22）就已落位**
（`config.py` 日 50.0 元 / 周 200.0 元），`evolution_budget.budget_state()` 已经是
「金额 + 调用次数」双口径、三态（ok / tripped / 不可判）判定，且**跨日跨周按 key 重置**。

所以刀8 不该再「补一遍阈值」（那是重复配置，且任务书明令**不新增 config 开关**），
该做的是把这条事实**钉住不许回退**：额度一旦被改回 None，熔断就静默失效，
而失效是无声的（照常烧钱、日志照常滚）。本件就是那把尺子。

## 四条判据

1. 额度**在位**：日/周都非 None 且 > 0（回退成 None 就红）；
2. **跨期重置**：侧账 `day.key`/`week.key` 与当期不符 ⇒ `used` 归零
   （不重置的话，昨天的花费会一直压着今天，或者反过来——旧账被当新账读）；
3. **超限必熔断**：`used >= limit` ⇒ `tripped=True`、`state="tripped"`、`basis` 含 `paid`；
4. **未配置不代定**：额度为 None 时**不熔断**且留 `note` 说清楚
   （模块不替人裁阈值 —— 但也不能把 None 当成 0 悄悄放行）。

另：`tripped_all is None` = 「调用次数口径未配置额度 ⇒ 不可判」，
绝不能填 False（False 会被读成「判过了、没超」，铁律166）。
"""
import json
import os
import time

import pytest

from nucleus.evolution import evolution_budget as EB

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _ledger(tmp_path, day_key=None, day_used=0.0, day_calls=0,
            week_key=None, week_used=0.0, week_calls=0):
    """造一份侧账（结构照 `load_budget` 的 _empty，不新造字段）。"""
    _now = time.time()
    _doc = {
        "version": "335.1", "updated_at": _now,
        "day": {"key": day_key if day_key is not None else EB._m335_day_key(_now),
                "used": day_used, "calls": day_calls},
        "week": {"key": week_key if week_key is not None else EB._m335_week_key(_now),
                 "used": week_used, "calls": week_calls},
    }
    _p = tmp_path / "evolution_budget.json"
    _p.write_text(json.dumps(_doc), encoding="utf-8")
    return str(_p)


# ============================== ① 额度在位（防回退成 None）
def test_01_daily_and_weekly_limits_are_configured():
    """日/周额度必须都被配置且为正 —— 回退成 None 就是熔断静默失效。"""
    import config as C

    _day = getattr(C, "EVOLUTION_BUDGET_DAILY_CNY", None)
    _week = getattr(C, "EVOLUTION_BUDGET_WEEKLY_CNY", None)
    assert _day is not None, "日额度被改回 None ⇒ 熔断不再触发（340B 刀7 已配 50.0）"
    assert _week is not None, "周额度被改回 None ⇒ 熔断不再触发（340B 刀7 已配 200.0）"
    assert float(_day) > 0 and float(_week) > 0, f"额度必须为正：day={_day} week={_week}"
    assert float(_week) >= float(_day), "周额度不该小于日额度（否则日档永远先撞线，周档形同虚设）"


def test_02_limits_are_read_back_by_module():
    import config as C

    _lim = EB._m335_limits()
    assert _lim["day"] == float(C.EVOLUTION_BUDGET_DAILY_CNY)
    assert _lim["week"] == float(C.EVOLUTION_BUDGET_WEEKLY_CNY)


def test_03_configured_flag_is_true():
    _st = EB.budget_state(path=os.path.join(ROOT, "data", "evolution", "_nonexistent_375c8.json"))
    assert _st["configured"] is True, "已配额度却报 configured=False ⇒ 读配置失败"


# ============================== ② 跨期重置
def test_04_stale_day_key_resets_used(tmp_path):
    """昨天的 used 不许压到今天：day.key 不符 ⇒ 归零。"""
    _p = _ledger(tmp_path, day_key="19990101", day_used=999.0)
    _st = EB.budget_state(path=_p)
    assert _st["day_used"] == 0.0, f"跨日未重置：day_used={_st['day_used']}"
    assert _st["tripped"] is False, "旧账被当新账读 ⇒ 误熔断"


def test_05_stale_week_key_resets_used(tmp_path):
    _p = _ledger(tmp_path, week_key="1999-W01", week_used=9999.0)
    _st = EB.budget_state(path=_p)
    assert _st["week_used"] == 0.0
    assert _st["tripped"] is False


def test_06_current_key_keeps_used(tmp_path):
    """反向：当期 key 必须**保留** used（否则永远归零 ⇒ 永远不熔断）。"""
    _p = _ledger(tmp_path, day_used=49.0)
    _st = EB.budget_state(path=_p)
    assert _st["day_used"] == pytest.approx(49.0)


# ============================== ③ 超限必熔断
def test_07_over_daily_limit_trips(tmp_path):
    import config as C

    _p = _ledger(tmp_path, day_used=float(C.EVOLUTION_BUDGET_DAILY_CNY) + 0.01)
    _st = EB.budget_state(path=_p)
    assert _st["tripped"] is True
    assert _st["state"] == "tripped"
    assert "paid" in _st["tripped_basis"]
    assert "日预算超限" in _st["reason"]


def test_08_at_limit_trips(tmp_path):
    """边界取 `>=`：正好等于额度的**那一笔之后**就该停手，不是再多花一笔。"""
    import config as C

    _p = _ledger(tmp_path, day_used=float(C.EVOLUTION_BUDGET_DAILY_CNY))
    assert EB.budget_state(path=_p)["tripped"] is True


def test_09_over_weekly_limit_trips(tmp_path):
    import config as C

    _p = _ledger(tmp_path, week_used=float(C.EVOLUTION_BUDGET_WEEKLY_CNY) + 0.01)
    _st = EB.budget_state(path=_p)
    assert _st["tripped"] is True
    assert "周预算超限" in _st["reason"]


def test_10_is_tripped_agrees_with_state(tmp_path):
    import config as C

    _p = _ledger(tmp_path, day_used=float(C.EVOLUTION_BUDGET_DAILY_CNY) + 1)
    assert EB.is_tripped(path=_p) is True
    assert EB.is_tripped(path=_p) == bool(EB.budget_state(path=_p)["tripped"])


# ============================== ④ 未配置不代定 / 不可判不填 False
def test_11_none_limit_never_trips(monkeypatch, tmp_path):
    """额度为 None 时不熔断，但必须留 note 说清楚（不是静默放行）。"""
    import config as C

    monkeypatch.setattr(C, "EVOLUTION_BUDGET_DAILY_CNY", None, raising=False)
    monkeypatch.setattr(C, "EVOLUTION_BUDGET_WEEKLY_CNY", None, raising=False)
    _p = _ledger(tmp_path, day_used=99999.0, week_used=99999.0)
    _st = EB.budget_state(path=_p)
    assert _st["tripped"] is False, "未配置额度却熔断 ⇒ 自己替人裁了阈值"
    assert _st["configured"] is False
    # 注：`note` 是单值字段，两条说明会互相覆盖（额度未配置时"不代定"
    #    那句会被量口径的"不可判"盖掉）—— 所以只断言"说清了未配置"，
    #    不锁具体措辞。该覆盖问题记在证据件里，不在本刀改生产。
    assert "未配置" in _st["note"]


def test_12_calls_dimension_is_undecidable_not_false():
    """调用次数口径未配置 ⇒ `tripped_all is None`（不可判），**不许填 False**。

    False 会被读成「判过了、没超」—— 而实际是「没设额度，判不了」（铁律166）。
    """
    _st = EB.budget_state(path=os.path.join(ROOT, "data", "evolution", "_nonexistent_375c8.json"))
    _lim = EB._m335_limits()
    if _lim["day_calls"] is None and _lim["week_calls"] is None:
        assert _st["tripped_all"] is None, "未配置量口径却给了布尔结论"
        assert "不可判" in _st["note"]


def test_13_missing_ledger_is_ok_not_tripped(tmp_path):
    """侧账不存在 ⇒ 空账起步，记 ok（不误熔断、也不冒充有账）。"""
    _st = EB.budget_state(path=str(tmp_path / "nope.json"))
    assert _st["day_used"] == 0.0 and _st["week_used"] == 0.0
    assert _st["tripped"] is False


def test_14_state_vocabulary_is_closed(tmp_path):
    _allowed = {"ok", "tripped"}
    _p = _ledger(tmp_path, day_used=0.0)
    assert EB.budget_state(path=_p)["state"] in _allowed


# ============================== ⑤ 生产侧账只读复核
def test_15_live_ledger_readable_and_keys_wellformed():
    """真读生产侧账（只读）：结构完整、key 形如 YYYYMMDD / YYYY-Www。

    ★不锁具体数值 —— 那是活体随时在变的事实，锁了必然假红（铁律 A2）。
    """
    _st = EB.budget_state()
    assert isinstance(_st["day_key"], str) and len(_st["day_key"]) == 8
    assert _st["day_key"].isdigit()
    assert "-W" in str(_st["week_key"])
    assert isinstance(_st["day_used"], float) and _st["day_used"] >= 0
    assert isinstance(_st["week_used"], float) and _st["week_used"] >= 0
    assert "cost_coverage" in _st, "覆盖率缺口必须随状态一起出（否则没熔断会被读成没花钱）"
