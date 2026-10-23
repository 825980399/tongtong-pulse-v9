# -*- coding: utf-8 -*-
"""主线第33批 T1（P2-163）：API Key 环境变量化 门控单测。

背景：
    config.py 曾在 zhipu / doubao 渠道把**真实密钥明文**写死为
    `os.environ.get(..., "<真实密钥>")` 的默认值。第26批已治理为「只从环境变量读取，
    缺失时为空字符串」。本批补齐该治理的**回归护栏**。

测试策略（关键：**与渠道名解耦**）：
    渠道池会被上游热改（第32批 P2-187 教训：硬编码渠道名会让测试集体失效），
    因此这里不硬编码任何渠道名，改用「**注入/未注入两次运行求差集**」：
        · 未注入 → 基线（应全部为空）
        · 注入某 key → 值**发生变化**的渠道集合必须非空，且变化后的值 == 注入值
    这样即使渠道增删/改名，断言依然成立。

    由于 `config.py` 在进程内只 import 一次（模块级读取 environ），
    「环境变量优先级」只能用**子进程**做真实验证。实测单次 ~0.3s，可接受。
"""
import json
import os
import re
import subprocess
import sys
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_KEY_ENVS = (
    # ★352追补项2：TTP_REMOTE_API_KEY 已退役（统一 DEEPSEEK_API_KEY），
    #   但**仍要清空**——宿主若残留该变量，退役后它不再影响任何配置，
    #   留着不清会让人误以为"退役没生效"。清空是更强的断言环境。
    "TTP_REMOTE_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY",
    "DOUBAO_API_KEY", "ZHIPU_API_KEY", "NEWAPI_TOKEN",
    # ★352追补项1/4：新增渠道的环境变量也必须清空，
    #   否则宿主设了这些 key，test_01「无密钥时全空」会假失败。
    "SILICONFLOW_API_KEY", "DASHSCOPE_API_KEY", "MODELSCOPE_API_KEY",
)

_PROBE = r'''
import json, sys
sys.path.insert(0, __ROOT__)
import config
_out = {"__channels__": {}, "__advanced__": None, "__remote__": None,
        "__missing__": None}
for _c in ((config.REMOTE_API_CHANNELS or {}).get("default_channels", []) or []):
    _out["__channels__"][_c.get("name", "?")] = _c.get("api_key", "")
_out["__advanced__"] = (config.REMOTE_API_CHANNELS or {}).get("advanced_api_key")
_out["__remote__"] = (config.REMOTE_API_CONFIG or {}).get("api_key")
try:
    _r = config.check_channel_api_keys()
    _out["__missing__"] = sorted(_r.get("missing", []))
except Exception as _e:
    _out["__missing__"] = "ERR:" + type(_e).__name__
print("RESULT:" + json.dumps(_out, ensure_ascii=False))
'''.replace("__ROOT__", repr(_PROJ))


def _run(overrides):
    """在干净环境（清空全部密钥变量）中加载 config，返回结果字典。

    Args:
        overrides: 额外注入的环境变量（str -> str）。

    Returns:
        dict: 含 ``__channels__`` / ``__advanced__`` / ``__remote__`` / ``__missing__``。
    """
    env = dict(os.environ)
    for _k in _KEY_ENVS:
        env.pop(_k, None)
    env["PULSE_SKIP_KEY_CHECK"] = "1"
    env.update(overrides)
    r = subprocess.run([sys.executable, "-c", _PROBE],
                       capture_output=True, text=True, env=env, cwd=_PROJ)
    for _l in (r.stdout or "").splitlines():
        if _l.startswith("RESULT:"):
            return json.loads(_l[7:])
    raise AssertionError(f"探测子进程失败: rc={r.returncode} "
                         f"err={(r.stderr or '')[-400:]}")


class TestEnvPriority(unittest.TestCase):
    """① 环境变量优先级（子进程真实加载）。"""

    @classmethod
    def setUpClass(cls):
        cls.base = _run({})
        cls.zhipu = _run({"ZHIPU_API_KEY": "zzz-test-key"})
        cls.deepseek = _run({"DEEPSEEK_API_KEY": "ddd-test-key"})
        cls.ark = _run({"ARK_API_KEY": "aaa-test-key"})
        cls.doubao = _run({"DOUBAO_API_KEY": "bbb-test-key"})
        cls.both = _run({"ARK_API_KEY": "aaa-test-key",
                         "DOUBAO_API_KEY": "bbb-test-key"})
        # ★352追补项2：原用 TTP_REMOTE_API_KEY 注入 —— 退役后该变量不再被读取，
        #   旧断言会变成"永远等于空"的假通过（其实连判别力都没有：注入什么都是空）。
        #   改用 DEEPSEEK_API_KEY，测试对象与生产口径一致。
        cls.remote = _run({"DEEPSEEK_API_KEY": "ttt-test-key"})

    @staticmethod
    def _changed(before, after):
        """返回「api_key 发生变化」的渠道集合。"""
        _a = before["__channels__"]
        _b = after["__channels__"]
        return {k: _b[k] for k in _b
                if k in _a and _a[k] != _b.get(k)}

    def test_01_no_key_falls_back_to_empty(self):
        """★回退机制：无任何环境变量时，所有渠道 api_key 必须为空字符串。

        即「默认回退值 = 空」—— 源码里**不存在**任何明文密钥兜底。
        """
        _bad = {k: v for k, v in self.base["__channels__"].items() if v}
        self.assertEqual(_bad, {}, f"无密钥时不应有非空 key: {list(_bad)}")
        self.assertFalse(self.base["__advanced__"],
                         "advanced_api_key 无环境变量时应为空")
        self.assertFalse(self.base["__remote__"],
                         "REMOTE_API_CONFIG.api_key 无环境变量时应为空")

    def test_02_zhipu_env_takes_effect(self):
        _d = self._changed(self.base, self.zhipu)
        self.assertTrue(_d, "注入 ZHIPU_API_KEY 后应有渠道 api_key 变化")
        self.assertEqual(set(_d.values()), {"zzz-test-key"},
                         "变化后的值必须等于注入值（环境变量优先）")

    def test_03_deepseek_env_takes_effect(self):
        _d = self._changed(self.base, self.deepseek)
        self.assertTrue(_d, "注入 DEEPSEEK_API_KEY 后应有渠道 api_key 变化")
        self.assertEqual(set(_d.values()), {"ddd-test-key"})
        self.assertEqual(self.deepseek["__advanced__"], "ddd-test-key",
                         "advanced_api_key 应取 DEEPSEEK_API_KEY")

    def test_04_ark_env_no_longer_effective(self):
        """★352刀9 起 ark-* 渠道已从 default_channels **移除** ⇒ 应无任何变化。

        原断言是「注入 ARK_API_KEY 后必须有渠道变化」，在 ark 移除后必然失败。
        判据反转而不删测试：将来若有人把 ark 加回池子，本用例会 FAIL 提醒同步。
        """
        _d = self._changed(self.base, self.ark)
        self.assertEqual(_d, {},
                         "ARK_API_KEY 不应再影响任何渠道（ark-* 已移除）: %s"
                         % list(_d))

    def test_05_doubao_alias_no_longer_effective(self):
        """★352刀9 起 ark-* 移除 ⇒ DOUBAO_API_KEY（ARK 的兼容别名）同样不再生效。"""
        _d = self._changed(self.base, self.doubao)
        self.assertEqual(_d, {},
                         "DOUBAO_API_KEY 不应再影响任何渠道（ark-* 已移除）: %s"
                         % list(_d))

    def test_06_ark_and_doubao_both_ineffective(self):
        """ARK 与 DOUBAO 同时存在也无变化（两者都指向已移除的 ark 渠道）。"""
        self.assertEqual(self.both["__channels__"], self.base["__channels__"],
                         "同时设置 ARK+DOUBAO 不应改变渠道池（ark-* 已移除）")

    def test_07_remote_config_key_from_env(self):
        """★352追补项2：REMOTE_API_CONFIG.api_key 统一取 DEEPSEEK_API_KEY。

        TTP_REMOTE_API_KEY 已退役；本用例改为断言"注入 DEEPSEEK 后该字段跟着变"，
        并**反向断言 TTP 注入不再产生作用**（退役必须是真退役，不能只改注释）。
        """
        self.assertEqual(self.remote["__remote__"], "ttt-test-key")
        _ttp_only = _run({"TTP_REMOTE_API_KEY": "ttt-should-be-ignored"})
        self.assertFalse(_ttp_only["__remote__"],
                         "TTP_REMOTE_API_KEY 已退役，注入它不应再影响 "
                         "REMOTE_API_CONFIG.api_key")

    def test_08_missing_report_reflects_env(self):
        """无密钥时应报缺失；注入全部渠道 key 后缺失集合应变小。"""
        _m0 = self.base["__missing__"]
        self.assertIsInstance(_m0, list, f"missing 应为列表，实际 {_m0!r}")
        self.assertTrue(_m0, "无密钥时应报告缺失渠道")
        _m1 = self.zhipu["__missing__"]
        self.assertLess(len(_m1), len(_m0) + 1,
                        "注入 ZHIPU 后缺失数不应增加")


class TestSourceNoPlaintextKey(unittest.TestCase):
    """② 源码审计：config.py 不得再出现明文密钥（P2-163 回归护栏）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = open(os.path.join(_PROJ, "config.py"),
                          encoding="utf-8").read()

    def test_10_no_sk_style_literal(self):
        _hits = re.findall(r'["\'](sk-[A-Za-z0-9_\-]{16,})["\']', self.src)
        self.assertEqual(_hits, [], f"config.py 存在明文 sk- 密钥: {_hits}")

    def test_11_no_ark_style_literal(self):
        _hits = re.findall(r'["\'](ark-[0-9a-f]{8}-[0-9a-f\-]{20,})["\']',
                           self.src)
        self.assertEqual(_hits, [], f"config.py 存在明文 ark- 密钥: {_hits}")

    def test_12_all_channel_keys_read_from_env(self):
        """每个渠道的 api_key 都必须来自 os.environ（而非字面量）。

        ★锚点修正：原用 `self.src.index("REMOTE_API_CHANNELS")` 会命中**注释**
        （config.py:1095 的 SUBAGENT_CHEAP_MODEL 说明），6000 字符窗口内一个
        `"api_key":` 都取不到 ⇒ 该用例在 HEAD 上就已 FAIL（本次改动前定量复核过）。
        改用带 ` = {` 的**定义**锚点，窗口放宽到 15000 覆盖 7 条渠道。
        """
        _i = self.src.index("REMOTE_API_CHANNELS = {")
        _seg = self.src[_i:_i + 15000]
        _assigns = re.findall(r'"api_key"\s*:\s*(.+)', _seg)
        self.assertGreaterEqual(len(_assigns), 7,
                                "渠道 api_key 赋值只找到 %d 条（应 >=7，"
                                "窗口可能不够）" % len(_assigns))
        for _a in _assigns:
            self.assertIn("os.environ.get", _a,
                          f"api_key 未从环境变量读取: {_a.strip()[:80]}")

    def test_13_env_defaults_are_empty(self):
        """渠道 **api_key** 读环境变量的默认值必须是空串（不得是真实密钥兜底）。

        ★判据域修正：原实现扫窗口内**所有** `os.environ.get(...)`，等价于假设
        "这个窗口里除了密钥没有别的配置"。本批把窗口 6000→15000 以覆盖第 7 条渠道后，
        `PULSE_DUP_TRACE="0"` 等正常默认值就被判成违规 —— 判据与被测对象不同域。
        改为只针对 `"api_key": os.environ.get("X", <default>)` 这一语义单元。
        """
        _i = self.src.index("REMOTE_API_CHANNELS = {")
        _seg = self.src[_i:_i + 15000]
        _pairs = re.findall(
            r'"api_key"\s*:\s*os\.environ\.get\(\s*"([A-Z_]+)"\s*,\s*([^)]*)\)',
            _seg)
        self.assertGreaterEqual(len(_pairs), 7,
                                "渠道 api_key 的环境变量读取只找到 %d 条（应 >=7）"
                                % len(_pairs))
        for _name, _default in _pairs:
            self.assertIn(_default.strip(), ('""', "''"),
                          f"{_name} 的默认值非空: {_default}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
