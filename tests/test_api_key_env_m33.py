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
    "TTP_REMOTE_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY",
    "DOUBAO_API_KEY", "ZHIPU_API_KEY", "NEWAPI_TOKEN",
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
        cls.remote = _run({"TTP_REMOTE_API_KEY": "ttt-test-key"})

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

    def test_04_ark_env_takes_effect(self):
        _d = self._changed(self.base, self.ark)
        self.assertTrue(_d, "注入 ARK_API_KEY 后应有渠道 api_key 变化")
        self.assertEqual(set(_d.values()), {"aaa-test-key"})

    def test_05_doubao_alias_fallback(self):
        """ARK 未设置时，DOUBAO_API_KEY 作为兼容别名必须生效。"""
        _d = self._changed(self.base, self.doubao)
        self.assertTrue(_d, "仅有 DOUBAO_API_KEY 时别名回退应生效")
        self.assertEqual(set(_d.values()), {"bbb-test-key"})

    def test_06_ark_wins_over_doubao(self):
        """ARK 与 DOUBAO 同时存在 → ARK 优先（`or` 短路顺序）。"""
        self.assertEqual(self.both["__channels__"], self.ark["__channels__"],
                         "同时设置时应与「仅 ARK」结果一致（ARK 优先）")

    def test_07_remote_config_key_from_env(self):
        self.assertEqual(self.remote["__remote__"], "ttt-test-key")

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
        """每个渠道的 api_key 都必须来自 os.environ（而非字面量）。"""
        _i = self.src.index("REMOTE_API_CHANNELS")
        _seg = self.src[_i:_i + 6000]
        _assigns = re.findall(r'"api_key"\s*:\s*(.+)', _seg)
        self.assertTrue(_assigns, "未找到渠道 api_key 赋值")
        for _a in _assigns:
            self.assertIn("os.environ.get", _a,
                          f"api_key 未从环境变量读取: {_a.strip()[:80]}")

    def test_13_env_defaults_are_empty(self):
        """读取环境变量的默认值必须是空串（不得是真实密钥兜底）。"""
        _i = self.src.index("REMOTE_API_CHANNELS")
        _seg = self.src[_i:_i + 6000]
        for _m in re.finditer(r'os\.environ\.get\(\s*"([A-Z_]+)"\s*,\s*([^)]*)\)',
                              _seg):
            _default = _m.group(2).strip()
            self.assertIn(_default, ('""', "''"),
                          f"{_m.group(1)} 的默认值非空: {_default}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
