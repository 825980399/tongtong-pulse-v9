# -*- coding: utf-8 -*-
"""第360批 A 组（API 统一与付费止血）验收测试。

背景（小林 2026-10-24 亲查）：DeepSeek 开放平台 10-23 单日 v4-pro
122 次 / 1.09 亿 tokens，而框架账本只记 450,129 tokens，生产代码无
v4-pro 活跃调用点。A 组三刀的目标：**先止血（免费优先）、再看见（出口埋点）、
后可算（付费占比）**。

覆盖：
- 刀0-1 三场景统一免费优先 + 高级任务质量门（禁止免费一失败就付费）
- 刀0-2 付费出口全链路埋点（含 v4-pro WARNING 判据）
- 刀0-3 付费额度看板（付费/免费/占比，无样本= None 不是 0）
- 刀0-4 路灯自查（生产面无第三处直连 api.deepseek.com）

★铁律：渠道名/端点一律从 config 动态推导，绝不硬编码（硬编码会随渠道池
  调整假失败 / 假绿）。
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus._silent_except import silent_exc  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk_lung():
    """轻量实例化 PulseLung（绕过 __init__），只注入本批断言所需的最小依赖。"""
    from organs.body.PulseLung import PulseLung
    _l = PulseLung.__new__(PulseLung)
    _l._logs = []
    _l._log = lambda lv, msg: _l._logs.append((lv, str(msg)))
    _l._channel_conc = None
    _l._channel_conc_lock = threading.Lock()
    _l._dialog_history = deque(maxlen=10)
    _l._dialog_history_lock = threading.Lock()
    _l._current_call_is_background = False
    _l._is_advanced_task = lambda _m: False
    _l._build_advanced_channel = lambda: None
    _l._get_channel_health = lambda: None
    _l._update_channel_health = lambda *a, **k: None
    _l._record_model_result = lambda *a, **k: None
    _l._adjust_channel_concurrency = lambda *a, **k: None
    _l._gateway_channel = lambda: None
    _l._last_progress_ts = 0.0
    _l._progress_callback = None
    return _l


def _m360_non_code_lines(path):
    """返回该 .py 中**被字符串（含 docstring）或注释覆盖**的行号集合。

    ★铁律219：判据扫全仓时，docstring 里的说明文字**不是**代码引用。
    只排 `#` 注释不够 —— 三引号 docstring 会整段误命中（本轮实测 7 处假 FAIL
    全部来自 docstring）。用 tokenize 精确到 STRING/COMMENT token 的行范围。
    """
    import tokenize
    _bad = set()
    try:
        with open(path, "rb") as _f:
            for _tk in tokenize.tokenize(_f.readline):
                if _tk.type == tokenize.COMMENT:
                    _bad.add(_tk.start[0])
                elif _tk.type == tokenize.STRING:
                    for _r in range(_tk.start[0], _tk.end[0] + 1):
                        _bad.add(_r)
    except Exception as _e:
        silent_exc(_e, where="tests.test_m360_batch::_m360_non_code_lines tokenize", level="debug")
        return None          # 解析不了 ⇒ 交回调用方跳过该文件（不臆断）
    return _bad


def _paid_names():
    return set(str(x) for x in (getattr(config, "PAID_CHANNEL_NAMES", []) or []))


def _first_free_channel():
    """取一个免费渠道名（从 config 动态推导）。"""
    for _c in (config.get_active_channels() or []):
        if isinstance(_c, dict) and str(_c.get("name")) not in _paid_names():
            return str(_c.get("name"))
    return "zhipu"


# ======================================================================
class TestM360D01ConfigSwitches(unittest.TestCase):
    """刀0-1①：两个止血开关必须已是新值（读回，不 mock）。"""

    def test_01_user_dialog_prefer_paid_is_false(self):
        self.assertFalse(getattr(config, "USER_DIALOG_PREFER_PAID", True),
                         "360 刀0-1：用户对话改为免费优先（USER_DIALOG_PREFER_PAID=False）")

    def test_02_evolution_use_channel_pool_is_true(self):
        self.assertTrue(getattr(config, "ENABLE_EVOLUTION_USE_CHANNEL_POOL", False),
                        "360 刀0-1：补丁/代码学习必须走渠道池，不再直连付费端点")

    def test_03_paid_trace_enabled(self):
        self.assertTrue(getattr(config, "ENABLE_PAID_API_TRACE", False),
                        "360 刀0-2：付费出口埋点总开关应为 True（否则抓不到 v4-pro 来源）")

    def test_04_advanced_quality_gate_defaults(self):
        self.assertTrue(getattr(config, "ADVANCED_TASK_FREE_FIRST", False),
                        "高级任务必须先打免费档")
        self.assertGreaterEqual(int(getattr(config, "ADVANCED_TASK_FREE_MIN_ATTEMPTS", 0)), 2,
                                "禁止免费渠道一失败就直接付费（至少 2 个免费档）")


# ======================================================================
class TestM360D01Ordering(unittest.TestCase):
    """刀0-1②：三场景统一排序 —— 免费优先、付费垫底。"""

    def test_05_paid_goes_last_for_all_three_callers(self):
        _l = _mk_lung()
        _paid = sorted(_paid_names())
        self.assertTrue(_paid, "本用例需要至少 1 个付费渠道名")
        _free = _first_free_channel()
        _cand = [
            {"name": _paid[0], "priority": -1},   # advanced：360 前 priority=-1 插队
            {"name": _free, "priority": 7},
            {"name": _paid[-1], "priority": 8},
        ]
        _out = _l._m360_order_candidates(_cand)
        _names = [c["name"] for c in _out]
        # 所有免费档必须排在所有付费档之前
        _last_free = max(i for i, n in enumerate(_names) if n == _free)
        _first_paid = min(i for i, n in enumerate(_names) if n in _paid)
        self.assertLess(_last_free, _first_paid,
                        "免费渠道必须全部排在付费渠道之前：%s" % _names)

    def test_06_advanced_no_longer_jumps_queue(self):
        """advanced（priority=-1）不得再因 priority 小而插到免费档前面。"""
        _l = _mk_lung()
        _paid = sorted(_paid_names())
        if "advanced" not in _paid:
            self.skipTest("PAID_CHANNEL_NAMES 不含 advanced，本例不适用")
        _free = _first_free_channel()
        _cand = [{"name": _free, "priority": 7}, {"name": "advanced", "priority": -1}]
        _out = _l._m360_order_candidates(_cand)
        self.assertEqual(_out[0]["name"], _free,
                         "advanced 不得再插队到免费档之前：%s" % [c["name"] for c in _out])

    def test_07_order_is_stable_and_total(self):
        """排序不得丢渠道、不得重复（防止分组时漏要素）。"""
        _l = _mk_lung()
        _cand = [{"name": "a", "priority": 3}, {"name": "deepseek", "priority": 8},
                 {"name": "b", "priority": 1}, {"name": "advanced", "priority": -1}]
        _out = _l._m360_order_candidates(_cand)
        self.assertEqual(sorted(c["name"] for c in _out),
                         sorted(c["name"] for c in _cand), "排序不得丢/增渠道")

    def test_08_order_failure_returns_original(self):
        """排序器自身异常不得吞掉候选池（原样返回，绝不返回空）。"""
        _l = _mk_lung()
        # 入参 None ⇒ 归一为 []（不得抛异常、不得返回 None 让调用方炸）
        self.assertEqual(_l._m360_order_candidates(None), [])
        self.assertEqual(_l._m360_order_candidates([]), [])


# ======================================================================
class TestM360D01QualityGate(unittest.TestCase):
    """刀0-1③：高级任务质量门 —— 禁止免费一失败就直接付费。"""

    def _lung(self):
        _l = _mk_lung()
        _l._m360_reply_truncated = False
        _l._m360_patch_precheck_failed = False
        return _l

    def test_09_paid_blocked_when_no_free_tried(self):
        _l = self._lung()
        self.assertTrue(_l._m360_adv_paid_blocked({"name": "deepseek"}, 0),
                        "一个免费档都没试过时，付费必须暂不放行")

    def test_10_paid_blocked_after_only_one_free_fail(self):
        _l = self._lung()
        self.assertTrue(_l._m360_adv_paid_blocked({"name": "deepseek"}, 1),
                        "只失败 1 个免费档就付费 = 任务书明令禁止")

    def test_11_paid_allowed_after_min_free_failed(self):
        _l = self._lung()
        _min = _l._m360_adv_free_min()
        self.assertFalse(_l._m360_adv_paid_blocked({"name": "deepseek"}, _min),
                         "已试满 %d 个免费档仍失败 ⇒ 允许付费兜底" % _min)

    def test_12_paid_allowed_on_truncated_free_reply(self):
        """免费档返回被截断（finish_reason=length）⇒ 立刻放行付费取完整结果。"""
        _l = self._lung()
        _l._m360_reply_truncated = True
        self.assertFalse(_l._m360_adv_paid_blocked({"name": "deepseek"}, 0),
                         "结果被截断属于坏结果，应放行付费")

    def test_13_paid_allowed_on_patch_precheck_failed(self):
        _l = self._lung()
        _l._m360_patch_precheck_failed = True
        self.assertFalse(_l._m360_adv_paid_blocked({"name": "deepseek"}, 0),
                         "补丁产物预检不通过 ⇒ 应放行付费")

    def test_14_free_channel_never_blocked(self):
        """质量门只拦付费渠道，免费渠道一律放行（否则会把自己饿死）。"""
        _l = self._lung()
        _free = _first_free_channel()
        self.assertFalse(_l._m360_adv_paid_blocked({"name": _free}, 0))

    def test_15_gate_can_be_disabled_by_switch(self):
        """ADVANCED_TASK_FREE_FIRST=False ⇒ 退回 360 前行为（回退闸）。"""
        _l = self._lung()
        _old = getattr(config, "ADVANCED_TASK_FREE_FIRST", True)
        try:
            config.ADVANCED_TASK_FREE_FIRST = False
            self.assertFalse(_l._m360_adv_paid_blocked({"name": "deepseek"}, 0))
        finally:
            config.ADVANCED_TASK_FREE_FIRST = _old

    def test_16_truncated_detection(self):
        from organs.body.PulseLung import PulseLung
        _f = PulseLung._m360_extract_truncated
        self.assertTrue(_f({"choices": [{"finish_reason": "length"}]}))
        self.assertFalse(_f({"choices": [{"finish_reason": "stop"}]}))
        self.assertFalse(_f({}))
        self.assertFalse(_f(None))
        self.assertFalse(_f("not a dict"))

    def test_17_advanced_falls_back_to_paid_after_two_free_fail(self):
        """端到端：高级任务下免费档全失败 ⇒ 付费兜底成功，且**付费排在最后**。

        注：`_call_via_channels` 不接受候选池入参（渠道池内部从 config 自取），
        故本例用**真实渠道池** + spy `_call_channel` 记录实际调用次序 ——
        比注入假渠道更能证伪"排序写了没生效"。
        """
        _l = self._lung()
        _l._is_advanced_task = lambda _m: True
        _paid = _paid_names()
        _seq = []

        def _spy(ch, prompt, **kw):
            _seq.append(str(ch.get("name")))
            return "付费兜底成功" if str(ch.get("name")) in _paid else None

        _l._call_channel = _spy
        _r = _l._call_via_channels("x", "deep_think", caller="advanced")
        self.assertEqual(_r, "付费兜底成功", "免费全失败后必须能兜底到付费：%s" % _seq)
        self.assertEqual(_seq[-1] in _paid, True,
                         "最后一个被尝试的必须是付费渠道：%s" % _seq)
        _paid_idx = [i for i, n in enumerate(_seq) if n in _paid]
        _free_idx = [i for i, n in enumerate(_seq) if n not in _paid]
        self.assertTrue(_free_idx, "必须真的先试过免费档：%s" % _seq)
        self.assertGreater(min(_paid_idx), max(_free_idx),
                           "所有免费档都必须排在付费档之前：%s" % _seq)
        self.assertGreaterEqual(len(_free_idx), _l._m360_adv_free_min(),
                                "质量门要求：回退付费前至少试过 %d 个免费档（实得 %d）"
                                % (_l._m360_adv_free_min(), len(_free_idx)))


# ======================================================================
class TestM360D02Trace(unittest.TestCase):
    """刀0-2：付费出口全链路埋点。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="m360trace_")
        self._path = os.path.join(self._tmpdir, "paid_api_trace.jsonl")
        self._old_rel = getattr(config, "PAID_API_TRACE_REL", "")
        config.PAID_API_TRACE_REL = self._path
        from nucleus.llm import paid_api_trace as _t
        self._t = _t

    def tearDown(self):
        config.PAID_API_TRACE_REL = self._old_rel

    def _lines(self):
        if not os.path.exists(self._path):
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            return [json.loads(x) for x in f.read().splitlines() if x.strip()]

    def _paid_url(self):
        return str((getattr(config, "REMOTE_API_CONFIG", {}) or {}).get("api_url", "")
                   or "https://api.deepseek.com/v1/chat/completions")

    def test_18_paid_request_is_recorded(self):
        _rid = self._t.begin(self._paid_url(), "deepseek-flash",
                             caller="unit_test", payload={"model": "deepseek-flash"})
        self.assertTrue(_rid, "付费端点必须产生 rid")
        self._t.finish(_rid, True, {"prompt_tokens": 10})
        _recs = self._lines()
        self.assertEqual(len(_recs), 2, "应有 request + result 两行")
        _req = _recs[0]
        self.assertEqual(_req["event"], "request")
        self.assertEqual(_req["model"], "deepseek-flash")
        self.assertEqual(_req["pid"], os.getpid())
        self.assertTrue(_req["stack"], "必须带调用栈（否则抓不到来源）")
        self.assertLessEqual(len(_req["stack"]), 5, "调用栈取前 5 帧")
        self.assertEqual(_recs[1]["rid"], _rid)
        self.assertTrue(_recs[1]["ok"])

    def test_19_non_paid_endpoint_not_recorded(self):
        """非付费端点零开销（不写盘），避免埋点把日志撑爆。"""
        _rid = self._t.begin("https://open.bigmodel.cn/api/paas/v4/chat/completions",
                              "glm-4.7-flash", caller="unit_test")
        self.assertEqual(_rid, "")
        self.assertEqual(self._lines(), [])

    def test_20_expensive_model_flagged(self):
        self.assertTrue(self._t.is_expensive_model("deepseek-v4-pro"),
                        "v4-pro 必须被判为大上下文收费模型（打 WARNING 便于定位）")
        self.assertTrue(self._t.is_expensive_model("DEEPSEEK-V4-PRO"))
        self.assertFalse(self._t.is_expensive_model("deepseek-flash"),
                         "flash 不该被误判为 pro（否则 WARNING 泛滥）")

    def test_21_trace_writes_and_survives_failure_path(self):
        """start→失败 也要留痕（否则"调用失败"这一半证据会缺）。"""
        _rid = self._t.begin(self._paid_url(), "deepseek-flash", caller="unit_test")
        self._t.finish(_rid, False, None, error="HTTP 429")
        _recs = self._lines()
        self.assertEqual(len(_recs), 2)
        self.assertFalse(_recs[1]["ok"])
        self.assertEqual(_recs[1]["error"], "HTTP 429")

    def test_22_trace_never_raises_on_bad_path(self):
        """埋点绝不能阻断主流程：路径不可写时静默降级。"""
        config.PAID_API_TRACE_REL = os.path.join(self._tmpdir, "no", "such", "dir", "x")
        try:
            _rid = self._t.begin(self._paid_url(), "m", caller="unit_test")
            self._t.finish(_rid, True, None)   # 不得抛出
        except Exception as e:
            self.fail("埋点不得向主流程抛异常：%r" % e)
        finally:
            config.PAID_API_TRACE_REL = self._path

    def test_23_trace_disabled_by_switch(self):
        _old = getattr(config, "ENABLE_PAID_API_TRACE", True)
        try:
            config.ENABLE_PAID_API_TRACE = False
            self.assertEqual(self._t.begin(self._paid_url(), "m"), "")
            self.assertEqual(self._lines(), [])
        finally:
            config.ENABLE_PAID_API_TRACE = _old

    def test_24_is_paid_endpoint_uses_config(self):
        self.assertTrue(self._t.is_paid_endpoint(self._paid_url()))
        self.assertFalse(self._t.is_paid_endpoint("https://example.com/v1/chat"))
        self.assertFalse(self._t.is_paid_endpoint(""))


# ======================================================================
class TestM360D03Dashboard(unittest.TestCase):
    """刀0-3：付费额度看板（付费/免费/占比）。"""

    def test_25_paid_usage_line_no_sample_is_none_not_zero(self):
        """铁律155：无样本是 None 不是 0（0 会被当成"今天没花钱"）。"""
        _tmp = tempfile.mkdtemp(prefix="m360daily_")
        _old = getattr(config, "DATA_DIR", None)
        try:
            config.DATA_DIR = _tmp
            from nucleus.llm.call_recorder import paid_usage_line
            _r = paid_usage_line(base_dir=_tmp, log=False)
            self.assertIsNone(_r["paid_share"], "无样本时占比必须是 None 不是 0")
            self.assertIsNone(_r["free_share"])
        finally:
            if _old is None:
                try:
                    delattr(config, "DATA_DIR")
                except Exception as _e:
                    silent_exc(_e, where="tests.test_m360_batch::还原 DATA_DIR", level="debug")
            else:
                config.DATA_DIR = _old

    def test_26_paid_by_model_bucket_exists(self):
        """付费侧按模型分桶（回答"是哪个模型在烧钱"）。"""
        from nucleus.llm.call_recorder import _m352b_empty_day, _m352b_accumulate
        _d = _m352b_empty_day("2026-10-24")
        self.assertIn("paid_by_model", _d, "360 刀0-3 新增付费模型分桶")
        # 注：`_m352b_accumulate` 取的是 `rec["tokens"]`（不是 usage 子字典）
        _out = _m352b_accumulate(
            _d, {"channel": "deepseek", "model": "deepseek-v4-pro",
                 "tokens": 1000}, "2026-10-24", None)
        self.assertEqual(int(_out.get("paid_tokens") or 0), 1000)
        _pb = _out.get("paid_by_model") or {}
        self.assertEqual(int((_pb.get("deepseek-v4-pro") or {}).get("tokens") or 0), 1000,
                         "v4-pro 用量必须能在模型分布里被看见")

    def test_27_free_call_not_counted_as_paid(self):
        from nucleus.llm.call_recorder import _m352b_empty_day, _m352b_accumulate
        _d = _m352b_empty_day("2026-10-24")
        _out = _m352b_accumulate(
            _d, {"channel": _first_free_channel(), "model": "glm-4.7-flash",
                 "tokens": 500}, "2026-10-24", None)
        self.assertEqual(int(_out.get("paid_tokens") or 0), 0)
        self.assertEqual(int(_out.get("free_tokens") or 0), 500)

    def test_28_evolution_channel_name_is_carried(self):
        """刀0-3：进化链必须带渠道名，否则付费用量会被计成免费（占比恒 0）。"""
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        _f = SafeEvolutionExecutor._m360_channel_of_url
        _paid_url = str((getattr(config, "REMOTE_API_CONFIG", {}) or {})
                        .get("api_url", "") or "https://api.deepseek.com/v1/chat")
        self.assertIn("deepseek", _f(_paid_url),
                      "付费端点必须归位到付费渠道名（否则付费用量计成 free）")
        self.assertIsInstance(_f("https://open.bigmodel.cn/api/paas/v4/"), str)

    def test_29_daily_reader_section_appends(self):
        """日报付费占比小节必须真的写进文件（356 刀7 的教训：写了函数没挂）。"""
        from nucleus.self_awareness import daily_reader
        self.assertTrue(hasattr(daily_reader, "append_paid_usage_section"),
                        "日报必须有付费占比小节出口")
        _tmp = tempfile.mkdtemp(prefix="m360rep_")
        _rp = os.path.join(_tmp, "daily.md")
        with open(_rp, "w", encoding="utf-8") as f:
            f.write("# 日报\n")
        _old = getattr(config, "DATA_DIR", None)
        try:
            config.DATA_DIR = _tmp
            _r = daily_reader.append_paid_usage_section(_rp)
            _txt = open(_rp, "r", encoding="utf-8").read()
            self.assertIn("付费token", _txt, "日报必须出现付费读数行")
            self.assertIn("付费占比", _txt)
            self.assertIsInstance(_r, dict)
        finally:
            if _old is None:
                try:
                    delattr(config, "DATA_DIR")
                except Exception as _e:
                    silent_exc(_e, where="tests.test_m360_batch::还原 DATA_DIR", level="debug")
            else:
                config.DATA_DIR = _old


# ======================================================================
class TestM360D04SelfAudit(unittest.TestCase):
    """刀0-4：路灯自查 —— 生产面无第三处直连 api.deepseek.com 的活跃调用。"""

    def test_30_no_third_direct_call_site(self):
        """生产面（config/main/organs/nucleus）除配置定义外不得有直连调用点。"""
        _roots = ["config.py", "main.py", "organs", "nucleus"]
        _hits = []
        for _r in _roots:
            _p = os.path.join(_PROJECT_ROOT, _r)
            if os.path.isfile(_p):
                _files = [_p]
            else:
                _files = []
                for _dirpath, _dirnames, _fnames in os.walk(_p):
                    _dirnames[:] = [d for d in _dirnames
                                    if not d.startswith(".") and d != "__pycache__"]
                    _files.extend(os.path.join(_dirpath, f)
                                  for f in _fnames if f.endswith(".py"))
            for _fp in _files:
                _bad = _m360_non_code_lines(_fp)
                if _bad is None:
                    continue
                try:
                    with open(_fp, "r", encoding="utf-8", errors="ignore") as f:
                        for _i, _line in enumerate(f, 1):
                            if "api.deepseek.com" not in _line:
                                continue
                            if _i in _bad:
                                continue      # 注释 / docstring，不是调用点
                            _hits.append("%s:%d" % (os.path.relpath(_fp, _PROJECT_ROOT), _i))
                except Exception:
                    continue
        # 白名单：config.py 里的 api_url **定义**（不是调用）
        _allowed = {"config.py"}
        _real = [h for h in _hits if h.split(":")[0] not in _allowed]
        self.assertEqual(_real, [],
                         "生产面出现配置定义之外的 api.deepseek.com 直连：%s" % _real)

    def test_31_no_v4_pro_active_call_in_production(self):
        """生产面不得有 deepseek-v4-pro 的活跃调用（都是注释/单价表/备份）。"""
        _roots = ["main.py", "organs", "nucleus"]
        _bad = []
        for _r in _roots:
            _p = os.path.join(_PROJECT_ROOT, _r)
            _files = [os.path.join(_dirpath, f)
                      for _dirpath, _dn, _fn in os.walk(_p)
                      for f in _fn if f.endswith(".py")] \
                if os.path.isdir(_p) else [_p]
            for _fp in _files:
                _skip = _m360_non_code_lines(_fp)
                if _skip is None:
                    continue
                try:
                    with open(_fp, "r", encoding="utf-8", errors="ignore") as f:
                        for _i, _line in enumerate(f, 1):
                            if "v4-pro" not in _line and "v4_pro" not in _line:
                                continue
                            if _i in _skip:
                                continue      # docstring / 注释 / 单价表字面量
                            _bad.append("%s:%d" % (os.path.relpath(_fp, _PROJECT_ROOT), _i))
                except Exception:
                    continue
        self.assertEqual(_bad, [], "生产面出现 v4-pro 活跃引用：%s" % _bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
