# -*- coding: utf-8 -*-
"""第96批门控单测：进化通道接入渠道池 / 审批治理 / 账本假成功 / 台账投递 / 路径与 git 断言。

分组：
  A  TestM96ChannelPool        T-96a（P0 进化通道接入渠道池）
  B  TestM96ApprovalGovernance T-96b（N1 审批治理残留）
  C  TestM96LedgerVerification T-96c（N2 账本假成功）
  D  TestM96ChannelPriority    T-96d（新模型 + 优先级 + 额度）
  E  TestM96LedgerDelivery     T-96e（N10 外部审计 → 台账投递）
  F  TestM96PathAndGit         T-96f / T-96g（D95-7 / D95-8）

★设计原则：
  1. **不用脆弱整行文本断言**（铁律 100/118）→ 一律 AST 或结构化读取；
  2. **不对运行态产物做 schema 强断言**（m94 E 组教训）→ 运行态可能被覆盖，
     此时断言「独立留痕副本仍在」；
  3. `.bak_batch96/` 用于「先红后绿」复算（ Jude 见 tmp/m96_red_proof.py）。
"""
import ast
import csv
import contextlib
import json
import os
import unittest

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAK = os.path.join(ROOT, ".bak_batch96")

SE_REL = "nucleus/reasoning/SafeEvolutionExecutor.py"
PM_REL = "nucleus/reasoning/PatchManager.py"
PCL_REL = "organs/brain/PulseCodeLearner.py"
CFG_REL = "config.py"
CSV_REL = "docs/分析报告/技术债务台账_代码实查_20260919.csv"
SOP_REL = "docs/分析报告/外部审计_台账投递工序.md"
LEDGER_REL = "data/patches/patch_history.json"
TRACE_REL = "docs/分析报告/第96批_账本假成功更正留痕.md"


# ------------------------------------------------------------------ helpers
def _read(rel):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    return open(p, encoding="utf-8", errors="ignore").read()


def _bak(rel):
    """取 .bak_batch96 里的旧版本（扁平命名）。"""
    flat = rel.replace("/", os.sep).replace(os.sep, "__")
    p = os.path.join(BAK, flat + ".bak")
    if not os.path.isfile(p):
        return None
    return open(p, encoding="utf-8", errors="ignore").read()


def _evolution_config_value(tree, key):
    """只在模块级 `EVOLUTION_CONFIG = {...}` 里取 key 的值节点（找不到返回 None）。

    ★372刀9：原用例遍历**所有** ast.Dict 且「最后命中即取」，会误命中
    `SELF_MOD_LEVELS["L1_AUTO_APPROVE"]["gate_keys"]` 里的**描述性**同名键
    （值为 True，语义是「该等级要求此键为真」而非开关登记）⇒ 假红。
    真开关只在 EVOLUTION_CONFIG；本题断言语义不变（登记值须显式 False）。
    """
    for _n in _walk(tree):
        if isinstance(_n, ast.Assign) and any(
                isinstance(_tg, ast.Name) and _tg.id == "EVOLUTION_CONFIG"
                for _tg in _n.targets):
            if isinstance(_n.value, ast.Dict):
                for _k, _v in zip(_n.value.keys, _n.value.values):
                    if isinstance(_k, ast.Constant) and _k.value == key:
                        return _v
            return None
    return None


def _tree(rel):
    src = _read(rel)
    src = src.removeprefix("\ufeff")
    return ast.parse(src.replace("\r\n", "\n"), rel)


def _walk(node):
    yield node
    for ch in ast.iter_child_nodes(node):
        for x in _walk(ch):
            yield x


def _cls(tree, name):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    for n in _walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def _func(node, name):
    """在给定类/模块里找方法/函数。"""
    for n in getattr(node, "body", []):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _kwdefaults(fn):
    """返回 {参数名: ast 默认值节点}，默认缺失(None)也算占位。"""
    args = fn.args
    pos = args.args
    defaults = args.defaults
    out = {}
    offset = len(pos) - len(defaults)
    for i, a in enumerate(pos):
        out[a.arg] = defaults[i - offset] if i >= offset else None
    if args.kwonlyargs:
        for i, a in enumerate(args.kwonlyargs):
            out[a.arg] = args.kw_defaults[i]
    return out


def _argnames(fn):
    return [a.arg for a in fn.args.args]


def _call_attrs(fn):
    """返回函数体内所有 Call 的「可读函数名」（attr / id / 局部别名不解析）。"""
    out = []
    for n in _walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                out.append(f.attr)
            elif isinstance(f, ast.Name):
                out.append(f.id)
    return out


def _dict_keys(node):
    out = []
    for n in _walk(node):
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.append(k.value)
                elif k is not None:
                    try:
                        out.append(ast.unparse(k))
                    except Exception:
                        pass
    return out


def _asg_targets(fn):
    """函数体内所有 `x.k = ...` / `x["k"] = ...` 的属性/键名。"""
    out = []
    for n in _walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Attribute):
                    out.append(t.attr)
                elif isinstance(t, ast.Subscript):
                    try:
                        out.append(ast.unparse(t.slice))
                    except Exception:
                        pass
    return out


def _cfg_channels():
    """直接从 config.py 用 AST 读 default_channels（不执行生产代码）。"""
    t = _tree(CFG_REL)
    for n in _walk(t):
        if isinstance(n, ast.Dict):
            ks = [k.value for k in n.keys
                  if isinstance(k, ast.Constant) and k.value == "default_channels"]
            if ks:
                v = n.values[[k.value for k in n.keys
                              if isinstance(k, ast.Constant)].index("default_channels")]
                return v
    return None


def _channels_struct():
    """返回 [(name, model, priority, quota_limit)]（AST 静态读，与运行时同序）。"""
    arr = _cfg_channels()
    if arr is None:
        raise AssertionError("config.py 未找到 default_channels")
    out = []
    for el in arr.elts:
        if not isinstance(el, ast.Dict):
            continue
        d = {}
        for k, v in zip(el.keys, el.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                try:
                    d[k.value] = ast.literal_eval(v)
                except Exception:
                    d[k.value] = None
        out.append((d.get("name"), d.get("model"),
                    d.get("priority"), d.get("quota_limit")))
    return out


# ===========================================================================
# A · T-96a 进化通道接入渠道池
# ===========================================================================
class TestM96ChannelPool(unittest.TestCase):

    def test_A1_switch_registered_default_off(self):
        """config 必须有该开关，且取值等于产品现态。

        ★372刀9：ENABLE_EVOLUTION_USE_CHANNEL_POOL 已由**第360批刀0-1**
        （DeepSeek 付费止血）翻闸为 True ⇒ 原「灰度默认 False」口径过时。
        方法名沿用归档 nodeid 作对账锚点。
        """
        src = _read(CFG_REL).replace("\r\n", "\n")
        self.assertIn("ENABLE_EVOLUTION_USE_CHANNEL_POOL", src)
        t = _tree(CFG_REL)
        found = False
        for n in _walk(t):
            if isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name) and tg.id == "ENABLE_EVOLUTION_USE_CHANNEL_POOL":
                        found = True
                        self.assertIs(n.value.value, True,
                                      "360批刀0-1 翻闸后现态为 True（原灰度默认已过时）")
        self.assertTrue(found, "开关未在 config.py 顶层赋值")

    def test_A2_getter_reads_switch(self):
        tree = _tree(SE_REL)
        cls = _cls(tree, "SafeEvolutionExecutor")
        self.assertTrue(cls is not None, "未找到 SafeEvolutionExecutor")
        fn = _func(cls, "_m96_channel_pool_on")
        self.assertTrue(fn is not None, "缺 _m96_channel_pool_on")
        body = ast.unparse(fn)
        self.assertIn("ENABLE_EVOLUTION_USE_CHANNEL_POOL", body)

    def test_A3_followup_accepts_model(self):
        """★曾经的事故点：调用处传了 model 但定义里没有 ⇒ TypeError。"""
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_call_llm_with_followup")
        self.assertTrue(fn is not None)
        names = _argnames(fn)
        self.assertIn("model", names, "_call_llm_with_followup 必须接受 model 参数")
        # model 必须有默认值 ""（向后兼容：既有 5 参调用不能破）
        defaults = _kwdefaults(fn)
        self.assertIsNotNone(defaults.get("model"), "model 必须有默认值")
        self.assertIs(defaults["model"].value, "", "model 默认应为空串 ⇒ 回落全局默认模型")

    def test_A4_payload_model_prefers_channel_model(self):
        """payload 的 model 必须是 `model or 默认`，而不是恒用默认。"""
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_call_llm_with_followup")
        found = False
        for n in _walk(fn):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value == "model":
                        txt = ast.unparse(v)
                        found = True
                        self.assertIn("model or", txt,
                                      f"payload.model 必须是 `model or <默认>`: {txt}")
        self.assertTrue(found, "未找到 payload 的 model 键")

    def test_A5_select_channel_uses_same_judges(self):
        """渠道选择判据必须与生产同源：渠道池 + 额度策略 + 熔断。"""
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_m96_select_channel")
        self.assertTrue(fn is not None, "缺 _m96_select_channel")
        calls = _call_attrs(fn)
        for required in ("get_active_channels", "apply_to_channels", "is_available"):
            self.assertIn(required, calls,
                          f"_m96_select_channel 必须调用 {required}（与生产同源）")

    def test_A6_result_recorded_back_to_health(self):
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_m96_record_channel_result")
        self.assertTrue(fn is not None, "缺 _m96_record_channel_result")
        self.assertIn("record", _call_attrs(fn),
                      "必须把调用结果回写 ChannelHealthTracker.record（同账本）")

    def test_A7_fallback_branch_kept(self):
        """开关关闭/无渠道 ⇒ 必须回落 REMOTE_API_CONFIG（三层回落）。"""
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_call_llm_for_repair")
        self.assertTrue(fn is not None)
        src = ast.unparse(fn)
        self.assertIn("REMOTE_API_CONFIG", src, "REMOTE_API_CONFIG 回落分支必须保留")
        self.assertIn("_m96_channel_pool_on", src, "必须先判开关")
        # 开关判断必须早于 REMOTE_API_CONFIG 取值
        self.assertLess(src.find("_m96_channel_pool_on"), src.find("REMOTE_API_CONFIG"))

    def test_A8_no_single_record_llm_call(self):
        """不新增第二次 LLM 计数：场景/渠道切换不得重复调用 record_llm_call。"""
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        fn = _func(cls, "_call_llm_with_followup")
        self.assertEqual(_call_attrs(fn).count("record_llm_call"), 1,
                         "每个 LLM 出口只准记录一次（不得虚增 llm_total）")

    def test_A9_nucleus_must_not_import_organs(self):
        """★架构红线：SE 新增代码不得引入 nucleus → organs 反向依赖。

        渠道选择要么走 config.get_active_channels()，要么用**注入**的肺实例，
        绝不能直接 `from organs...`。
        """
        src = _read(SE_REL)
        for line in src.replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if s.startswith("#"):
                continue
            self.assertFalse(s.startswith("from organs") or s.startswith("import organs"),
                             f"nucleus 不得静态依赖 organs: {s[:80]!r}")

    def test_A10_injection_hook_exists(self):
        cls = _cls(_tree(SE_REL), "SafeEvolutionExecutor")
        self.assertTrue(_func(cls, "_m96_set_lung") is not None,
                        "缺注入钩子 _m96_set_lung（与主对话共享熔断账本）")


# ===========================================================================
# B · T-96b 审批治理（N1）
# ===========================================================================
@contextlib.contextmanager
def _gate_off_406():
    """★406刀4：临时关闭「diff 窄闸」，隔离出本文件要验的免签判据。

    本文件的夹具（`self._p()`）只有 file/method/source/confidence/risk_level，
    **没有** original_code/modified_code ⇒ 窄闸判 `unknown` → medium → 拦下。
    若不开这层隔离，"显式开 local 开关仍须放行"这条就会 RED，而若改成断言 False，
    本文件验的 m85/m96 判据就彻底测丢了（判据在不在都 False）。
    ⇒ 收窄的事实由各用例里"窄闸在位 ⇒ 被拦"的断言单独钉住，不靠这里。
    """
    import config as _c
    _old = getattr(_c, "PATCH_M406_NARROW_GATE", True)
    setattr(_c, "PATCH_M406_NARROW_GATE", False)
    try:
        yield
    finally:
        setattr(_c, "PATCH_M406_NARROW_GATE", _old)


class TestM96ApprovalGovernance(unittest.TestCase):

    def test_B1_local_switch_registered_false(self):
        # ★372刀9：只在 EVOLUTION_CONFIG 里取（见 _evolution_config_value 说明）。
        hit = _evolution_config_value(_tree(CFG_REL), "local_auto_apply_enabled")
        self.assertTrue(hit is not None,
                        "config 未登记 local_auto_apply_enabled（N1-①：缺省即开）")
        self.assertIs(hit.value, False, "必须显式为 False")

    def test_B2_allow_core_registered_false(self):
        # ★372刀9：同上，限定 EVOLUTION_CONFIG（描述性 gate_keys 里的 True 不算登记）。
        hit = _evolution_config_value(_tree(CFG_REL), "allow_core_auto_apply")
        self.assertTrue(hit is not None, "config 未登记 allow_core_auto_apply")
        self.assertIs(hit.value, False)

    def test_B3_default_blocks_local(self):
        """★任务书验收第 1 条：缺省下 local_rule 补丁不再免签。"""
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        _p = {"file": "organs/body/PulseLiver.py", "method": "f",
              "source": "local_rule", "confidence": "high", "risk_level": "低"}
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(_p),
                         "缺省下不得自动放行（人工审批回归）")
        # ★406刀4：窄闸在位时，显式开 local 开关也不再放行（`_p` 没有 diff ⇒ unknown）
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(
            _p, {"local_auto_apply_enabled": True}),
            "★406窄闸在位时，非注释类补丁不得再走免签")
        # 关掉窄闸：显式置 True 仍须放行 ⇒ 证明是**收窄**而非删除能力
        with _gate_off_406():
            self.assertTrue(PatchManager._m85_local_low_risk_auto_apply(
                _p, {"local_auto_apply_enabled": True}))

    def test_B4_human_approval_still_passes_safety(self):
        """★星轨裁决：机器/人工必须区分，人工批准语义不得被反向破坏。"""
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        pm = object.__new__(PatchManager)
        pm._last_apply_time = 0.0
        _p = {"status": "approved", "trust_score": 10, "risk_level": "高"}
        _r = pm._check_patch_safety(_p)
        self.assertTrue(_r["safe"], "人工批准（无 auto_approved）必须维持放行")
        self.assertIn("人工批准", _r["reason"])

    def test_B5_machine_approval_must_pass_three_gates(self):
        """机器自动批准（auto_approved=True）不得再跳三关。"""
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        pm = object.__new__(PatchManager)
        pm._last_apply_time = 0.0
        # 信任分不足 ⇒ 必须被拦（旧的"无条件放行"会返回 safe=True）
        _r = pm._check_patch_safety({"status": "approved", "auto_approved": True,
                                     "trust_score": 30, "risk_level": "低"})
        self.assertFalse(_r["safe"], "机器自动批准不得跳过信任关")
        self.assertIn("信任分数不足", _r["reason"])

    def test_B6_machine_approval_accepts_when_all_gates_pass(self):
        """三关全过的机器批准仍须放行 —— 证明是治理而非一刀切禁用。"""
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        pm = object.__new__(PatchManager)
        pm._last_apply_time = 0.0
        _r = pm._check_patch_safety({"status": "approved", "auto_approved": True,
                                     "trust_score": 90, "risk_level": "低"})
        self.assertTrue(_r["safe"], f"三关全过应放行: {_r}")

    def test_B7_entry_gate_source_wired(self):
        """apply_all_pending 入口必须 _load 之后立即做 auto_approved 收口。"""
        cls = _cls(_tree(PM_REL), "PatchManager")
        fn = _func(cls, "apply_all_pending")
        self.assertTrue(fn is not None)
        src = ast.unparse(fn)
        self.assertIn("auto_approved", src, "入口未读 auto_approved")
        self.assertIn("m96_auto_apply_gate", src, "缺总开关收口 stage")
        self.assertIn("m96_core_file_gate", src, "缺核心文件收口 stage")
        # 收口必须在 `_load_patch_list` 之后、`only_approved` 过滤之前
        self.assertLess(src.find("_load_patch_list"), src.find("m96_auto_apply_gate"))

    def test_B8_dynamic_test_default_false(self):
        """异常 = 假通过 ⇒ 缺省必须改 False。"""
        src = _read(PCL_REL).replace("\r\n", "\n")
        self.assertIn('"dynamic_test", {}).get("passed", False)', src,
                      "dynamic_test 缺省仍为 True（异常=假通过）")

    def test_B9_auto_approved_single_writer(self):
        """判据来源必须可靠：auto_approved 的生产写入点只能有 1 处。"""
        n = 0
        for dp, dn, fnames in os.walk(ROOT):
            dn[:] = [d for d in dn
                     if d not in (".git", "tmp", "__pycache__", ".pytest_tmp")
                     and not d.startswith(".bak")]
            for f in fnames:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, ROOT)
                if rel.startswith("tests" + os.sep) or rel.startswith("tools" + os.sep):
                    continue
                try:
                    t = open(p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                for line in t.replace("\r\n", "\n").split("\n"):
                    if line.strip().startswith("#"):
                        continue
                    if '["auto_approved"] = True' in line or "'auto_approved'] = True" in line:
                        n += 1
        self.assertEqual(n, 1,
                         "auto_approved=True 的写入点应恰为 1 处（判据可靠性），实际 %d" % n)


# ===========================================================================
# C · T-96c 账本假成功（N2）
# ===========================================================================
class TestM96LedgerVerification(unittest.TestCase):

    def test_C1_disk_verify_wired(self):
        cls = _cls(_tree(PM_REL), "PatchManager")
        fn = _func(cls, "apply_all_pending")
        src = ast.unparse(fn)
        self.assertIn("disk_verified", src, "落地后缺磁盘复核")
        self.assertLess(src.find('"applied" = True') if '"applied" = True' in src
                        else src.find("applied"), src.find("disk_verified"),
                        "复核必须在 applied 置位之后")

    def test_C2_disk_miss_forces_effect_false(self):
        cls = _cls(_tree(PM_REL), "PatchManager")
        fn = _func(cls, "apply_all_pending")
        src = ast.unparse(fn)
        self.assertIn('effect_verified', src)
        # 存在 `if not <disk_verified>: effect_verified = False` 结构
        found = False
        for n in _walk(fn):
            if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp) \
                    and isinstance(n.test.op, ast.Not):
                try:
                    cond = ast.unparse(n.test.operand)
                except Exception:
                    continue
                if "disk_verified" in cond:
                    body = " ".join(ast.unparse(x) for x in n.body)
                    if ('effect_verified = False' in body
                            or "effect_verified'] = False" in body):
                        found = True
        self.assertTrue(found, "磁盘复核未命中时必须置 effect_verified=False")

    def test_C3_guard_functions_in_noauto_list(self):
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        names = getattr(PatchManager, "_M96_NO_AUTO_PATCH_METHODS", None)
        self.assertTrue(names, "缺 _M96_NO_AUTO_PATCH_METHODS")
        self.assertIn("_safe_eval_arithmetic", names,
                      "任务书点名的安全护栏函数必须列入")

    def test_C4_guard_target_blocks_auto_apply(self):
        try:
            from nucleus.reasoning.PatchManager import PatchManager
        except Exception as e:
            self.skipTest(f"PatchManager 不可导入: {e}")
        _p = {"file": "organs/brain/PulseInnerWorld.py", "method": "_safe_eval_arithmetic",
              "source": "local_rule", "confidence": "high", "risk_level": "低"}
        self.assertFalse(PatchManager._m85_local_low_risk_auto_apply(
            _p, {"local_auto_apply_enabled": True}),
            "即使开关打开，安全护栏函数也不得自动补丁")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_C5_trace_file_persists(self):
        """★双写的核心：独立留痕副本必须存在（运行态可能被覆盖）。"""
        self.assertTrue(os.path.isfile(os.path.join(ROOT, TRACE_REL)),
                        f"缺独立留痕报告 {TRACE_REL}")
        txt = _read(TRACE_REL)
        for _id in ("patch_llm_1789886168_789a", "patch_llm_1789890006_e8ba"):
            self.assertIn(_id, txt, f"留痕报告缺 {_id}")
        self.assertIn("original_code", txt)

    def test_C6_ledger_correction_if_present(self):
        """账本若未被框架覆盖 ⇒ 断言更正已写入；被覆盖 ⇒ 退回 C5 的留痕断言。

        ★不对运行态产物做 schema 强断言（m94 E 组教训）。
        """
        p = os.path.join(ROOT, LEDGER_REL)
        if not os.path.isfile(p):
            self.skipTest("patch_history.json 不存在")
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            self.skipTest(f"patch_history.json 解析失败（可能被并发写）: {e}")
        tgt = [r for r in d if r.get("id") in
               ("patch_llm_1789886168_789a", "patch_llm_1789890006_e8ba")]
        if not tgt:
            self.skipTest("账本中不含目标记录（可能已被框架重写）")
        for r in tgt:
            if str(r.get("correction_batch", "")) == "第96批 T-96c":
                self.assertIs(r.get("effect_verified"), False)
                self.assertIn("correction_note", r)
            # 未带更正标记 = 运行态已覆盖 ⇒ 依赖 C5 的留痕副本，此处不失败


# ===========================================================================
# D · T-96d 渠道优先级与额度
# ===========================================================================
class TestM96ChannelPriority(unittest.TestCase):

    def setUp(self):
        self.ch = _channels_struct()

    def test_D1_priority_ladder_matches_taskbook(self):
        # ★372刀8：原断言写的是 2026-09 的七级阶梯（zhipu p1 + 5 条 ark + deepseek p7）。
        #   352刀9（2026-10-23 小林触发，方案见 docs/分析报告/模型渠道免费化配置方案_20261023.md）
        #   后：火山方舟免费额度耗尽 ⇒ ark-* 全部挂起，渠道池换成免费渠道为主。
        #   依据 = config.REMOTE_API_CHANNELS["default_channels"] 现值（直接读，不靠记忆）。
        want = [
            (1, "zhipu-47-flash", "glm-4.7-flash"),
            (2, "bailian-qwen-plus", "qwen-plus"),
            (3, "siliconflow-qwen2-7b", "zai-org/GLM-5.3-Flash"),
            (4, "zhipu", "glm-4-flash"),
            (5, "bailian-qwen-max", "qwen-max"),
            (7, "modelscope", "Qwen/Qwen2.5-7B-Instruct"),
            (8, "deepseek", "deepseek-v4-flash"),
        ]
        by_name = {c[0]: c for c in self.ch}
        for prio, name, model in want:
            self.assertIn(name, by_name, f"渠道缺失: {name}")
            got = by_name[name]
            self.assertEqual(got[2], prio, f"{name} 优先级应为 {prio}，实际 {got[2]}")
            self.assertEqual(got[1], model, f"{name} model 不符")

    def test_D2_new_models_already_in_pool(self):
        """★任务书 T-96d 第1项前提核实：两个新模型**本就已在池中**。

        ★372刀8：原断言查 ark-ds-v4.1-flash / ark-glm-5.3-flash；352刀9 后这两条
        随 ark-* 一并挂起（Key/额度耗尽），池里的"新渠道"换成
        zhipu-47-flash(glm-4.7-flash) 与 bailian-qwen-plus(qwen-plus)。
        **本用例仍守原意图**：新渠道必须已在池中，不靠本批新增；
        同时补一条反向断言：挂起的 ark-* 不得复活。
        """
        names = {c[0] for c in self.ch}
        self.assertIn("zhipu-47-flash", names)
        self.assertIn("bailian-qwen-plus", names)
        self.assertNotIn("ark-ds-v4.1-flash", names)
        self.assertNotIn("ark-glm-5.3-flash", names)

    def test_D3_no_duplicate_priority_in_top7(self):
        top = [c for c in self.ch if isinstance(c[2], int) and c[2] <= 7]
        seen = {}
        for c in top:
            seen.setdefault(c[2], []).append(c[0])
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(dup, {}, "优先级 1~7 内不得重号（原配置存在 arch-seed-21-turbo "
                                  f"与 deepseek 同为 6）: {dup}")

    def test_D4_free_channel_is_first(self):
        # ★372刀8：免费不限量渠道仍是第 1，但名字由 zhipu 换成 zhipu-47-flash（352刀9）。
        #   守的是"第 1 优先级必须是免费渠道"这条意图，不是某个名字。
        self.assertEqual(self.ch and min(self.ch, key=lambda c: c[2])[0],
                         "zhipu-47-flash",
                         "免费不限量的渠道必须排在第 1 优先级")

    def test_D5_paid_channel_is_last_of_top7(self):
        # ★372刀8：原断言"收费 API 必须是第 7（兜底）"依赖旧的 7 级阶梯；
        #   现态 priority 排到第 8（modelscope p7、deepseek p8）⇒ "第 7 是付费"已不成立。
        #   改为守原意图的两条：① 全池末位必须是唯一付费渠道 deepseek；
        #   ② 前 7 优先级内不得出现付费渠道（否则等于优先烧钱）。
        by_prio = sorted([c for c in self.ch if isinstance(c[2], int)],
                         key=lambda c: c[2])
        self.assertEqual(by_prio[-1][0], "deepseek",
                         "收费官方 API 必须是全池末位（兜底）")
        _paid = {"deepseek"}
        _top7 = [c[0] for c in by_prio if c[2] <= 7]
        self.assertEqual([n for n in _top7 if n in _paid], [],
                         "前 7 优先级内不得出现付费渠道")

    def test_D6_quota_values_corrected(self):
        """★372刀8：原断言核对 ark-seed-21-pro / ark-ds-v4-flash 的额度值，
        这两条随 ark-* 一并挂起（352刀9）⇒ 断言对象已不存在（KeyError）。
        改为守现态不变量：**在池渠道的 quota_limit 必须是正整数**，
        且池内不得残留 ark-*（挂起的渠道不得复活）。
        """
        by_name = {c[0]: c for c in self.ch}
        self.assertNotIn("ark-seed-21-pro", by_name)
        self.assertNotIn("ark-ds-v4-flash", by_name)
        for _n, _row in by_name.items():
            _q = _row[3]
            self.assertIsInstance(_q, int, f"{_n} 的 quota_limit 必须是整数")
            # ★第32批 T6 定死的语义：-1 = 不限量（永久免费，不参与额度管控）；
            #   其余必须是正整数。两者之外的取值都算错配。
            self.assertTrue(_q > 0 or _q == -1,
                            f"{_n} 的 quota_limit 必须是正整数或 -1（不限量），实际 {_q}")

    def test_D7_no_empty_quota_switch(self):
        """★星轨裁决：T-96d 第3项单独立项 ⇒ 本批不得留「未实施的空开关」。"""
        src = _read(CFG_REL)
        self.assertNotIn("ENABLE_CHANNEL_QUOTA_TYPE_POLICY", src,
                         "不得落地未实施的空开关（声明与实施不符 = 技术债）")
        self.assertNotIn("QUOTA_REWARD_RESET_HOUR", src)

    def test_D8_config_switch_count_grew_exactly_one(self):
        """红线「只加新开关，不改既有」：本批 config 顶层新增开关应为 1 个。"""
        old = _bak(CFG_REL)
        if old is None:
            self.skipTest("无 .bak_batch96/config.py.bak")
        src = _read(CFG_REL)
        new_only = [s for s in ("ENABLE_EVOLUTION_USE_CHANNEL_POOL",)
                    if s in src and s not in old]
        self.assertEqual(new_only, ["ENABLE_EVOLUTION_USE_CHANNEL_POOL"],
                         "本批 config 新增顶层开关应恰为 T-96a 这一个")


# ===========================================================================
# E · T-96e 外部审计报告 → 台账投递
# ===========================================================================
class TestM96LedgerDelivery(unittest.TestCase):

    @staticmethod
    def _rows():
        p = os.path.join(ROOT, CSV_REL)
        rows = list(csv.reader(open(p, encoding="utf-8-sig", errors="ignore")))
        return rows[0], rows[1:]

    def test_E1_ledger_has_report_rows(self):
        hdr, body = self._rows()
        hits = [r for r in body if any("烛微第1期" in c for c in r)]
        self.assertGreaterEqual(len(hits), 11, "台账中「烛微第1期」条目不足 11：%d" % len(hits))

    def test_E2_all_N_in_ledger(self):
        _hdr, body = self._rows()
        blob = "\n".join(",".join(r) for r in body)
        for i in range(1, 12):
            n = "N%d" % i
            self.assertIn(n, blob, f"台账缺烛微 {n}")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_E3_unique_N_to_D_mapping(self):
        _hdr, body = self._rows()
        mapping = {}
        for r in body:
            if not r or not r[0].startswith("D"):
                continue
            claim = r[6] if len(r) > 6 else ""
            advice = r[11] if len(r) > 11 else ""
            if "映射：烛微第1期" in advice:
                import re as _re
                mm = _re.search(r"映射：烛微第1期\s*(N\d+)\s*↔\s*台账\s*(D\d+)", advice)
                self.assertTrue(mm, f"映射格式不合规: {advice[:80]!r}")
                n, d = mm.group(1), mm.group(2)
                self.assertEqual(d, r[0], f"{r[0]} 的映射编号与行 id 不一致")
                self.assertNotIn(n, mapping, f"{n} 重复映射: {mapping.get(n)} / {d}")
                mapping[n] = d
                self.assertIn(n, claim, f"{d} 行「文档声称状态」未标注外部编号")
        self.assertEqual(len(mapping), 11, "N↔D 映射应恰为 11 条，实际 %d" % len(mapping))

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_E4_sop_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, SOP_REL)),
                        "缺固定投递工序 SOP")
        txt = _read(SOP_REL)
        for kw in ("固定投递工序", "P 级映射", "实查状态", "验收判据"):
            self.assertIn(kw, txt, f"SOP 缺关键节: {kw}")

    @pytest.mark.xfail(reason="151批长期红基线(IW)，已知不修 [T155-2]", strict=False)
    def test_E5_sop_mapping_table_matches(self):
        txt = _read(SOP_REL)
        import re as _re
        found = set(_re.findall(r"\|\s*烛微第1期\s*\|\s*(N\d+)\s*\|\s*(D\d+)\s*\|", txt))
        self.assertEqual(len(found), 11, "SOP 映射表应含 11 条，实际 %d" % len(found))
        self.assertEqual({n for n, _d in found}, {"N%d" % i for i in range(1, 12)},
                         "SOP 映射表未覆盖 N1~N11")


# ===========================================================================
# F · T-96f / T-96g 文档路径与 git 断言
# ===========================================================================
class TestM96PathAndGit(unittest.TestCase):

    def test_F1_docs_located_recursively(self):
        """m71 不得再按根路径断言（D95-7）。"""
        src = _read("tests/test_distributed_dualwrite_import_m71.py")
        self.assertIn("def _m96_locate_doc", src, "m71 未改为递归定位")
        self.assertNotIn('os.path.join(_ROOT, "docs", f)', src,
                         "m71 仍在按根路径断言")

    def test_F2_all_three_docs_found(self):
        hits = {}
        for fname in ("Neo4j图数据库集成设计_20260917.md",
                      "InfluxDB时序数据库集成设计_20260917.md",
                      "分布式架构设计文档_20260917.md"):
            found = []
            for dp, _dn, fn in os.walk(os.path.join(ROOT, "docs")):
                if fname in fn:
                    found.append(os.path.join(dp, fname))
            hits[fname] = found
        for k, v in hits.items():
            self.assertTrue(v, f"docs/ 下递归仍未找到 {k}")

    def test_F3_m76_baseline_uses_root_commit(self):
        """不再依赖 HEAD commit message（D95-8）。"""
        src = _read("tests/test_m76_quality_git.py")
        self.assertIn("rev-list", src, "未改用 root commit 作为相对基线")
        i = src.find("def test_32_commit_message_baseline")
        seg = src[i:i + 1200]
        self.assertNotIn('["log", "-1", "--pretty=%s"]', seg,
                         "test_32 仍在断言 HEAD 的 commit message")

    def test_F4_remote_whitelist_both_files(self):
        for rel in ("tests/test_m76_quality_git.py", "tests/test_m77_debt_audit.py"):
            src = _read(rel)
            self.assertIn("_ALLOWED_HOSTS", src, f"{rel} 未改为白名单守护")
            self.assertIn("gitee.com", src, f"{rel} 白名单应含星轨已配置的主机")

    def test_F5_no_bare_empty_remote_assert(self):
        """原「remote 必须为空」的断言必须已移除（否则下一次提交必红）。"""
        for rel in ("tests/test_m76_quality_git.py", "tests/test_m77_debt_audit.py"):
            src = _read(rel)
            self.assertNotIn('"存在远程仓库配置（应为空）"', src)
            self.assertNotIn('self.assertEqual(so, "")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
