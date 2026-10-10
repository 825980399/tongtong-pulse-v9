# -*- coding: utf-8 -*-
"""主线第76批（代码质量治理与git初始化）门控测试。

覆盖
----
T1(P0) PulseLiver 静默 except 清理（5 处 → 带日志）
T2(P0) InfluxDB 真实端到端：凭证校验 + verify 脚本 real 模式修复
T3(P1) 本地修复能力：bare_return_none_in_except 规则
T4(P1) git 仓库初始化 + 首次提交内容安全

★测真实源码（切片 exec / 源码扫描 / 真实 subprocess），源码回退即失败。
"""
import io
import os
import re
import subprocess
import sys
import textwrap
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LIVER = os.path.join(_ROOT, "organs", "body", "PulseLiver.py")
_STORE = os.path.join(_ROOT, "nucleus", "timeseries_store", "influxdb_store.py")
_VERIFY = os.path.join(_ROOT, "tools", "verify_write_only_e2e.py")
_EXEC = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")


def _rd(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def _git(args):
    r = subprocess.run(["git"] + args, cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


# =====================================================================
# T1 · PulseLiver 静默 except
# =====================================================================
class TestT1LiverSilentExcept(unittest.TestCase):
    def setUp(self):
        self.src = _rd(_LIVER)
        self.lines = self.src.splitlines()

    def _silent_hits(self):
        """全文件 `except ...:` 后紧跟 pass 的行。"""
        out = []
        for i, ln in enumerate(self.lines):
            if re.search(r"except[^:]*:\s*$", ln.rstrip()):
                if i + 1 < len(self.lines) and self.lines[i + 1].strip() == "pass":
                    out.append(i + 1)
        return out

    def test_01_no_silent_except_pass(self):
        """★全文件不得再有 `except ...: pass`（与 m42 门控同口径）。"""
        self.assertEqual(self._silent_hits(), [],
                         "PulseLiver.py 仍存在静默吞异常: {}".format(self._silent_hits()))

    def test_02_all_five_now_log_exception(self):
        """★原 5 处均改为 `except Exception as e:`。"""
        n = len(re.findall(r"except Exception as e:", self.src))
        self.assertGreaterEqual(n, 5, "应至少 5 处带异常变量的 except，实际 %d" % n)

    def test_03_logs_contain_exception_type_and_msg(self):
        """日志必须含类型与异常对象（禁止只记类型）。"""
        logs = re.findall(r"self\._log\(LogLevel\.WARNING, f\"\[肝\][^\n]*", self.src)
        self.assertGreaterEqual(len(logs), 5)
        for _l in logs:
            self.assertIn("type(e).__name__", _l)
            self.assertIn("{e}", _l)

    def test_04_five_expected_messages_present(self):
        """5 处修复点的日志文案均存在。"""
        for kw in ("KAL获取节点异常", "KAL节点统计异常", "节点池统计异常",
                   "直连获取节点异常", "KAL查询异常"):
            self.assertIn(kw, self.src, "缺少日志文案: {}".format(kw))

    def test_05_control_flow_preserved(self):
        """★容错行为不变：except 之后仍走回退/返回 None。"""
        self.assertIn("return self._m70_direct_get_node(node_id)", self.src)
        self.assertIn("return 0", self.src)
        self.assertIn("return None", self.src)

    def test_06_loglevel_still_imported(self):
        self.assertIn("LogLevel", self.src)
        self.assertTrue(re.search(r"^\s*LogLevel,", self.src, re.M),
                        "LogLevel 应从 nucleus.const 导入")

    def test_07_no_new_bare_except(self):
        """不得新引入裸 `except:`（宽口径）。"""
        hits = [i + 1 for i, l in enumerate(self.lines)
                if re.match(r"^\s*except\s*:\s*$", l)]
        self.assertEqual(hits, [], "出现裸 except: {}".format(hits))


# =====================================================================
# T2 · InfluxDB 凭证校验 + verify 脚本
# =====================================================================
class TestT2InfluxCredential(unittest.TestCase):
    def setUp(self):
        self.store_src = _rd(_STORE)
        self.verify_src = _rd(_VERIFY)

    def test_10_credential_check_present(self):
        """★connect() 必须做凭证校验（修「connect 成功但写入静默失败」假象）。"""
        self.assertIn("INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", self.store_src)

    def test_11_check_is_inside_connect(self):
        seg = self.store_src[self.store_src.find("def connect("):
                             self.store_src.find("def close(")]
        self.assertIn("INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", seg)
        # 校验失败必须置不可用并记日志
        self.assertIn("_available = False", seg)
        self.assertIn("_write_api = None", seg)
        self.assertIn("凭证校验失败", seg)

    def test_12_verify_default_true(self):
        """校验开关默认开启（零配置即生效）。"""
        self.assertIn('"INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", True', self.store_src)

    def test_13_switch_default_from_module(self):
        """运行时口径：_cfg 默认取 True。"""
        try:
            import nucleus.timeseries_store.influxdb_store as _m
        except Exception:
            self.skipTest("influxdb_store 不可导入")
        self.assertTrue(_m._cfg("INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", True))

    def test_14_write_failure_does_not_raise(self):
        """★写入失败（不可用态）不得抛异常——主流程安全。"""
        try:
            from nucleus.timeseries_store.influxdb_store import InfluxDBStore
        except Exception:
            self.skipTest("influxdb_store 不可导入")
        _s = InfluxDBStore.__new__(InfluxDBStore)
        _s._lock = threading.RLock()
        _s._buffer = []
        _s._available = False
        _s._write_api = None
        _s._client_missing = True
        self.assertFalse(_s.node_activated("x", "L1", "t"))
        self.assertFalse(_s.node_modified("x", "f", "a", "b"))
        self.assertFalse(_s.node_accessed("x", "t"))
        self.assertEqual(_s.query_latest("node_activated"), [])
        self.assertEqual(_s.query_count("node_activated"), 0)

    def test_15_verify_script_has_adapter(self):
        """真实 store 无 counts 属性 → 必须有适配器。"""
        self.assertIn("class RealStoreAdapter", self.verify_src)

    def test_16_verify_real_opens_switch_first(self):
        """★时序修正：先开开关再 connect（原实现必然降级 mock）。"""
        seg = self.verify_src[self.verify_src.find("def _build_real_writer("):
                              self.verify_src.find("def run(")]
        i_switch = seg.find("ENABLE_INFLUXDB_TIMESERIES = True")
        # ★精确形态：docstring 里也出现过 "get_influxdb_store()" 字样，
        #   只找调用点必须带上赋值前缀，否则会命中文档字符串（文本扫描经典坑）。
        i_get = seg.find("store = get_influxdb_store()")
        i_conn = seg.find("store.connect()")
        self.assertGreater(i_switch, -1, "未打开开关")
        self.assertGreater(i_get, i_switch, "取单例必须晚于开开关")
        self.assertGreater(i_conn, i_get, "connect 必须晚于取单例")

    def test_17_verify_returns_adapter(self):
        seg = self.verify_src[self.verify_src.find("def _build_real_writer("):
                              self.verify_src.find("def run(")]
        self.assertIn("return RealStoreAdapter(store)", seg)


# =====================================================================
# T3 · 本地修复能力 bare_return_none_in_except
# =====================================================================
class TestT3LocalFixRule(unittest.TestCase):
    def setUp(self):
        self.src = _rd(_EXEC)
        self.branch = self._extract_branch()

    def _extract_branch(self):
        start = self.src.find('elif "bare_return_none_in_except" in plan_type:')
        if start == -1:
            return None
        end = self.src.find("\n        else:", start)
        seg = self.src[start:end if end != -1 else len(self.src)]
        # elif 不能独立 exec → 改为 if True
        return seg.replace('elif "bare_return_none_in_except" in plan_type:',
                           "if True:", 1)

    def _apply(self, original, switch=True):
        """★真实执行源码分支（切片 exec），不是复刻逻辑。"""
        # ★分支内部是 `import config as _cfg_brne`，绑定的是**真实** config 模块
        #   （sys.modules 缓存），所以开关必须改真实模块属性 ——
        #   往 ns 里注入假 config 是无效的（import 会重新绑定名字）。
        import config as _real_cfg
        _old = getattr(_real_cfg, "ENABLE_LOCAL_FIX_BARE_RETURN_NONE", True)
        _real_cfg.ENABLE_LOCAL_FIX_BARE_RETURN_NONE = switch
        try:
            ns = {
                "original_code": original,
                "plan_type": "bare_return_none_in_except",
                "modified_code": original,
                "_applied_strategy": "",
            }
            exec(textwrap.dedent(self.branch), ns)
            return ns["modified_code"], ns["_applied_strategy"]
        finally:
            _real_cfg.ENABLE_LOCAL_FIX_BARE_RETURN_NONE = _old

    def test_20_type_in_local_fixable(self):
        self.assertIn('"bare_return_none_in_except",', self.src)

    def test_21_branch_exists(self):
        self.assertIsNotNone(self.branch, "未找到本地修复分支")

    def test_22_adds_log_when_as_present(self):
        """带 `as e` 的 except + 裸 return None → 补日志。"""
        code = "    def f(self):\n        try:\n            pass\n        except Exception as e:\n            return None\n"
        out, strat = self._apply(code)
        self.assertNotEqual(out, code)
        self.assertIn("self._log(LogLevel.WARNING", out)
        self.assertIn("type(e).__name__", out)
        self.assertIn("return None", out)
        self.assertEqual(strat, "bare_return_none_add_log")

    def test_23_no_match_without_as(self):
        """★保守：无 `as` 时绝不臆造变量名。"""
        code = "    def f(self):\n        try:\n            pass\n        except Exception:\n            return None\n"
        out, strat = self._apply(code)
        self.assertEqual(out, code)
        self.assertEqual(strat, "bare_return_none_no_match")

    def test_24_switch_off_disables(self):
        code = "    def f(self):\n        try:\n            pass\n        except Exception as e:\n            return None\n"
        out, strat = self._apply(code, switch=False)
        self.assertEqual(out, code)
        self.assertEqual(strat, "bare_return_none_disabled")

    def test_25_preserves_return_none(self):
        """★不得改变返回值语义（仍是 return None）。"""
        code = "    def f(self):\n        try:\n            pass\n        except ValueError as ve:\n            return None\n"
        out, _ = self._apply(code)
        self.assertIn("return None", out)
        self.assertIn("except ValueError as ve:", out)

    def test_26_no_match_when_already_logged(self):
        """已有日志语句时不重复改（except 后不是直接 return None）。"""
        code = ("    def f(self):\n        try:\n            pass\n"
                "        except Exception as e:\n            self._log(LogLevel.WARNING, \"x\")\n"
                "            return None\n")
        out, strat = self._apply(code)
        self.assertEqual(out, code)
        self.assertEqual(strat, "bare_return_none_no_match")


# =====================================================================
# T4 · git 初始化 + 提交安全
# =====================================================================
class TestT4GitInit(unittest.TestCase):
    def test_30_repo_is_valid(self):
        rc, so, _ = _git(["rev-parse", "--is-inside-work-tree"])
        self.assertEqual(rc, 0, "git 仓库无效: {}".format(so))
        self.assertEqual(so.strip(), "true")

    def test_31_has_commit(self):
        rc, so, _ = _git(["log", "--oneline", "-1"])
        self.assertEqual(rc, 0)
        self.assertTrue(so.strip(), "无任何提交")

    def test_32_commit_message_baseline(self):
        """★第96批 T-96g（D95-8）：原断言**最新提交**的 message 含「初始提交」。

        第94批星轨在 HEAD 上新增了 commit（「第94批事故修复…」）⇒ HEAD 不再是
        初始提交，该断言失去意义。改为**相对基线**：取 **root commit**
        （``git rev-list --max-parents=0 HEAD``）的 message —— 不依赖 HEAD，
        无论后续再有多少批次提交都不会失配；第156批 B156-1 重锚：历史已把
        原始「初始提交」压入「第102~116批技术债务清偿与框架完善」这一基始提交，
        故锚定子串由「初始提交」改为「技术债务清偿」，仍守护「仓库由基始提交建立」这一事实。
        """
        rc, so, _ = _git(["rev-list", "--max-parents=0", "HEAD"])
        self.assertEqual(rc, 0)
        _roots = [x for x in so.splitlines() if x.strip()]
        self.assertTrue(_roots, "无 root commit（git log 为空？）")
        rc2, so2, _ = _git(["log", "-1", "--pretty=%s", _roots[-1]])
        self.assertEqual(rc2, 0)
        self.assertIn("技术债务清偿", so2, "root commit 不是预期基始提交: {!r}".format(so2[:120]))

    def test_33_data_not_tracked(self):
        rc, so, _ = _git(["ls-files", "data"])
        self.assertEqual(so.strip(), "", "data/ 被提交: {}".format(so[:200]))

    def test_34_logs_not_tracked(self):
        rc, so, _ = _git(["ls-files", "logs"])
        self.assertEqual(so.strip(), "", "logs/ 被提交: {}".format(so[:200]))

    def test_35_no_pycache_tracked(self):
        _, so, _ = _git(["ls-files"])
        bad = [x for x in so.splitlines() if "__pycache__" in x]
        self.assertEqual(bad, [], "__pycache__ 被提交: {}".format(bad[:5]))

    def test_36_no_bak_tracked(self):
        _, so, _ = _git(["ls-files"])
        bad = [x for x in so.splitlines() if x.startswith(".bak")]
        self.assertEqual(bad, [], "备份目录被提交: {}".format(bad[:5]))

    def test_37_no_remote_configured(self):
        """★约束6（第96批 T-96g 修订）：**不得由自动化流程擅自配置远程**。

        原断言要求 ``git remote -v`` **恒为空**。第94批星轨**主动**配置了
        origin（gitee.com/tongtongkaiyuan/tongtong-pulse-v9.git）作为备份远程，
        该约束事实上已被人类决策解除 ⇒ 原断言必红（D95-8）。
        ⇒ 改为**白名单守护**：远程为空，或仅指向白名单主机。
        仍守护原始意图（自动化不得乱配远程、不得偷偷指向未知第三方），
        同时不违背星轨的显式决策。
        """
        _ALLOWED_HOSTS = ("gitee.com", "openi.pcl.ac.cn", "github.com")
        rc, so, _ = _git(["remote", "-v"])
        lines = [x for x in so.splitlines() if x.strip()]
        for line in lines:
            parts = line.split()
            url = parts[1] if len(parts) > 1 else ""
            self.assertTrue(
                any(h in url for h in _ALLOWED_HOSTS),
                "远程不在白名单 {}（约束：自动化不得乱配远程）: {}".format(_ALLOWED_HOSTS, url))

    def test_38_gitignore_covers_keys(self):
        gi = _rd(os.path.join(_ROOT, ".gitignore"))
        for kw in ("data/", "logs/", "__pycache__", ".env"):
            self.assertIn(kw, gi, ".gitignore 缺少 {}".format(kw))

    def test_39_source_is_tracked(self):
        """源码确实被纳入版本管理（不是空提交）。"""
        _, so, _ = _git(["ls-files"])
        files = [x for x in so.splitlines() if x.strip()]
        self.assertGreater(len(files), 500, "跟踪文件过少: %d" % len(files))
        self.assertIn("config.py", files)


if __name__ == "__main__":
    unittest.main(verbosity=2)
