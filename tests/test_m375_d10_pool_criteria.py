# -*- coding: utf-8 -*-
"""★375批刀10：渠道入池判据（TokenHub / 千帆 ERNIE-Speed）的**整条**判据。

## 票面与实测

票面：`TokenHub 入池 ⇔ zhipu 连续 2 日 calls 占比>70% 且（健康度<0.6 或 429≥2 次/日）`；
`千帆 ERNIE-Speed 入池 ⇔ siliconflow/modelscope 任一 429≥2 次/日`。

实测 354 批只落地了**一半**：

* `evaluate_pool_expansion()` 只判 `all(share > 0.70)`，`∧ (健康度<0.6 ∨ 429≥2/日)`
  那半截**没有代码** ⇒ 只要 zhipu 占比高就建议入池，哪怕它 100% 成功、零限流
  —— 那正是「不该换渠道」的情形（判据被削成了一半）。
* 千帆判据读 `by_channel[ch]["rate_limit"]`，但 `_m352b_accumulate` **从不写这个键**
  ⇒ 恒 0 ⇒ 千帆永远 `defer`（写了≠接通）。
* 占比算不出来时（两日无 calls / 一条都没走 zhipu）返回 0.0 ⇒ `defer`
  ⇒ 把「判不了」说成「不达标」（铁律166）。

## 本刀的判据

1. `_m375_is_rate_limit()`：限流判定用**显式标记表**（不随手在理由串里找关键字）；
2. `by_channel` 每个渠道补 `failed` / `rate_limit` 两个计数（`by_model` 不加 ——
   限流/失败是渠道属性，混进模型桶只会让两处口径打架）；
3. `evaluate_pool_expansion()`：三态 `join / defer / undecidable`，
   `join` 必须**两条判据同时成立**；字段未采集时记 `undecidable` 而不是 0。

★本件会改掉 354 的一条旧断言（`test_354_d4_pool_expansion_join_when_criteria_met`
把「只判占比」的缺陷固化成 `join`）—— 那是把 bug 当规格，本刀改成正判据。
"""
import ast
import os

import pytest

import nucleus.llm.call_recorder as CR

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CR_PATH = os.path.join(ROOT, "nucleus", "llm", "call_recorder.py")


def _rec(channel="zhipu", model="glm-4.7-flash", tokens=100,
         status=CR.STATUS_SUCCESS, error="", ts=1793000000.0):
    return {"channel": channel, "model": model, "tokens": tokens,
            "status": status, "error": error, "ts": ts}


def _day(channel, calls, failed=0, rate_limit=0, tokens=0, with_keys=True):
    _c = {"calls": calls, "tokens": tokens}
    if with_keys:
        _c["failed"] = failed
        _c["rate_limit"] = rate_limit
    return {"by_channel": {channel: _c}}


# ============================ ① 限流判定口
@pytest.mark.parametrize("_err,_want", [
    ("HTTP 429: Too Many Requests", True),
    ("429", True),
    ("rate limit exceeded", True),
    ("RateLimitError", True),
    ("server throttled us", True),
    ("请求过于频繁", True),
    ("HTTP 500: Internal Server Error", False),
    ("timeout after 30s", False),
    ("", False),
])
def test_01_rate_limit_markers(_err, _want):
    """限流判定逐标记比对 —— 500/timeout **不许**算成限流（那是两种病）。"""
    assert CR._m375_is_rate_limit({"status": CR.STATUS_FAILED, "error": _err}) is _want


def test_02_rate_limit_not_from_success_status():
    """成功调用即便 error 里带 429 字样，也不该被算成"发生了限流"以外的失败。"""
    _r = {"status": CR.STATUS_SUCCESS, "error": ""}
    assert CR._m375_is_rate_limit(_r) is False


# ============================ ② by_channel 的错误面计数
def test_03_accumulate_counts_failed_and_rate_limit():
    """三条调用（成功/失败/429）⇒ failed=1、rate_limit=1（**分开计**）。"""
    _out = CR._m352b_accumulate({}, _rec(), "2026-10-27")
    _out = CR._m352b_accumulate(_out, _rec(status=CR.STATUS_FAILED,
                                           error="HTTP 500"), "2026-10-27")
    _out = CR._m352b_accumulate(_out, _rec(status=CR.STATUS_FAILED,
                                           error="HTTP 429"), "2026-10-27")
    _c = _out["by_channel"]["zhipu"]
    assert _c["calls"] == 3
    assert _c["failed"] == 2, "失败计数只数了部分"
    assert _c["rate_limit"] == 1, "429 没有单独计（会和普通失败混成一堆）"


def test_04_by_model_has_no_error_dimensions():
    """`by_model` 不许带 `failed` / `rate_limit` —— 那是渠道属性。"""
    _out = CR._m352b_accumulate({}, _rec(status=CR.STATUS_FAILED,
                                         error="HTTP 429"), "2026-10-27")
    _m = _out["by_model"]["glm-4.7-flash"]
    assert "failed" not in _m and "rate_limit" not in _m, (
        f"模型桶混进了渠道口径：{sorted(_m)}")


def test_05_day_rollover_resets_error_counts():
    """跨日整体重置时，错误面也必须归零（否则昨天的 429 压着今天的判据）。"""
    _d1 = CR._m352b_accumulate({}, _rec(status=CR.STATUS_FAILED,
                                        error="HTTP 429"), "2026-10-26")
    _d2 = CR._m352b_accumulate(_d1, _rec(), "2026-10-27")
    _c = _d2["by_channel"]["zhipu"]
    assert _c["calls"] == 1
    assert _c.get("rate_limit") == 0 and _c.get("failed") == 0


def test_06_accumulate_is_pure():
    """纯函数：不许改入参（改了就成隐式共享状态，多线程下必炸）。"""
    _prev = {"date": "2026-10-27", "by_channel": {"zhipu": {"calls": 1, "tokens": 0}},
             "total_tokens": 0, "calls": 1}
    _snap = repr(_prev)
    CR._m352b_accumulate(_prev, _rec(), "2026-10-27")
    assert repr(_prev) == _snap, "`_m352b_accumulate` 改了入参"


# ============================ ③ TokenHub：两条判据必须同时成立
def test_07_no_join_when_zhipu_healthy():
    """★核心：占比 80%/85% 但 zhipu **完全健康、零限流** ⇒ **不换渠道**（defer）。

    354 旧断言在这里给的是 `join` —— 那等于「只要用得多就换」，把「依赖」
    和「依赖且不好用」混为一谈。
    """
    _rows = [_day("zhipu", 80), _day("zhipu", 85)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["verdicts"]["TokenHub"] == "defer", (
        f"判据仍只看占比：{_r['trace'][0]}")
    assert _r["error_surface"]["zhipu_health"] == 1.0


def test_08_join_when_unhealthy():
    """占比 >70%（连续 2 日）且健康度 <0.6 ⇒ join（依赖 + 不好用 = 该加渠道）。

    ★**前半截必须真的判**：第二日占比掉到 0.2 时不许 join（见 test_10）。
    """
    _rows = [{"by_channel": {"zhipu": {"calls": 80, "failed": 40, "rate_limit": 0},
                             "bailian-qwen-plus": {"calls": 20}}},
             {"by_channel": {"zhipu": {"calls": 85, "failed": 45, "rate_limit": 0},
                             "bailian-qwen-plus": {"calls": 15}}}]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["zhipu_health"] == pytest.approx(round(1 - 85 / 165.0, 4))
    assert _r["verdicts"]["TokenHub"] == "join", _r["trace"]


def test_09_join_when_rate_limited():
    """占比 >70% 且两日 429 合计 ≥2 ⇒ join（健康度可能仍然 1.0 —— 限流是容量信号）。"""
    _rows = [{"by_channel": {"zhipu": {"calls": 80, "failed": 0, "rate_limit": 1},
                             "bailian-qwen-plus": {"calls": 20}}},
             {"by_channel": {"zhipu": {"calls": 85, "failed": 0, "rate_limit": 1},
                             "bailian-qwen-plus": {"calls": 15}}}]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["zhipu_health"] == 1.0, "限流不该被算成 failed"
    assert _r["error_surface"]["zhipu_rate_limit"] == 2
    assert _r["verdicts"]["TokenHub"] == "join", _r["trace"]


def test_10_defer_when_share_below_threshold_even_if_unhealthy():
    """反向：健康度再差，占比没到 70% 也不该 join（前半截同样要判）。"""
    _rows = [{"by_channel": {"zhipu": {"calls": 30, "failed": 20, "rate_limit": 5},
                             "bailian-qwen-plus": {"calls": 70}}},
             {"by_channel": {"zhipu": {"calls": 25, "failed": 15, "rate_limit": 5},
                             "bailian-qwen-plus": {"calls": 75}}}]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["verdicts"]["TokenHub"] == "defer"


# ============================ ④ 不可判 ≠ 不达标
def test_11_undecidable_when_no_calls_at_all():
    """两日一条调用都没有 ⇒ TokenHub **undecidable**（原来算成 0.0 ⇒ defer）。

    千帆这一档不同：它只需要「限流次数」，零调用 ⇒ 限流必然 0 ⇒ **事实**是
    `defer`。两个判据要的量不同，结论可以不一样 —— 但都**不是**"不达标"。
    """
    _r = CR.evaluate_pool_expansion([{"by_channel": {}}, {"by_channel": {}}])
    assert _r["verdicts"]["TokenHub"] == "undecidable", "占比无定义时不许给 defer"
    assert _r["error_surface"]["qianfan_state"] == "no_calls"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "defer"


def test_12_undecidable_when_zhipu_never_called():
    """有调用但一条都没走 zhipu ⇒ 占比无意义 ⇒ undecidable。"""
    _rows = [_day("bailian-qwen-plus", 10), _day("bailian-qwen-plus", 12)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["verdicts"]["TokenHub"] == "undecidable"


def test_13_insufficient_days_still_undecidable():
    """<2 日 ⇒ 不可判（354 已有行为，本刀不许回退）。"""
    _r = CR.evaluate_pool_expansion([_day("zhipu", 9)])
    assert _r["verdicts"]["TokenHub"] == "undecidable"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "undecidable"


# ============================ ⑤ 千帆：字段在不在决定"真 0"与"没数据"
def test_14_qianfan_undecidable_when_stale_schema():
    """渠道被调用过、但汇总里没有 `rate_limit` 键（旧格式）⇒ **undecidable**。

    「真 0」与「没采」长得一样，不许读成一个（铁律166）。
    """
    _rows = [_day("siliconflow-qwen2-7b", 10, with_keys=False),
             _day("zc_other", 5, with_keys=False)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["qianfan_state"] == "stale_schema"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "undecidable", _r["trace"]


def test_14b_qianfan_defer_when_channel_never_called():
    """两个渠道**一次都没出现** ⇒ 零调用 ⇒ 限流必然 0，这是**事实**不是缺数据。

    若把它也判成 undecidable，从没配 key 的渠道就永远不可判 —— 判据等于白设。
    """
    _rows = [_day("zhipu", 80), _day("zhipu", 85)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["qianfan_state"] == "no_calls"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "defer"


def test_15_qianfan_defer_when_field_present_and_zero():
    """有键且为 0 ⇒ 真的零限流 ⇒ defer（key 已设不浪费）。"""
    _rows = [_day("siliconflow-qwen2-7b", 10), _day("modelscope", 5)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["qianfan_state"] == "ok"
    assert _r["verdicts"]["qianfan-ernie-speed"] == "defer"


def test_16_qianfan_join_on_two_429_across_days():
    """任一渠道 429 合计 ≥2 ⇒ join（跨两日累加）。"""
    _rows = [_day("siliconflow-qwen2-7b", 10, rate_limit=1),
             _day("modelscope", 5, rate_limit=1)]
    _r = CR.evaluate_pool_expansion(_rows)
    assert _r["error_surface"]["qianfan_rate_limit"] == 2
    assert _r["verdicts"]["qianfan-ernie-speed"] == "join"


# ============================ ⑥ 判据文案与代码同源（防"文案写了代码没写"）
def test_17_criteria_text_has_both_halves_in_code():
    """`POOL_EXPANSION_CRITERIA` 文案里的健康度/429 两半，代码里必须都有。

    354 的病根就是「文案写了 ∧ 健康度/429，代码只写了占比」。这条判据把
    「文案 → 代码」的对应关系钉住：文案里出现的判据标识必须能在函数体里找到。
    """
    with open(CR_PATH, encoding="utf-8") as _f:
        _src = _f.read()
    _tree = ast.parse(_src)
    _fn = None
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.FunctionDef) and _n.name == "evaluate_pool_expansion":
            _fn = _n
            break
    assert _fn is not None, "找不到 evaluate_pool_expansion"
    _body = ast.get_source_segment(_src, _fn) or ""
    assert "_z_health" in _body, "健康度判据没进代码（文案里有）"
    assert "_z_429" in _body or "rate_limit" in _body, "429 判据没进代码"
    assert "_cond2" in _body, "∧ 后半截没有合成条件（缺了就等于只判前半截）"
    _txt = CR.POOL_EXPANSION_CRITERIA["TokenHub"]
    assert "健康度" in _txt and "429" in _txt, "文案本身被改弱了"


def test_18_error_surface_is_reported():
    """错误面读数必须随结论一起出（只有结论没有分母，没法复核）。"""
    _r = CR.evaluate_pool_expansion([_day("zhipu", 80), _day("zhipu", 85)])
    _es = _r.get("error_surface") or {}
    for _k in ("zhipu_calls", "zhipu_failed", "zhipu_rate_limit",
               "zhipu_health", "qianfan_rate_limit", "qianfan_state"):
        assert _k in _es, f"error_surface 缺 {_k}"
    assert _es["zhipu_calls"] == 165


# ============================ ⑦ 渠道族：「zhipu」不是一个 key，是一族
def test_19_family_aggregation_sums_zhipu_members():
    """票面说的「zhipu」是**渠道族**：`zhipu` + `zhipu-47-flash` 要合计占比。

    现网 2026-10-27 实测 `by_channel` = `zhipu-47-flash 33 / bailian-qwen-plus 43 /
    unknown 4` —— **`zhipu` 一条都没有**。只认 `"zhipu"` 这个键，判据对现网
    永远不可判（写了但判不了）。
    """
    _rows = [
        {"by_channel": {"zhipu": {"calls": 10},
                        "zhipu-47-flash": {"calls": 70, "failed": 40},
                        "bailian-qwen-plus": {"calls": 20}}},
        {"by_channel": {"zhipu": {"calls": 10},
                        "zhipu-47-flash": {"calls": 70, "failed": 40},
                        "bailian-qwen-plus": {"calls": 20}}},
    ]
    _r = CR.evaluate_pool_expansion(_rows)
    _es = _r["error_surface"]
    assert _es["zhipu_calls"] == 160, "族内两个渠道没有合计"
    assert _es["zhipu_members"] == ["zhipu", "zhipu-47-flash"]
    assert _es["zhipu_health"] == pytest.approx(1 - 80 / 160.0)
    # 两日占比都是 80/100 = 0.80 > 0.70，健康度 0.5 < 0.6 ⇒ join
    assert _r["verdicts"]["TokenHub"] == "join", _r["trace"]


def test_20_unlisted_same_prefix_still_counted_but_flagged():
    """族表外的同前缀渠道（`zhipu-new-x`）**仍然计入**，但必须单列。

    漏算 = 判据静默失效（新渠道上线后占比突然"变小"、结论悄悄变 defer）；
    但只在代码里兜住而不报出来，就没人知道族表该更新了。
    """
    _rows = [
        {"by_channel": {"zhipu-new-x": {"calls": 90, "failed": 50},
                        "bailian-qwen-plus": {"calls": 10}}},
        {"by_channel": {"zhipu-new-x": {"calls": 90, "failed": 50},
                        "bailian-qwen-plus": {"calls": 10}}},
    ]
    _r = CR.evaluate_pool_expansion(_rows)
    _es = _r["error_surface"]
    assert _es["zhipu_calls"] == 180, "族表过期时不许漏算"
    assert _es["zhipu_unknown_members"] == ["zhipu-new-x"], "族表过期必须可见"
    assert _r["verdicts"]["TokenHub"] == "join"


def test_21_live_channel_shape_is_decidable_not_undecidable():
    """用**现网真实形态**判：zhipu-47-flash 33 / bailian-qwen-plus 43 / unknown 4。

    占比 33/80 = 0.4125 < 0.70 ⇒ `defer`（**不是** undecidable）。
    族聚合之前这里是「判不了」，现在「判得了且结论是不换」—— 两者差别很大。
    """
    _row = {"by_channel": {"zhipu-47-flash": {"calls": 33},
                           "bailian-qwen-plus": {"calls": 43},
                           "unknown": {"calls": 4}}}
    _r = CR.evaluate_pool_expansion([_row, _row])
    assert _r["verdicts"]["TokenHub"] == "defer", _r["trace"]
    assert _r["error_surface"]["zhipu_calls"] == 66
    assert _r["error_surface"]["zhipu_members"] == ["zhipu-47-flash"]
    assert _r["error_surface"]["zhipu_unknown_members"] == []


def test_22_family_table_covers_all_configured_channels():
    """族表必须覆盖 config 里**实际配置**的 zhipu 族渠道（防族表落后于配置）。"""
    _r = CR.evaluate_pool_expansion([_day("zhipu", 10), _day("zhipu", 10)])
    assert _r["error_surface"]["zhipu_unknown_members"] == [], (
        "族表漏了已配置的 zhipu 族渠道："
        f"{_r['error_surface']['zhipu_unknown_members']}")
    assert "zhipu-47-flash" in CR.CHANNEL_FAMILIES["zhipu"], (
        "现网主力渠道不在族表里（2026-10-27 实测它是 zhipu 族唯一在跑的）")
