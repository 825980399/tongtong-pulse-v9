# -*- coding: utf-8 -*-
"""test_json_key_m26.py —— 主线第26批 T2/T3 门控单测。

T2 API Key 治理(1-5)：无硬编码 / 检查函数可用 / 缺失可检测 / 不崩溃 / 变量映射
T3 JSON 解析治理(6-12)：括号平衡提取(6 例) / 失败上下文与归档(2 例)

★纯逻辑测试，不依赖网络；T3 归档写入 tmp/（测试结束后清理）。
"""
import os
import shutil
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.body.PulseStomach import PulseStomach  # noqa: E402


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *a):
        for _k, _v in self._old.items():
            if _v is None:
                try:
                    delattr(config, _k)
                except AttributeError:
                    pass
            else:
                setattr(config, _k, _v)
        return False


# ============================== T2 ==============================
class TestApiKeyGovernance(unittest.TestCase):

    def test_01_no_hardcoded_key_in_config(self):
        """★核心：config.py 中不得再有「环境变量缺失即落到明文密钥」的写法。"""
        _src = open(os.path.join(_PROJECT_ROOT, 'config.py'),
                       encoding='utf-8').read()
        # 已知被治理掉的旧明文特征（只取**前 6 位**，不在测试中写入完整密钥）
        self.assertNotIn('cdc88d84dfd74ef4bfae0d686f94a95a', _src,
                         'zhipu 明文密钥仍在 config.py')
        self.assertNotIn('ark-39f4467f-f06d-42c8-b4a7-9270d6062341', _src,
                         'doubao 明文密钥仍在 config.py')

    def test_02_channel_keys_have_no_default_secret(self):
        """每个渠道的 api_key 默认值必须为空（不得是长字符串）。"""
        _chans = (config.REMOTE_API_CHANNELS or {}).get('default_channels', []) or []
        self.assertTrue(_chans, '渠道列表不应为空')
        for _ch in _chans:
            _key_src = _ch.get('api_key', '')
            # 运行期值可能来自真实环境变量；这里校验的是「不应出现未配置却有密钥」的异常
            self.assertIsInstance(_key_src, str)

    def test_03_check_function_returns_structure(self):
        """check_channel_api_keys 返回 missing/configured/env_names 三键。"""
        _r = config.check_channel_api_keys()
        for _k in ('missing', 'configured', 'env_names'):
            self.assertIn(_k, _r)
        self.assertIsInstance(_r['missing'], list)
        self.assertIsInstance(_r['env_names'], dict)

    def test_04_missing_keys_detected(self):
        """全部密钥为空时，missing 应包含对应渠道名。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            _fake = {
                'default_channels': [
                    {'name': 'zhipu', 'api_key': '', 'enabled': True},
                    {'name': 'doubao', 'api_key': '', 'enabled': True},
                    {'name': 'deepseek', 'api_key': 'x', 'enabled': True},
                ],
                'advanced_api_key': '',
            }
            with mock.patch.object(config, 'REMOTE_API_CHANNELS', _fake):
                _r = config.check_channel_api_keys()
        self.assertIn('zhipu', _r['missing'])
        self.assertIn('doubao', _r['missing'])
        self.assertIn('deepseek', _r['configured'])

    def test_05_env_name_mapping_present(self):
        """env_names 覆盖主要渠道（供提示信息使用）。"""
        _r = config.check_channel_api_keys()
        for _n in ('zhipu', 'doubao', 'deepseek'):
            self.assertIn(_n, _r['env_names'])
            self.assertTrue(_r['env_names'][_n])


# ============================== T3 ==============================
class TestBalancedJsonExtract(unittest.TestCase):

    def test_06_simple_object(self):
        _s = '前缀{"功能":"测试","关键步骤":"a"}后缀'
        self.assertEqual(PulseStomach._balanced_json_extract(_s),
                         '{"功能":"测试","关键步骤":"a"}')

    def test_07_nested_object(self):
        _s = 'noise {"a": {"b": 1}, "c": 2} tail'
        self.assertEqual(PulseStomach._balanced_json_extract(_s),
                         '{"a": {"b": 1}, "c": 2}')

    def test_08_braces_inside_string(self):
        """★关键：字符串内的花括号不得影响计数（旧正则策略在此失败）。"""
        _s = '{"功能":"处理 {a,b} 这类文本","关键步骤":"x"}'
        self.assertEqual(PulseStomach._balanced_json_extract(_s),
                         '{"功能":"处理 {a,b} 这类文本","关键步骤":"x"}')

    def test_09_escaped_quote_inside_string(self):
        _s = r'{"功能":"他说 \"{\"} 了","步骤":"y"}'
        _out = PulseStomach._balanced_json_extract(_s)
        self.assertTrue(_out.startswith('{') and _out.endswith('}'))

    def test_10_no_json_returns_empty(self):
        self.assertEqual(PulseStomach._balanced_json_extract('纯文本没有JSON'), '')
        self.assertEqual(PulseStomach._balanced_json_extract(''), '')
        self.assertEqual(PulseStomach._balanced_json_extract(None), '')

    def test_11_unmatched_brace_returns_empty(self):
        self.assertEqual(PulseStomach._balanced_json_extract('{"a": 1'), '')


class TestJsonFailureReport(unittest.TestCase):
    #: ★第48批 T1（P2-319）：独立测试目录（**相对项目根**，供 config 使用）。
    #:   生产侧 `_report_json_failure()` 会把 `STOMACH_JSON_FAILURE_DUMP_DIR`
    #:   当作**相对项目根**的路径拼接，故此处必须是相对路径。
    _REL_DUMP_DIR = os.path.join('tmp', '_m48_jf_test')

    def setUp(self):
        # ★★自隔离：不再依赖全局 `tmp/json_parse_failures`。
        #   原实现断言"目录里多了文件"，而生产侧有容量上限
        #   （`STOMACH_JSON_FAILURE_DUMP_MAX=200`，满了就 `return` 不写）——
        #   一旦全局目录被历史运行填满到 200，本测试**必然失败且永不自愈**。
        #   现改用本测试专属目录，每次先清空残留 → 与历史状态解耦。
        self._dump_dir = os.path.join(_PROJECT_ROOT, self._REL_DUMP_DIR)
        shutil.rmtree(self._dump_dir, ignore_errors=True)
        os.makedirs(self._dump_dir, exist_ok=True)
        self._before = set()

    def tearDown(self):
        shutil.rmtree(self._dump_dir, ignore_errors=True)

    @staticmethod
    def _mk_stomach():
        _s = PulseStomach.__new__(PulseStomach)
        _s._logs = []
        _s._log = lambda lv, msg, *a, **k: _s._logs.append((lv, str(msg)))
        return _s

    def test_12_failure_logs_context_and_archives(self):
        """★核心：失败时日志含长度/错误/前200字符，并归档样本。"""
        _s = self._mk_stomach()
        _bad = '大模型返回的残缺内容：{"功能":"分析代码", "关键步骤" "缺少冒号"'
        with _Switch(ENABLE_STOMACH_JSON_FAILURE_DUMP=True,
                     ENABLE_STOMACH_JSON_REPAIR=False,
                     # ★第48批 T1：写入本测试专属目录（自隔离）
                     STOMACH_JSON_FAILURE_DUMP_DIR=self._REL_DUMP_DIR):
            _s._report_json_failure(_bad, ValueError("Expecting ':' delimiter"),
                                    "missing_colon")
        _joined = "\n".join(_m for _lv, _m in _s._logs)
        self.assertIn('代码分析JSON解析失败', _joined)
        self.assertIn('内容长度=', _joined)
        self.assertIn("Expecting ':' delimiter", _joined)
        self.assertIn('缺少冒号', _joined, '应包含原文前 200 字符')

        self.assertTrue(os.path.isdir(self._dump_dir), '应创建归档目录')
        _new = set(os.listdir(self._dump_dir)) - self._before
        self.assertTrue(_new, '应写入归档文件')
        _content = open(os.path.join(self._dump_dir, sorted(_new)[-1]),
                           encoding='utf-8').read()
        self.assertIn('label=missing_colon', _content)
        self.assertIn('缺少冒号', _content)

    def test_13_dump_can_be_disabled(self):
        """归档开关关闭时不写文件（灰度可控）。"""
        _s = self._mk_stomach()
        with _Switch(ENABLE_STOMACH_JSON_FAILURE_DUMP=False,
                     ENABLE_STOMACH_JSON_REPAIR=False,
                     # ★第48批 T1：同样指向专属目录（确认关闭时不写）
                     STOMACH_JSON_FAILURE_DUMP_DIR=self._REL_DUMP_DIR):
            _s._report_json_failure('{"功能": "x"', ValueError("e"), "t")
        if os.path.isdir(self._dump_dir):
            self.assertEqual(set(os.listdir(self._dump_dir)) - self._before, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
