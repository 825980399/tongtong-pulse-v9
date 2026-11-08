# -*- coding: utf-8 -*-
"""352 批隔离单测（刀1~刀9）。

纪律：
  · 铁律179：不依赖现场 data/，所有真值在 tmp_path 现造；
  · 铁律168/193：判据必须带**已知正例**（能过）与**已知负例**（该拒），
    只验一侧等于没验；
  · 铁律205：状态机流转只走合法路径。
"""

import os
import sys
import json
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import patch_generator as pg            # noqa: E402
from nucleus.evolution import chain_metrics as cm             # noqa: E402
from nucleus.mnemosyne import ast_release as ar               # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager       # noqa: E402

SAMPLE_BODY = '''class Organ:
    def _m(self):
        try:
            return 1
        except ValueError as ve:
            pass
'''


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _mk_draft(file, method="m", issue="silent_exception",
              orig="def m(self):\n    return 1\n", mod=None, **kw):
    d = {"file": file, "method": method, "issue_type": issue,
         "original_code": orig,
         "modified_code": mod if mod is not None else orig + "\n    # x",
         "reason": "触发行：a。现象：b。依据：c。",
         "dedup_key": pg.make_dedup_key(file, method, issue)}
    d.update(kw)
    return d


# ═════════════ 刀1 生成前目标存在性预检 ═════════════

def test_刀1_预检四判词(tmp_path):
    _write(tmp_path, "a.py", "def f():\n    pass\n\n\nclass C:\n    def g(self):\n        pass\n")
    assert pg.precheck_target(str(tmp_path), "a.py", "f")["verdict"] == pg.TARGET_IN_PLACE
    assert pg.precheck_target(str(tmp_path), "a.py", "g")["verdict"] == pg.TARGET_IN_PLACE
    assert pg.precheck_target(str(tmp_path), "a.py", "zz")["verdict"] == pg.TARGET_METHOD_ABSENT
    assert pg.precheck_target(str(tmp_path), "nope.py", "f")["verdict"] == pg.TARGET_FILE_ABSENT


def test_刀1_同名多类标ambiguous但仍算在位(tmp_path):
    _write(tmp_path, "b.py",
           "class A:\n    def dup(self):\n        pass\n\n\n"
           "class B:\n    def dup(self):\n        pass\n")
    r = pg.precheck_target(str(tmp_path), "b.py", "dup")
    assert r["verdict"] == pg.TARGET_IN_PLACE
    assert r["ambiguous"] is True
    assert sorted(r["cls"]) == ["A", "B"]


def test_刀1_解析失败不冒充漂移(tmp_path):
    _write(tmp_path, "broken.py", "def f(:\n    pass\n")
    r = pg.precheck_target(str(tmp_path), "broken.py", "f")
    assert r["verdict"] == pg.TARGET_PARSE_FAILED
    assert r["verdict"] not in pg.TARGET_DRIFTED_VERDICTS


def test_刀1_漂移不进生成_传输零调用(tmp_path):
    _write(tmp_path, "c.py", "def f():\n    pass\n")
    calls = []

    def _tp(prompt, **kw):
        calls.append(1)
        return {"ok": False, "err": "stop", "text": ""}
    entry = {"file": "c.py", "method": "gone_method", "issue_type": "silent_exception",
             "severity": "low", "klass": "k", "desc": "d"}
    r = pg.generate_patch_draft(entry, str(tmp_path), transport=_tp, now=1.0)
    assert r["state"] == pg.STATE_TARGET_DRIFTED
    assert calls == []
    # 改前它会落到 generation_failed（同一件事两个名字）——本刀把它分开
    assert r["would_be_state_before_352"] == pg.STATE_GENERATION_FAILED


def test_刀1_判死池排除漂移候选(tmp_path):
    """漂移候选不进判死池：分母里不该有它。"""
    from nucleus.evolution import zero_break_judge as zbj
    _write(tmp_path, "d.py", "def alive():\n    return 1\n")
    # ★存量红修复（2026-11-06 路灯）：354刀6 把「无 m323_truth.method_before 且无
    #   original_code」的条目当 O3 观察位**早退**（excluded_o3），压根到不了漂移判定
    #   ⇒ 本条原用两个裸条目，实测被跳过 2 条、filt["excluded"] 恒 0。
    #   A/B 已证：退回 HEAD 版 config.py 仍红 ⇒ 与 466 渠道扩容、469 四刀均无关。
    #   修法 = 补齐真实补丁必带的 original_code（不是放宽判据）：
    #     k1 片段与现场逐字一致 ⇒ in_place；k2 片段现场不存在 ⇒ drifted
    #   ⇒ 原判据「漂移候选不进分母」照旧成立。回滚点：删掉两处 original_code 即回旧形态。
    _alive = "def alive():\n    return 1\n"
    entries = [
        {"dedup_key": "k1", "file": "d.py", "method": "alive",
         "issue_type": "dead_code", "evidence_tier": "hard_loose",
         "original_code": _alive},
        {"dedup_key": "k2", "file": "d.py", "method": "vanished",
         "issue_type": "dead_code", "evidence_tier": "hard_loose",
         "original_code": "def vanished():\n    return 2\n"},
    ]
    filt = {}
    rows = zbj.collect_death_samples(str(tmp_path), entries, universe=10, limit=10,
                                     drift_filter=filt)
    assert filt["applied"] is True
    assert filt["excluded"] == 1
    assert "k2" not in [x["id"] for x in rows]
    assert "k1" in [x["id"] for x in rows]


# ═════════════ 刀2 pending 面 dedup_key ═════════════

def test_刀2_四情形():
    ensure = PatchManager._m352_ensure_dedup_key
    good = pg.make_dedup_key("a/b.py", "m", "dead_code")
    c_ok = {"file": "a/b.py", "method": "m", "issue_type": "dead_code",
            "dedup_key": good}
    assert ensure(c_ok)["action"] == "ok"

    c_fill = {"file": "a/b.py", "method": "m2", "issue_type": "dead_code"}
    r = ensure(c_fill)
    assert r["action"] == "filled" and c_fill["dedup_key"] == r["expect"]

    c_bad = {"file": "a/b.py", "method": "m3", "issue_type": "dead_code",
             "dedup_key": "0123456789abcdef"}
    r = ensure(c_bad)
    assert r["action"] == "recomputed"
    assert c_bad["dedup_key"] == r["expect"]          # 以可复算值为准
    assert c_bad["dedup_key_raw"] == "0123456789abcdef"  # 原值留痕

    assert ensure({"file": "a/b.py", "method": ""})["ok"] is False
    assert ensure({"file": "", "method": "m"})["ok"] is False


def test_刀2_口径与池同源():
    """pending 侧与 audit_problem_pool 侧必须是同一个算法（不靠口头承诺）。"""
    assert pg.dedup_key_matches_pool("nucleus/a.py", "f", "dead_code") is True


def test_刀2_入池口真的补键(tmp_path):
    """真入口：隔离根走完整 save_pending_patch，落盘条目必须带自洽 dedup_key。"""
    (tmp_path / "organs" / "motor").mkdir(parents=True)
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "organs", "motor", "PulseLegs.py")
    import shutil
    shutil.copyfile(src, str(tmp_path / "organs" / "motor" / "PulseLegs.py"))
    pm = PatchManager(str(tmp_path))
    rec = {"id": "t352", "file": "organs/motor/PulseLegs.py",
           "method": "set_llm_callback", "issue_type": "silent_exception",
           "original_code": "def set_llm_callback(self, cb):\n    self._cb = cb\n",
           "modified_code": "def set_llm_callback(self, cb):\n    self._cb = cb\n    # t\n",
           "reason": "触发行：a。现象：b。依据：c。", "risk_level": "低",
           "source": "local_rule"}
    pm.save_pending_patch(rec)
    ip = tmp_path / "data" / "patches" / "pending_patches.json"
    rows = json.loads(ip.read_text(encoding="utf-8")) if ip.is_file() else []
    hit = [x for x in rows if x.get("id") == "t352"]
    if hit:
        assert hit[0]["dedup_key"] == pg.make_dedup_key(
            "organs/motor/PulseLegs.py", "set_llm_callback", "silent_exception")


# ═════════════ 刀3 分层生成路由 ═════════════

def test_刀3_三档判定(tmp_path):
    _write(tmp_path, "s.py", SAMPLE_BODY)
    simple = pg.route_generation_lane(
        {"file": "s.py", "method": "_m", "issue_type": "silent_exception"},
        str(tmp_path))
    assert simple["lane"] == pg.LANE_TEMPLATE

    mid = pg.route_generation_lane(
        {"file": "s.py", "method": "_m", "issue_type": "non_atomic_write"},
        str(tmp_path))
    assert mid["lane"] == pg.LANE_LOCAL_MODEL

    hard = pg.route_generation_lane(
        {"file": "s.py", "method": "_m", "issue_type": "cross_module_singleton_call"},
        str(tmp_path))
    assert hard["lane"] == pg.LANE_MANUAL
    assert hard["score"] > 0 and hard["reasons"]


def test_刀3_核心文件必转人工():
    r = pg.route_generation_lane(
        {"file": "nucleus/reasoning/PatchManager.py", "method": "save_pending_patch",
         "issue_type": "silent_exception"}, None)
    assert r["lane"] == pg.LANE_MANUAL


def test_刀3_模板档零模型产出(tmp_path):
    _write(tmp_path, "t.py",
           "from nucleus._silent_except import silent_exc\n\n\n" + SAMPLE_BODY)
    calls = []

    def _tp(prompt, **kw):
        calls.append(1)
        return {"ok": False, "err": "stop", "text": ""}
    entry = {"file": "t.py", "method": "_m", "issue_type": "silent_exception",
             "severity": "low", "klass": "k", "desc": "d"}
    r = pg.generate_patch_draft(entry, str(tmp_path), transport=_tp, now=1.0)
    assert r["lane"]["lane"] == pg.LANE_TEMPLATE
    assert calls == []                       # 零模型
    assert r["template"]["ok"] is True
    assert r["state"] in (pg.STATE_DRAFT_READY, pg.STATE_GATE_REJECTED)


def test_刀3_模板不适用时降级并留痕(tmp_path):
    """文件没导入 silent_exc ⇒ 模板拒，降级给模型，且**必须留痕**（不静默）。"""
    _write(tmp_path, "u.py", SAMPLE_BODY)
    calls = []

    def _tp(prompt, **kw):
        calls.append(1)
        return {"ok": False, "err": "stop", "text": ""}
    entry = {"file": "u.py", "method": "_m", "issue_type": "silent_exception",
             "severity": "low", "klass": "k", "desc": "d"}
    r = pg.generate_patch_draft(entry, str(tmp_path), transport=_tp, now=1.0)
    assert r["lane_effective"] == pg.LANE_LOCAL_MODEL
    assert r["lane_downgraded_from"] == pg.LANE_TEMPLATE
    assert "no_silent_exc_import" in r["lane_downgrade_why"]
    assert len(calls) == 1


# ═════════════ 刀4 审批链分层 ═════════════

@pytest.mark.parametrize("kw,lane,expect", [
    ({"file": "organs/motor/PulseLegs.py"}, pg.LANE_TEMPLATE, pg.TIER_LOW),
    ({"file": "nucleus/reasoning/PatchManager.py"}, pg.LANE_LOCAL_MODEL, pg.TIER_HIGH),
    ({"file": "organs/motor/PulseLegs.py", "o3_dead_code": True},
     pg.LANE_TEMPLATE, pg.TIER_HIGH),
    ({"file": "organs/motor/PulseLegs.py",
      "issue_type": "cross_module_singleton_call"}, pg.LANE_TEMPLATE, pg.TIER_HIGH),
    ({"file": "a/b.py,organs/x/y.py"}, pg.LANE_TEMPLATE, pg.TIER_HIGH),
])
def test_刀4_分层评分(kw, lane, expect):
    d = _mk_draft(**kw)
    r = pg.score_approval_tier(d, None, lane=lane)
    assert r["tier"] == expect
    assert r["requires_confirmation"] == (expect == pg.TIER_HIGH)


def test_刀4_免审不等于免征():
    """不变量：auto_apply_eligible 恒 False —— 免人工签字 ≠ 允许自动改源码。"""
    d = _mk_draft("organs/motor/PulseLegs.py")
    r = pg.score_approval_tier(d, None, lane=pg.LANE_TEMPLATE)
    assert r["tier"] == pg.TIER_LOW
    assert r["auto_apply_eligible"] is False


# ═════════════ 刀5 质量回灌 ═════════════

def test_刀5_测试件被过滤(tmp_path):
    (tmp_path / "effect_verify.json").write_text(json.dumps({"verdicts": {
        "t1": {"patch_id": "t1", "file": "a.py", "passed": True, "verified_at": 3},
        "t2": {"patch_id": "t2", "file": "tmp/x/y.py", "passed": True, "verified_at": 2},
        "r1": {"patch_id": "r1", "file": "organs/a/b.py", "issue_type": "x",
               "passed": True, "reason": "ok", "verified_at": 1},
    }}, ensure_ascii=False), encoding="utf-8")
    r = pg.load_feedback_examples(str(tmp_path), n=3)
    assert r["total"] == 3 and r["real"] == 1 and r["used"] == 1
    assert r["examples"][0]["patch_id"] == "r1"


def test_刀5_空样本不产生空标题():
    assert pg.render_feedback_block([]) == ""


def test_刀5_prompt确定性():
    src = {"ok": True, "code": "def m():\n    pass\n", "start_line": 1,
           "end_line": 2, "why": "def_found"}
    ex = [{"patch_id": "r1", "file": "organs/a/b.py", "issue_type": "x",
           "passed": True, "reason": "ok"}]
    a = pg.build_prompt({"file": "f.py", "method": "m", "issue_type": "x",
                         "desc": "d"}, src, ex)
    b = pg.build_prompt({"file": "f.py", "method": "m", "issue_type": "x",
                         "desc": "d"}, src, ex)
    assert a == b


# ═════════════ 刀6 提示模板 ═════════════

def test_刀6_版本与模板一致():
    src = {"ok": True, "code": "def m():\n    pass\n", "start_line": 1,
           "end_line": 2, "why": "def_found"}
    p = pg.build_prompt({"file": "f.py", "method": "m", "issue_type": "x",
                         "desc": "d"}, src, [])
    assert pg.PROMPT_TEMPLATE_VERSION in p
    assert pg.GENERATOR_VERSION == pg.PROMPT_TEMPLATE_VERSION
    for k in ("① 预检", "② 定位", "③ 理由", "④ 交付", "⑤ 自检"):
        assert k in p
    for k in ("触发行", "现象", "依据"):
        assert k in p
    # 契约是整方法改写 ⇒ prompt 不能再索要锚点三元组
    assert pg.METHOD_LEVEL_CONTRACT is True
    assert '"anchor"' not in p
    assert '"original_code"' in p and '"modified_code"' in p


# ═════════════ 刀7 六段指标 ═════════════

def test_刀7_六段顺序与NA语义(tmp_path):
    m = cm.compute_chain_metrics(str(tmp_path), now=1.0)
    assert [s["stage"] for s in m["stages"]] == list(cm.STAGES)
    gen = [s for s in m["stages"] if s["stage"] == cm.STAGE_GENERATE][0]
    # 草稿池不存在 ⇒ 分子分母都不是 0，rate 是 None（不拿 None 当 0）
    assert gen["numerator"] is None
    assert gen["rate"] is None
    line = cm.weekly_report_line(m)
    assert line.startswith(cm.WEEKLY_TAG)
    assert "NA(" in line


def test_刀7_空路径拒写():
    r = cm.write_chain_metrics("", {"schema": cm.CHAIN_SCHEMA, "stages": []})
    assert r["written"] is False and r["why"] == "empty_path"


def test_刀7_隔离落盘可读回(tmp_path):
    m = cm.compute_chain_metrics(str(tmp_path), now=1.0)
    p = tmp_path / "out" / "chain.json"
    r = cm.write_chain_metrics(str(p), m, now=2.0)
    assert r["written"] is True
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["schema"] == cm.CHAIN_SCHEMA and back["written_at"] == 2.0


# ═════════════ 刀8 告警抑制 ═════════════

def test_刀8_抑制生效且不外溢且不误伤():
    def _reduce():
        warnings.warn("`torch.distributed.reduce_op` is deprecated", FutureWarning)

    def _other():
        warnings.warn("另一条 FutureWarning", FutureWarning)

    def _user():
        warnings.warn("这是 UserWarning", UserWarning)

    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        with ar.suppress_torch_deprecation():
            _reduce()
            _other()
            _user()
        msgs = [str(x.message) for x in rec]
    assert not any("reduce_op" in m for m in msgs)      # 抑制生效
    assert any("另一条 FutureWarning" in m for m in msgs)  # 不误伤同类
    assert any("UserWarning" in m for m in msgs)          # 不误伤他类

    with warnings.catch_warnings(record=True) as rec2:
        warnings.simplefilter("always")
        _reduce()                                          # 块外
    assert sum(1 for x in rec2 if "reduce_op" in str(x.message)) == 1  # 不外溢


def test_刀8_计数仍可用():
    n = ar.ast_live_count()
    assert isinstance(n, int) and n >= 0


# ═════════════ 刀9 渠道池 ═════════════

def test_刀9_三新增渠道齐备():
    os.environ.setdefault("PULSE_FRAMEWORK", "1")
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    # ★466 改口径（2026-11-06 小林裁决）：百炼渠道 2→7 条，原 qwen-plus/qwen-max
    #   额度耗尽 403 ⇒ 换成 qwen3.8-flash / qwen3.7-plus 等新档，priority 整表重排。
    #   回滚点：466 回退时本块需退回 bailian-qwen-plus(2, qwen-plus) /
    #   bailian-qwen-max(5, qwen-max) 旧表。
    for n, pri, model in (("zhipu-47-flash", 1, "glm-4.7-flash"),
                          ("bailian-qwen38-flash", 2, "qwen3.7-flash"),
                          # ★352追补项1：Qwen2-7B 已下架（403）⇒ 改 zai-org/GLM-5.3-Flash
                          #   ★466：priority 3→5（百炼插队后往后挪）
                          ("siliconflow-qwen2-7b", 5, "zai-org/GLM-5.3-Flash"),
                          ("bailian-qwen37-plus", 6, "qwen3.7-plus")):
        assert n in ch, "缺渠道 %s" % n
        assert ch[n]["priority"] == pri
        assert ch[n]["model"] == model
        assert ch[n]["enabled"] is True
    # ★466 新增的 5 条同样必须齐备（名字/优先级/模型/启用四判）
    for n, pri, model in (("bailian-deepseek-v41-flash", 3, "deepseek-v4.1-flash"),
                          ("bailian-glm53", 4, "glm-5.3"),
                          ("bailian-kimi-k3", 7, "kimi-k3"),
                          ("bailian-deepseek-v4-pro", 9, "deepseek-v4-pro"),
                          ("bailian-qwen38-max", 10, "qwen3.8-max")):
        assert n in ch, f"缺 466 新增渠道 {n}"
        assert ch[n]["priority"] == pri
        assert ch[n]["model"] == model
        assert ch[n]["enabled"] is True


def test_刀9_优先级与ark已移除():
    import config
    ch = {c["name"]: c for c in config.REMOTE_API_CHANNELS["default_channels"]}
    # ★466 改口径：百炼 2→7 条插入后，zhipu 4→8、deepseek 8→11（收费兜底继续垫底）。
    #   回滚点：466 回退时这两行需退回 4 / 8。
    assert ch["zhipu"]["priority"] == 8
    assert ch["deepseek"]["priority"] == 11
    # ★任务书 277d465 改口径：ark-* 从 default_channels **移除**，不再留 enabled=False 的挂起条目。
    #   实测现场是 10 条（任务书写 8，方案表漏列 ark-seed-21-turbo），10 条全删。
    ark = [c for c in ch.values() if str(c["name"]).startswith("ark-")]
    assert ark == [], "ark-* 应已从渠道池移除，实际残留 %d 条" % len(ark)
    # 移除 10 条 ark 后为 5 条；追加 bailian-qwen-max 备选后为 6 条；
    # ★352追补项4 再加 modelscope(p7) ⇒ 7 条
    # ★466：百炼 2→7 条（净 +5）⇒ 12 条；modelscope 保留配置但不入池
    assert len(ch) == 12, "池内应为 12 条，实际 %d 条" % len(ch)
    # ★存量红修复（2026-11-06 路灯）：426刀4 已把 modelscope 降级为
    #   priority=99 / enabled=False（模型下架 HTTP 400），352 此处 `== 7` 自 426 起即红。
    #   A/B 已证与 466 无关（退回 HEAD 版 config 仍红）⇒ 按 426 的新规格改钉。
    #   回滚点：若 426刀4 回退，此两行需退回 priority == 7（且无 enabled 断言）。
    assert ch["modelscope"]["priority"] == 99
    assert ch["modelscope"]["enabled"] is False
    assert ch["modelscope"]["model"] == "Qwen/Qwen2.5-7B-Instruct"


def test_刀9_ssrf白名单与高级渠道():
    import config
    extra = tuple(config.SSRF_TRUSTED_EXTRA_HOSTS)
    assert "dashscope.aliyuncs.com" in extra
    assert "api.siliconflow.cn" in extra
    # 收费兜底模型名保留（回退单端点时仍用它）
    assert config.REMOTE_API_CHANNELS["advanced_model"] == "deepseek-flash"


def test_刀9_两引擎不直读单端点():
    """⑧⑨：两引擎 `_call_llm` 的取数段必须走渠道池，不得再直读 REMOTE_API_CONFIG。

    这是验收行"调用记录实际路由渠道可 grep"的静态前提：源码里还在直读单端点，
    日志里写出花来也只是口头声明。
    """
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel, tag in (("nucleus/evolution/LLMEvolutionEngine.py", "evolution"),
                     ("nucleus/evolution/SelfReflectionEngine.py", "reflection")):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            src = f.read()
        i = re.search(r"def _call_llm\(", src)
        assert i, "%s 里找不到 _call_llm" % rel
        body = src[i.start():]
        # 截到方法体结束（下一个同缩进 def）
        m = re.search(r"\n    def ", body[10:])
        if m:
            body = body[:m.start() + 10]
        assert "get_llm_call_targets(" in body, "%s 未接渠道池" % rel
        assert "REMOTE_API_CONFIG" not in body, "%s 仍在直读 REMOTE_API_CONFIG" % rel
        assert ("[352接线] engine=" + tag) in body, "%s 缺路由留痕" % rel


def test_刀9_转移链只含有key渠道():
    """失效转移链：按 priority 升序，且必须逐条跳过没配 key 的渠道。

    `get_active_channels()` 只过滤 enabled/api_url，**不看密钥**；池里 bailian /
    siliconflow 现无 key，若不过滤会顶在前面 ⇒ 拿 url 配空 key ⇒ 401 卡死链路。
    """
    import config
    chain = config.get_llm_call_targets(None)
    assert chain, "转移链为空"
    assert chain[0]["channel"] == "zhipu-47-flash"
    assert all(c["api_key"] for c in chain), "转移链混入了无密钥渠道"
    names = [c["channel"] for c in chain]
    assert "bailian-qwen-plus" not in names, "无 key 的百炼不应进链"
    assert "bailian-qwen-max" not in names, "无 key 的百炼备选不应进链"
    assert "single_endpoint" not in names, "有可用渠道时不该退单端点"


def test_刀9_指定模型只挑同名渠道():
    """点名模型时不能把该模型名硬套到别家 host（必 404）⇒ 只挑同名渠道；
    池里没有该模型 ⇒ 退回单端点（语义与本批之前一致）。"""
    import config
    hit = config.get_llm_call_targets("glm-4-flash")
    assert hit and all(c["model"] == "glm-4-flash" for c in hit)
    miss = config.get_llm_call_targets("no-such-model-xyz")
    assert len(miss) == 1 and miss[0]["source"] == "single_endpoint"
    assert miss[0]["model"] == "no-such-model-xyz"
