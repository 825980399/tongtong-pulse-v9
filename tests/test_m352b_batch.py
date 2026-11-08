# -*- coding: utf-8 -*-
"""第 352 批 · 刀9 追补合并（5 项）隔离单测。

覆盖：
  项1 硅基渠道模型修正（Qwen2-7B 下架 → zai-org/GLM-5.3-Flash）
  项2 环境变量合并（TTP_REMOTE_API_KEY 退役 → DEEPSEEK_API_KEY）
  项3 4 处旁路收编进渠道池（+ max_tokens 门 + 留痕可 grep）
  项4 新增魔搭渠道（priority 7）+ SSRF 白名单
  项5 每日 Tokens 统计 + 付费阈值告警

纪律：
  · 真值用 monkeypatch / 隔离目录注入，**不依赖现场环境变量与生产 data/**（铁律179）。
  · 判据按语义单元（剥行内注释后判"活跃读取"），不做整文件子串匹配（铁律162）。
  · 每条断言都写明"为什么这么判"——将来改坏了能立刻看懂该改哪。
"""
import io
import json
import os
import re
import shutil
import sys
import time

import pytest

os.environ.setdefault("PULSE_FRAMEWORK", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_ISO = os.path.join(_ROOT, "tmp", "p352_root", "iso_pytest_m352b")


def _src(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _code_only(line):
    """剥掉行内注释，只留代码部分。

    为什么必须剥：本批在 4 处旁路里写了大段解释性注释，注释里出现
    `REMOTE_API_CONFIG` 字样是**说明**不是**读取**。整行子串匹配会把
    注释判成违规（铁律162：判据按语义单元）。
    """
    return line.split("#", 1)[0]


@pytest.fixture()
def iso_dir():
    if os.path.isdir(_ISO):
        shutil.rmtree(_ISO)
    os.makedirs(_ISO, exist_ok=True)
    yield _ISO
    if os.path.isdir(_ISO):
        shutil.rmtree(_ISO)


# ══════════════════════ 项1：硅基渠道模型 ══════════════════════

def test_项1_硅基模型已改且旧名零残留():
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    assert "siliconflow-qwen2-7b" in ch, "渠道名应保留（改名会牵动测试与运维台账）"
    assert ch["siliconflow-qwen2-7b"]["model"] == "zai-org/GLM-5.3-Flash"
    # ★478：siliconflow 402 余额耗尽 ⇒ 降级 p99（原 p5 让给恢复启用的魔搭）
    assert ch["siliconflow-qwen2-7b"]["priority"] == 99
    # 旧模型名在 config.py 中零残留（注释也算 —— 留着注释会让人以为还在用）
    assert "Qwen/Qwen2-7B-Instruct" not in _src("config.py")


def test_项1_硅基其余字段不变():
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    sf = ch["siliconflow-qwen2-7b"]
    assert sf["api_url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert sf["quota_limit"] == -1
    assert sf["max_concurrent"] == 2
    assert "SILICONFLOW_API_KEY" in _src("config.py")


# ══════════════════════ 项2：环境变量合并 ══════════════════════

def test_项2_源码无TTP活跃读取():
    """config.py 里 TTP_REMOTE_API_KEY 只许出现在注释里，不许出现在 os.environ.get。"""
    txt = _src("config.py")
    for i, line in enumerate(txt.split("\n"), 1):
        if "TTP_REMOTE_API_KEY" not in line:
            continue
        assert line.lstrip().startswith("#"), (
            "config.py:%d 仍活跃读取 TTP_REMOTE_API_KEY: %r" % (i, line.strip()[:100]))
    assert 'os.environ.get("DEEPSEEK_API_KEY", "")' in txt


def test_项2_REMOTE_api_key跟随DEEPSEEK():
    """真实加载 config 断言（不 mock）：本机 DEEPSEEK_API_KEY 已设，应被读到。"""
    import config
    _env = os.environ.get("DEEPSEEK_API_KEY", "")
    if not _env:
        pytest.skip("本机未设 DEEPSEEK_API_KEY，无法断言真实取值")
    assert config.REMOTE_API_CONFIG["api_key"] == _env


# ══════════════════════ 项3：4 处旁路收编 ══════════════════════

_BYPASS = (
    ("organs/brain/pulse_inner_world_knowledge.py", "inner_world_refine"),
    ("organs/brain/PulseSpiritualCore.py", "spiritual_reflect"),
    ("organs/motor/PulseController.py", "controller_refine_search"),
    ("organs/senses/visual_engines/ocr_engine.py", "ocr"),
)


@pytest.mark.parametrize("rel,caller", _BYPASS)
def test_项3_旁路无活跃单端点读取(rel, caller):
    txt = _src(rel)
    bad = []
    for i, line in enumerate(txt.split("\n"), 1):
        if "REMOTE_API_CONFIG" in _code_only(line):
            bad.append((i, line.strip()[:90]))
    assert bad == [], "%s 仍活跃读取 REMOTE_API_CONFIG: %s" % (rel, bad)


def test_项3_视觉皮层调用方不再直读():
    txt = _src("organs/senses/PulseVisualCortex.py")
    bad = [(i, ln.strip()[:90]) for i, ln in enumerate(txt.split("\n"), 1)
           if "REMOTE_API_CONFIG" in _code_only(ln)]
    assert bad == [], "PulseVisualCortex 仍直读单端点: %s" % bad


@pytest.mark.parametrize("rel,caller", _BYPASS)
def test_项3_留痕可grep(rel, caller):
    txt = _src(rel)
    assert "[352追补2]" in txt, "%s 缺 [352追补] 留痕" % rel
    if caller != "ocr":
        # ocr 的 caller 名由函数上下文体现（_call_remote_ocr 只有一个入口），不单独打标签
        assert ("caller=" + caller) in txt, "%s 留痕缺 caller=%s" % (rel, caller)


def test_项3_统一走渠道池取数():
    """4 处都必须调用 get_llm_call_target* —— 否则"收编"只是删了旧代码。"""
    for rel, _ in _BYPASS:
        txt = _src(rel)
        assert "get_llm_call_target" in txt, "%s 未调用 get_llm_call_target*" % rel


@pytest.mark.parametrize("rel,expect", [
    ("organs/brain/pulse_inner_world_knowledge.py", 512),
    ("organs/brain/PulseSpiritualCore.py", 1024),
    ("organs/motor/PulseController.py", 512),
    ("organs/senses/visual_engines/ocr_engine.py", 1024),
])
def test_项3_max_tokens门(rel, expect):
    """首位渠道 glm-4.7-flash 是**推理模型**：小预算会被 reasoning_tokens 吃光 ⇒ 空回复。"""
    txt = _src(rel)
    vals = re.findall(r'"max_tokens":\s*(\d+)', txt)
    assert vals, "%s 未找到 max_tokens" % rel
    assert str(expect) in vals, "%s max_tokens 应为 %d，实际 %s" % (rel, expect, vals)


# ══════════════════════ 项4：魔搭渠道 ══════════════════════

def test_项4_魔搭渠道字段齐备():
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    assert "modelscope" in ch, "缺 modelscope 渠道"
    ms = ch["modelscope"]
    # ★426刀4（2026-11-02 活体实测）：模型下架 ⇒ 渠道降级保留
    #   （enabled=False + priority=99 垫底，详见 config.py 该渠道注释）。
    #   本用例随 426 决策同步钉死"降级后状态"，不再断言旧的有效态。
    # ★478（真机探针验证）：模型换 Qwen/Qwen3.8-Flash-Next 并恢复启用（p5）
    assert ms["priority"] == 5, "478：魔搭恢复启用，顶替 siliconflow 的 p5 槽位"
    assert ms["api_url"] == "https://api-inference.modelscope.cn/v1/chat/completions"
    assert ms["max_concurrent"] == 2
    assert ms["enabled"] is True, "478：探针 chat 200 ⇒ 恢复启用"
    assert ms["quota_type"] == "dynamic", "魔搭配额动态调整，不承诺固定额度"


def test_项4_关键从环境变量读且默认空():
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    # 不让宿主残留值影响判据：这里只断言"来源是环境变量"，默认空已在源码层
    src = _src("config.py")
    assert 'os.environ.get("MODELSCOPE_API_KEY", "")' in src
    assert "modelscope" in ch


def test_项4_池序与SSRF():
    import config
    pri = sorted((c["priority"], c["name"]) for c in
                 config.REMOTE_API_CHANNELS["default_channels"])
    # ★466 渠道扩容（百炼 2→7 条）+ 426刀4 魔搭降级（priority=99）后，
    #   池内优先级实际为 1..11（11 条启用） + 99（魔搭垫底），共 12 条。
    assert [p for p, _ in pri] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 99], pri
    # ★478：垫底哨兵换成余额耗尽的 siliconflow（魔搭已恢复启用）
    assert pri[-1][1] == "siliconflow-qwen2-7b", "p99 垫底哨兵应为 siliconflow"
    assert "api-inference.modelscope.cn" in tuple(config.SSRF_TRUSTED_EXTRA_HOSTS)


# ══════════════════════ 项5：每日 Tokens ══════════════════════

def _rows():
    _t = time.time()
    return [
        {"channel": "zhipu-47-flash", "model": "glm-4.7-flash", "tokens": 1000, "ts": _t},
        {"channel": "deepseek", "model": "deepseek-v4-flash", "tokens": 5000, "ts": _t},
        {"channel": "zhipu", "model": "glm-4-flash", "tokens": 200, "ts": _t},
    ]


def test_项5_混合聚合正确():
    from nucleus.llm.call_recorder import _m352b_accumulate, _m352b_empty_day
    d = _m352b_empty_day("2026-10-23")
    for r in _rows():
        d = _m352b_accumulate(d, r, "2026-10-23")
    assert d["calls"] == 3
    assert d["total_tokens"] == 6200
    assert d["paid_tokens"] == 5000, "只有 deepseek 算付费（PAID_CHANNELS 单一来源）"
    assert d["free_tokens"] == 1200
    assert d["paid_tokens"] + d["free_tokens"] == d["total_tokens"]
    assert set(d["by_channel"]) == {"zhipu-47-flash", "deepseek", "zhipu"}
    assert set(d["by_model"]) == {"glm-4.7-flash", "deepseek-v4-flash", "glm-4-flash"}
    assert d["by_channel"]["deepseek"]["tokens"] == 5000


def test_项5_纯函数不改入参():
    from nucleus.llm.call_recorder import _m352b_accumulate, _m352b_empty_day
    prev = _m352b_empty_day("2026-10-23")
    snapshot = json.dumps(prev, sort_keys=True)
    _m352b_accumulate(prev, _rows()[0], "2026-10-23")
    assert json.dumps(prev, sort_keys=True) == snapshot, "聚合函数污染了入参"


def test_项5_日期切换重置():
    from nucleus.llm.call_recorder import _m352b_accumulate, _m352b_empty_day
    d = _m352b_empty_day("2026-10-23")
    for r in _rows():
        d = _m352b_accumulate(d, r, "2026-10-23")
    d2 = _m352b_accumulate(d, _rows()[0], "2026-10-24")
    assert d2["date"] == "2026-10-24"
    assert d2["calls"] == 1 and d2["total_tokens"] == 1000
    assert d2["paid_tokens"] == 0, "跨日必须整体重置，昨日付费额度不残留"
    assert d2["alerted"] is False, "跨日告警标记要复位，否则次日永远不再告"


def test_项5_付费集为config单一来源(monkeypatch):
    import config
    from nucleus.llm.call_recorder import _m352b_accumulate, _m352b_empty_day
    monkeypatch.setattr(config, "PAID_CHANNELS", frozenset({"zhipu"}), raising=False)
    d = _m352b_accumulate(_m352b_empty_day("2026-10-23"), _rows()[2], "2026-10-23")
    assert d["paid_tokens"] == 200, "PAID_CHANNELS 改了，统计口径必须跟着改"
    assert d["free_tokens"] == 0


def test_项5_告警阈值两例(iso_dir, monkeypatch):
    import config
    from nucleus.llm import call_recorder as CR
    _fired = []
    monkeypatch.setattr(CR._module_logger, "warning",
                        lambda *a, **kw: _fired.append(str(a[0])))
    rec = CR.LLMCallRecorder(base_dir=iso_dir)

    # 例1：阈值高 → 不告警
    monkeypatch.setattr(config, "DAILY_PAID_TOKEN_ALERT", 10 ** 9, raising=False)
    rec.record(origin="system_internal", prompt="a", response="b",
               channel="deepseek", model="deepseek-v4-flash", tokens=5000)
    assert _fired == [], "未超阈值不应告警"

    # 例2：阈值低 → 告警一次；再记一条不重复告警
    shutil.rmtree(iso_dir, ignore_errors=True)
    os.makedirs(iso_dir, exist_ok=True)
    monkeypatch.setattr(config, "DAILY_PAID_TOKEN_ALERT", 100, raising=False)
    rec.record(origin="system_internal", prompt="a", response="b",
               channel="deepseek", model="deepseek-v4-flash", tokens=5000)
    assert len(_fired) == 1 and "[每日Tokens]" in _fired[0]
    rec.record(origin="system_internal", prompt="c", response="d",
               channel="deepseek", model="deepseek-v4-flash", tokens=600)
    assert len(_fired) == 1, "alerted 幂等：当日只告一次，否则每条调用刷一条"


def test_项5_落盘与查询(iso_dir, monkeypatch):
    import config
    from nucleus.llm import call_recorder as CR
    monkeypatch.setattr(config, "DAILY_PAID_TOKEN_ALERT", 10 ** 9, raising=False)
    rec = CR.LLMCallRecorder(base_dir=iso_dir)
    for r in _rows():
        rec.record(origin="system_internal", prompt="p", response="r",
                   channel=r["channel"], model=r["model"], tokens=r["tokens"])
    path = os.path.join(iso_dir, "daily_tokens.json")
    assert os.path.isfile(path), "daily_tokens.json 未落盘"
    on_disk = json.load(io.open(path, encoding="utf-8"))
    assert on_disk["calls"] == 3 and on_disk["total_tokens"] == 6200
    got = CR.get_daily_tokens(base_dir=iso_dir)
    assert got["source"] == "current"
    assert got["paid_tokens"] == 5000 and got["free_tokens"] == 1200
    # 既有 calls jsonl 一行没少
    calls = [f for f in os.listdir(iso_dir) if f.startswith("calls_")]
    assert calls, "既有 calls_*.jsonl 未写入（说明改坏了原落盘逻辑）"


def test_项5_四态source(iso_dir):
    from nucleus.llm import call_recorder as CR
    assert CR.get_daily_tokens(base_dir=os.path.join(iso_dir, "nope"))["source"] == "absent"

    bad = os.path.join(iso_dir, "bad")
    os.makedirs(bad, exist_ok=True)
    with io.open(os.path.join(bad, "daily_tokens.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert CR.get_daily_tokens(base_dir=bad)["source"] == "unreadable"

    stale = os.path.join(iso_dir, "stale")
    os.makedirs(stale, exist_ok=True)
    with io.open(os.path.join(stale, "daily_tokens.json"), "w", encoding="utf-8") as f:
        json.dump({"date": "2020-01-01", "calls": 9, "total_tokens": 99,
                   "by_channel": {}, "by_model": {}, "paid_tokens": 0,
                   "free_tokens": 0, "alerted": False}, f)
    _s = CR.get_daily_tokens(base_dir=stale)
    # 铁律166：'今天没数据' 与 '昨天的 9 次' 是两件事，不许抹平成 0
    assert _s["source"] == "stale_yesterday"
    assert _s["calls"] == 9
