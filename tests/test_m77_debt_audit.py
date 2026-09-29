# -*- coding: utf-8 -*-
"""主线第77批（InfluxDB真实验证与债务整理）门控测试。

覆盖
----
T1(P0) InfluxDB 凭证链路（token 失效为环境硬阻，此处校验修复仍在位）
T2(P1) git 仓库完整性 + 损坏备份已清理
T3(P1) 本地修复规则 bare_return_none_in_except 仍在生效
T4(P2) 债务摸底结论 + 本批顺手修复（ssrf_guard 静默点 / 过期注释更正）

★测真实源码与真实行为；环境不可用时 skip，不伪造通过。
"""
import io
import os
import re
import subprocess
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SSRF = os.path.join(_ROOT, "nucleus", "ssrf_guard.py")
_MAIN = os.path.join(_ROOT, "main.py")
_DS = os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py")
_EXEC = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_STORE = os.path.join(_ROOT, "nucleus", "timeseries_store", "influxdb_store.py")
_VERIFY = os.path.join(_ROOT, "tools", "verify_write_only_e2e.py")


def _rd(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def _git(args):
    r = subprocess.run(["git"] + args, cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


# =====================================================================
# T1 · InfluxDB 凭证链路（修复仍在位）
# =====================================================================
class TestT1InfluxCredential(unittest.TestCase):
    def setUp(self):
        self.store = _rd(_STORE)
        self.verify = _rd(_VERIFY)

    def test_01_credential_check_still_present(self):
        """第76批补的凭证校验不得回退。"""
        self.assertIn("INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", self.store)

    def test_02_check_inside_connect(self):
        seg = self.store[self.store.find("def connect("):self.store.find("def close(")]
        self.assertIn("凭证校验失败", seg)
        self.assertIn("_available = False", seg)

    def test_03_default_enabled(self):
        self.assertIn('"INFLUXDB_VERIFY_CREDENTIAL_ON_CONNECT", True', self.store)

    def test_04_real_writer_fixes_present(self):
        """verify 脚本 real 模式修复（适配器 + 时序）不得回退。"""
        self.assertIn("class RealStoreAdapter", self.verify)
        seg = self.verify[self.verify.find("def _build_real_writer("):
                          self.verify.find("def run(")]
        self.assertGreater(seg.find("ENABLE_INFLUXDB_TIMESERIES = True"), -1)
        self.assertGreater(seg.find("store.connect()"),
                           seg.find("store = get_influxdb_store()"))

    def test_05_write_failure_safe(self):
        """写入失败（不可用态）不抛异常——主流程安全。"""
        try:
            from nucleus.timeseries_store.influxdb_store import InfluxDBStore
        except Exception:
            self.skipTest("influxdb_store 不可导入")
        import threading
        s = InfluxDBStore.__new__(InfluxDBStore)
        s._lock = threading.RLock()
        s._buffer = []
        s._available = False
        s._write_api = None
        s._client_missing = True
        self.assertFalse(s.node_activated("x", "L1", "t"))
        self.assertEqual(s.query_count("node_activated"), 0)


# =====================================================================
# T2 · git 仓库完整性 + 损坏备份清理
# =====================================================================
class TestT2GitRepo(unittest.TestCase):
    def test_10_repo_valid(self):
        rc, so, _ = _git(["rev-parse", "--is-inside-work-tree"])
        self.assertEqual(rc, 0)
        self.assertEqual(so, "true")

    def test_11_has_commit(self):
        rc, so, _ = _git(["log", "--oneline", "-1"])
        self.assertEqual(rc, 0)
        self.assertTrue(so)

    def test_12_no_remote(self):
        """约束：只本地操作，不得推送 —— 第96批 相关任务 改为**白名单守护**。

        原断言要求 ``git remote -v`` 恒为空；第94批内部协作者主动配置了 gitee 远程
        ⇒ 必红（D95-8）。改为「为空 或 仅白名单主机」，保留「自动化不得乱配
        远程」的守护意图（与 m76::test_37 同口径）。
        """
        _ALLOWED_HOSTS = ("gitee.com",)
        _, so, _ = _git(["remote", "-v"])
        for line in [x for x in so.splitlines() if x.strip()]:
            parts = line.split()
            url = parts[1] if len(parts) > 1 else ""
            self.assertTrue(any(h in url for h in _ALLOWED_HOSTS),
                            "远程不在白名单: %s" % url)

    def test_13_corrupt_backup_removed(self):
        """★T2 验收：损坏的 .bak_git76/ 已清理。"""
        self.assertFalse(os.path.isdir(os.path.join(_ROOT, ".bak_git76")),
                         ".bak_git76/ 仍未清理")

    def test_14_batch_backup_kept(self):
        """批次备份作为回滚基线予以保留。"""
        self.assertTrue(os.path.isdir(os.path.join(_ROOT, ".bak_batch77")))

    def test_15_ignore_rules_effective(self):
        for path in ("data", "logs", "__pycache__", ".env"):
            rc, so, _ = _git(["check-ignore", "-v", path])
            self.assertEqual(rc, 0, "%s 未被忽略" % path)
            self.assertTrue(so)

    def test_16_source_tracked(self):
        _, so, _ = _git(["ls-files"])
        files = [x for x in so.splitlines() if x.strip()]
        self.assertGreater(len(files), 500)
        self.assertIn("config.py", files)

    def test_17_no_sensitive_tracked(self):
        _, so, _ = _git(["ls-files"])
        bad = [x for x in so.splitlines()
               if x.startswith(("data/", "logs/")) or "__pycache__" in x
               or x.startswith(".bak")]
        self.assertEqual(bad, [], "敏感/数据被提交: %s" % bad[:5])


# =====================================================================
# T3 · 本地修复规则仍在生效
# =====================================================================
class TestT3LocalFixRule(unittest.TestCase):
    def setUp(self):
        self.src = _rd(_EXEC)
        start = self.src.find('elif "bare_return_none_in_except" in plan_type:')
        self.branch = None
        if start != -1:
            end = self.src.find("\n        else:", start)
            seg = self.src[start:end if end != -1 else len(self.src)]
            self.branch = seg.replace(
                'elif "bare_return_none_in_except" in plan_type:', "if True:", 1)

    def _apply(self, original, switch=True):
        import config as real_cfg
        old = getattr(real_cfg, "ENABLE_LOCAL_FIX_BARE_RETURN_NONE", True)
        real_cfg.ENABLE_LOCAL_FIX_BARE_RETURN_NONE = switch
        try:
            ns = {"original_code": original,
                  "plan_type": "bare_return_none_in_except",
                  "modified_code": original,
                  "_applied_strategy": ""}
            exec(textwrap.dedent(self.branch), ns)
            return ns["modified_code"], ns["_applied_strategy"]
        finally:
            real_cfg.ENABLE_LOCAL_FIX_BARE_RETURN_NONE = old

    def test_20_type_registered(self):
        self.assertIn('"bare_return_none_in_except",', self.src)

    def test_21_branch_present(self):
        self.assertIsNotNone(self.branch)

    def test_22_adds_log_with_as(self):
        code = ("    def f(self):\n        try:\n            pass\n"
                "        except Exception as e:\n            return None\n")
        out, strat = self._apply(code)
        self.assertNotEqual(out, code)
        self.assertIn("self._log(LogLevel.WARNING", out)
        self.assertEqual(strat, "bare_return_none_add_log")

    def test_23_no_match_without_as(self):
        code = ("    def f(self):\n        try:\n            pass\n"
                "        except Exception:\n            return None\n")
        out, strat = self._apply(code)
        self.assertEqual(out, code)
        self.assertEqual(strat, "bare_return_none_no_match")

    def test_24_switch_off(self):
        code = ("    def f(self):\n        try:\n            pass\n"
                "        except Exception as e:\n            return None\n")
        out, strat = self._apply(code, switch=False)
        self.assertEqual(out, code)
        self.assertEqual(strat, "bare_return_none_disabled")


# =====================================================================
# T4 · 顺手修复 + 摸底结论固化
# =====================================================================
class TestT4SsrfAndComments(unittest.TestCase):
    def setUp(self):
        self.ssrf = _rd(_SSRF)
        self.main = _rd(_MAIN)
        self.ds = _rd(_DS)

    def test_30_ssrf_has_logger(self):
        """★顺手修：ssrf_guard 引入 logger，不再静默。"""
        self.assertIn("_LOGGER = logging.getLogger(__name__)", self.ssrf)
        self.assertIn("import logging", self.ssrf)

    def test_31_ssrf_logs_on_failure(self):
        self.assertIn("受信任主机集合解析失败", self.ssrf)
        self.assertIn("_LOGGER.debug(", self.ssrf)

    def test_32_ssrf_no_silent_except(self):
        """该文件不得再有 `except ...: pass`。"""
        lines = self.ssrf.splitlines()
        hits = [i + 1 for i, l in enumerate(lines)
                if re.search(r"except[^:]*:\s*$", l.rstrip())
                and i + 1 < len(lines) and lines[i + 1].strip() == "pass"]
        self.assertEqual(hits, [], "ssrf_guard 仍有静默 except: %s" % hits)

    def test_33_ssrf_fail_closed_semantics(self):
        """★fail-closed 语义不变：异常时受信任集合退化为最小集合。"""
        try:
            import nucleus.ssrf_guard as sg
        except Exception:
            self.skipTest("ssrf_guard 不可导入")
        from unittest import mock
        sg._TRUSTED_CACHE = None
        with mock.patch.object(sg, "urlparse", side_effect=ValueError("boom")):
            with mock.patch.object(sg, "_LOGGER") as lg:
                hosts = sg._trusted_hosts()
        sg._TRUSTED_CACHE = None
        # 最小集合仍然包含本地回环（不是空集合、也不是全放行）
        self.assertIn("localhost", hosts)
        self.assertIn("127.0.0.1", hosts)
        # 且必须留下日志
        self.assertTrue(lg.debug.called, "异常路径未记录日志")

    def test_34_main_comment_corrected(self):
        """过期的「未接线」注释已更正。"""
        self.assertIn("第77批摸底更正", self.main)

    def test_35_daily_scheduler_comment_corrected(self):
        self.assertIn("第77批摸底更正", self.ds)

    def test_36_daily_scheduler_is_wired(self):
        """★摸底结论：DailyScheduler 确实已被 main.py 接线。"""
        self.assertIn("start_daily_schedule", self.main)

    def test_37_two_analyzers_wired(self):
        """★摸底结论：两维分析器确实已注册。"""
        self.assertIn('"call_graph"', self.main)
        self.assertIn('"knowledge_quality"', self.main)

    def test_38_silent_except_scan_reproducible(self):
        """摸底口径可复现：全库静默 except 数量级应 >0（债务真实存在）。"""
        pat = re.compile(r"except[^:]*:\s*$")
        total = 0
        for dp, dns, fns in os.walk(_ROOT):
            rel = os.path.relpath(dp, _ROOT).replace("\\", "/")
            if rel == ".":
                rel = ""
            if rel and (rel.startswith(("backups/", ".release-tmp/", "tmp/", "data/"))
                        or any(s.startswith(".bak") for s in rel.split("/"))):
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d != "__pycache__" and not d.startswith(".bak")]
            for f in fns:
                if not f.endswith(".py"):
                    continue
                try:
                    ls = io.open(os.path.join(dp, f), encoding="utf-8",
                                 errors="replace").read().replace("\r\n", "\n").splitlines()
                except OSError:
                    continue
                for i, l in enumerate(ls):
                    if pat.search(l.rstrip()) and i + 1 < len(ls) \
                            and ls[i + 1].strip() == "pass":
                        total += 1
        self.assertGreater(total, 100, "静默 except 债务量级异常: %d" % total)


if __name__ == "__main__":
    unittest.main(verbosity=2)
