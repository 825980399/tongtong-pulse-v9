# -*- coding: utf-8 -*-
"""
PeriodicTestScheduler.py —— 定期测试调度器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 定期自动测试调度与结果收集
机制: 基于PeriodicTestScheduler类实现，包含10个核心方法
定位: 进化验证层
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any

from nucleus.data.DataAccessLayer import safe_read_json

# 确保项目根在 sys.path，便于复用 tools._framework_probe（独立运行脚本时亦需要）
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

try:
    from tools._framework_probe import _framework_looks_running
except Exception:  # pragma: no cover - 探针不可用时降级为「不在跑」
    def _framework_looks_running() -> bool:  # type: ignore[no-redef]
        return False


class PeriodicTestScheduler:
    """定期测试调度器。"""

    # ★T7+T8修复（P2，2026-09-05）：改为**显式清单注册**，取代按前缀自动发现。
    #
    # 【原实现的问题】
    #   原先用 _LIGHT/_HEAVY/_EXCLUDED_PREFIXES + startswith 自动归类，
    #   意味着任何新放入 tools/ 的 benchmark_*.py / stress_test_*.py
    #   都会**静默自动进入**定期测试集，无需任何人工登记。
    #
    #   这类脚本在框架**正在运行时**跑，属于自扰：
    #   1) benchmark_cython.py 以 CPU 计时比值作判据
    #      （tools/benchmark_cython.py:94 speedup = py_time / cy_time，
    #        :185 sys.exit(0 if _overall_ok else 1)）。
    #      框架满载时计时必被调度抖动污染 → 偶发跌破阈值 → 假失败。
    #      更关键：其 encode 项阈值定的是 ≥2.5x（:122），
    #      但 nucleus/pulse/_frequency_codec_cy.pyx:176-181 的关键词循环
    #      仍是**纯 Python 对象循环**（.lower()/.encode()/list append/sum），
    #      只有 MD5 走 C（:134-142），历史实测仅约 1.1x
    #      （见 benchmark_cython.py:166 的硬编码说明文字）。
    #      → 在装有 Cython 扩展的环境里，该项近乎**恒定失败**。
    #   2) stress_test_* 为压测，会额外占满 CPU；
    #      stress_test_framework_chat.py 还会触发真实 LLM 调用。
    #
    #   假失败会持续掩盖**真回归**——而这正是定期测试存在的全部意义。
    #   实测日志「8通过/1失败」即 5 个 light 全过 + 4 个 heavy 中 1 个失败。
    #
    # 【改为显式清单后的行为】
    #   - 只有清单内的脚本才自动跑；新脚本默认**不进**自动测试集。
    #   - 清单登记了但磁盘上不存在 → 告警，便于清理失效登记。
    #   - 磁盘上出现未登记的重型脚本 → 告警提示人工评估，不再静默纳入。

    # 自动跑：轻量验证脚本（每轮都跑）
    _AUTO_LIGHT: tuple[str, ...] = (
        "verify_phase17_1_5.py",
        "verify_semantic_kernel.py",
    )

    # 自动跑：重负载脚本（低频，默认每 6 个周期跑一次）
    # ★T7：当前**刻意留空**——压测与性能基准一律退出自动集，改人工触发。
    #   理由见上方说明：满载下的 CPU 计时比值判据会产生假失败，
    #   压测脚本则会自扰并触发真实 LLM 调用。
    _AUTO_HEAVY: tuple[str, ...] = ()

    # 已评估为「仅人工运行」的脚本：明确登记，
    # 避免后续被人因不明就里而误加回自动集。
    # ★第82批 T-b：benchmark_hot_cold_faiss_kal.py 为第81批冷热/FAISS/KAL
    #   性能基准（CPU 计时比值 + 压测自扰），按 T7/T8 只应人工触发；
    #   登记后 discover_scripts 不再为它打"未登记重型脚本" WARNING，
    #   但仍归 excluded（不自动跑），安全契约不变。
    _MANUAL_ONLY: tuple[str, ...] = (
        "benchmark_hot_cold_faiss_kal.py",
    )

    # 保留前缀仅用于**识别**新出现的重型脚本并提示登记，不再决定归属
    _HEAVY_HINT_PREFIXES = ("stress_test_", "benchmark_")

    # ★第五批 任务2B：运行期安全白/黑名单。
    #   框架在跑时，只有「白名单」内的脚本（已确认走 TestIsolation 完全隔离）
    #   才允许自动跑；「黑名单」脚本（需停框架、写生产或半隔离）一律跳过，
    #   避免定期测试在框架运行期自扰 / 触发访问违规（0xC0000005）。
    #   当前黑名单留空，作为后续「必须停框架才跑」脚本的登记位。
    _FRAMEWORK_SAFE: tuple[str, ...] = (
        "verify_phase17_1_5.py",
        "verify_semantic_kernel.py",
    )
    _REQUIRE_FRAMEWORK_STOP: tuple[str, ...] = ()

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._tools_dir = os.path.join(project_root, "tools")
        self._results_dir = os.path.join(project_root, "data", "evolution", "test_runs")
        os.makedirs(self._results_dir, exist_ok=True)
        self._run_counter = 0
        # ★7-1/P1-13：计数原子化
        self._counter_lock = threading.Lock()
        # 归档目录若创建失败，后续 _archive_report 写盘会连带失败，
        # 此处置标记以便降级为「不归档」，避免整轮测试因归档异常而丢失结果。
        self._archive_enabled = True

    def _log(self, level: str, message: str) -> None:
        """★R5修复：本类此前**没有** _log 方法，但 190 行（现 2xx 行）调用了它。

         PeriodicTestScheduler 是不继承任何基类的普通类（class 声明见 34 行），
         因此在归档写盘失败时，except 分支的 `self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")`
         会抛出 AttributeError，向上传播到 PulseCodeLearner._maybe_run_periodic_tests
         的 except（DEBUG 级「定期测试异常(忽略)」）被吞掉 ——
         结果是**整轮测试连汇总日志都不会打印**，静默消失，且无任何归档可查。
         此处补上实现：优先走项目统一日志，不可用则降级 print。
        """
        try:
            from nucleus.logger import get_module_logger
            _logger = get_module_logger("PeriodicTestScheduler")
            # ★D1顺带修正：不再手工拼接 "[PeriodicTestScheduler]" 前缀，
            #   get_module_logger 输出的日志头已带模块名，
            #   旧写法会造成 "[PeriodicTestScheduler] [PeriodicTestScheduler] xxx" 重复。
            if str(level).upper() in ("WARNING", "ERROR"):
                _logger.warning(message)
            else:
                _logger.debug(message)
        except Exception:
            # 日志系统不可用时降级为标准输出，绝不让日志调用本身抛异常
            try:
                print(f"[PeriodicTestScheduler][{level}] {message}", flush=True)
            except Exception:
                pass

    # ========== 脚本发现与分类 ==========

    def discover_scripts(self) -> dict[str, list[str]]:
        """
        ★T8修复：按**显式清单**归类（原为按文件名前缀自动发现）。

        返回:
            {
              "light":        [自动跑的轻量验证脚本...],
              "heavy":        [自动跑的重负载脚本...],
              "excluded":     [不自动跑的脚本...],
              "missing":      [清单已登记但磁盘上不存在的脚本...],
              "unregistered": [磁盘上有、未登记、且疑似重型的脚本...],
            }

        说明:
            - 未登记的脚本默认归入 excluded（保守，不自动跑）。
            - missing 与 unregistered 用于暴露「清单与磁盘不一致」，
              让新增脚本需要**显式人工登记**才会进入自动集。
        """
        _light: list[str] = []
        _heavy: list[str] = []
        _excluded: list[str] = []
        _missing: list[str] = []
        _unregistered: list[str] = []

        if not os.path.isdir(self._tools_dir):
            self._log("WARNING",
                      f"定期测试: tools 目录不存在，无法发现脚本: {self._tools_dir}")
            return {"light": _light, "heavy": _heavy, "excluded": _excluded,
                    "missing": _missing, "unregistered": _unregistered}

        _on_disk = {_f for _f in os.listdir(self._tools_dir) if _f.endswith(".py")}

        for _fname in sorted(_on_disk):
            if _fname in self._AUTO_LIGHT:
                _light.append(_fname)
            elif _fname in self._AUTO_HEAVY:
                _heavy.append(_fname)
            else:
                # 未登记 → 保守处理，不自动跑
                _excluded.append(_fname)
                # 疑似重型脚本却未登记 → 提示人工评估
                if _fname.startswith(self._HEAVY_HINT_PREFIXES) \
                        and _fname not in self._MANUAL_ONLY:
                    _unregistered.append(_fname)

        # 清单登记了但磁盘上已不存在 → 提示清理，避免「以为在测其实没测」
        _registered = set(self._AUTO_LIGHT) | set(self._AUTO_HEAVY) | set(self._MANUAL_ONLY)
        _missing = sorted(_f for _f in _registered if _f not in _on_disk)

        if _missing:
            self._log("WARNING",
                      f"定期测试清单登记了磁盘上不存在的脚本(请清理清单): "
                      f"{', '.join(_missing)}")
        if _unregistered:
            self._log("WARNING",
                      f"发现未登记的重型脚本(默认不自动跑,请人工评估后登记): "
                      f"{', '.join(_unregistered)}")

        return {"light": _light, "heavy": _heavy, "excluded": _excluded,
                "missing": _missing, "unregistered": _unregistered}

    # ========== 运行期安全过滤 ==========

    def runtime_safe_scripts(self, scripts: list[str],
                             framework_running: bool | None = None) -> list[str]:
        """★第五批 任务2B：按运行期状态过滤脚本。

        框架在跑时：
          - 仅保留白名单（_FRAMEWORK_SAFE）脚本；
          - 丢弃黑名单（_REQUIRE_FRAMEWORK_STOP）脚本（它们需停框架才跑）。
        框架未跑时：原样返回全部脚本。

        Args:
            scripts: 待跑脚本名列表
            framework_running: None 时自动探测；否则用给定值

        返回:
            实际允许运行的脚本名列表
        """
        if framework_running is None:
            framework_running = _framework_looks_running()
        if not framework_running:
            return list(scripts)
        _safe = set(self._FRAMEWORK_SAFE)
        _blocked = set(self._REQUIRE_FRAMEWORK_STOP)
        return [s for s in scripts if s in _safe and s not in _blocked]

    # ========== 主入口：跑一轮测试 ==========

    def run_cycle(self, include_heavy: bool = False,
                  timeout_light: int = 60,
                  timeout_heavy: int = 300) -> dict[str, Any]:
        """
        跑一轮定期测试。

        Args:
            include_heavy: 是否本轮也跑重负载脚本（默认 False，低频跑）
            timeout_light: 轻量脚本超时（秒）
            timeout_heavy: 重负载脚本超时（秒）

        返回:
            {"passed": bool, "light": {...}, "heavy": {...},
             "total_ran": N, "total_passed": N, "total_failed": N}
        """
        with self._counter_lock:
            self._run_counter += 1
        _scripts = self.discover_scripts()

        # ★第五批 任务2B：框架在跑时，仅跑白名单隔离脚本，跳过黑名单脚本
        _fw = _framework_looks_running()
        _light_allowed = self.runtime_safe_scripts(_scripts.get("light", []), _fw)
        if _fw and set(_scripts.get("light", [])) - set(_light_allowed):
            self._log("WARNING",
                      f"定期测试: 框架运行中，以下轻量脚本被安全策略跳过: "
                      f"{', '.join(sorted(set(_scripts.get('light', [])) - set(_light_allowed)))}")

        # 轻量脚本：每轮都跑
        _light_result = self._run_scripts(_light_allowed, timeout_light)

        # 重负载脚本：低频跑（每 6 轮跑一次，或显式指定）
        _heavy_result = {"passed": True, "results": [], "skipped": True}
        if include_heavy or self._run_counter % 6 == 0:
            _heavy_result = self._run_scripts(
                _scripts.get("heavy", []), timeout_heavy
            )

        # 汇总
        _all_results = _light_result.get("results", []) + _heavy_result.get("results", [])
        _total_ran = len(_all_results)
        _total_passed = sum(1 for r in _all_results if r.get("passed"))
        _total_failed = _total_ran - _total_passed
        _passed = _light_result.get("passed", True) and _heavy_result.get("passed", True)

        _report = {
            "passed": _passed,
            "light": _light_result,
            "heavy": _heavy_result,
            "total_ran": _total_ran,
            "total_passed": _total_passed,
            "total_failed": _total_failed,
            "cycle": self._run_counter,
            "timestamp": time.time(),
        }

        # 归档结果
        self._archive_result(_report)

        return _report

    def _run_scripts(self, scripts: list[str], timeout: int) -> dict[str, Any]:
        """运行一组脚本，返回结果。"""
        _results: list[dict[str, Any]] = []
        _all_passed = True

        for _fname in scripts:
            _script = os.path.join(self._tools_dir, _fname)
            try:
                _proc = subprocess.run(  # 有意不检查子进程退出码
                
                    ["python", _script],
                    check=False, capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                    cwd=self._project_root,
                    # ★P2修复：强制子进程自身用 UTF-8 输出。
                    # 父进程的 encoding="utf-8" 只影响解码子进程输出，救不了子进程内部崩溃。
                    # Windows 上子进程默认 stdout 编码为 GBK(cp936)，测试脚本 print 的 emoji(✅)
                    # 会在子进程写 stdout 时就触发 UnicodeEncodeError → returncode=1 → 误判失败。
                    # 通过 PYTHONIOENCODING=utf-8 让子进程内部也用 UTF-8，彻底消除误报。
                    env={**os.environ,
                         "PYTHONIOENCODING": "utf-8",
                         # ★日志隔离：子进程写入独立测试日志，不混入主进程 pulse.log
                         "PULSE_LOG_FILE": os.path.join(
                             self._results_dir,
                             f"test_{time.strftime('%Y%m%d_%H%M%S')}_{_fname}.log")},
                )
                _passed = _proc.returncode == 0
                _results.append({
                    "script": _fname,
                    "passed": _passed,
                    "returncode": _proc.returncode,
                    "output_tail": ((_proc.stdout or "") + (_proc.stderr or ""))[-500:],
                })
                if not _passed:
                    _all_passed = False
            except subprocess.TimeoutExpired:
                _results.append({
                    "script": _fname, "passed": False,
                    "returncode": -1, "output_tail": f"超时({timeout}s)",
                })
                _all_passed = False
            except Exception as _e:
                _results.append({
                    "script": _fname, "passed": False,
                    "returncode": -2, "output_tail": f"异常: {_e}",
                })
                _all_passed = False

        return {"passed": _all_passed, "results": _results, "skipped": False}

    def _archive_result(self, report: dict[str, Any]) -> None:
        """归档本轮测试结果。"""
        # ★R5：归档失败不应影响测试结果本身（结果已通过返回值交给调用方），
        #   但必须记录具体原因。原实现只写「异常已忽略」，
        #   导致 data/evolution/test_runs/ 目录缺失时完全无法察觉，
        #   事后也没有任何归档可供追溯失败用例。
        if not getattr(self, "_archive_enabled", True):
            return
        _fname = time.strftime("test_run_%Y%m%d_%H%M%S.json")
        _path = os.path.join(self._results_dir, _fname)
        try:
            os.makedirs(self._results_dir, exist_ok=True)
            with open(_path, "w", encoding="utf-8") as _f:
                json.dump(report, _f, ensure_ascii=False, indent=2)
        except Exception as _e:
            self._log("WARNING", f"测试结果归档失败(不影响本轮结论): "
                                 f"path={_path}, err={_e}")

    # ========== 查询 ==========

    def latest_result(self) -> dict[str, Any] | None:
        """读取最近一轮测试结果。"""
        if not os.path.isdir(self._results_dir):
            return None
        _files = sorted(
            [f for f in os.listdir(self._results_dir) if f.endswith(".json")],
            reverse=True,
        )
        if not _files:
            return None
        try:
            return safe_read_json(os.path.join(self._results_dir, _files[0]), default={})
        except Exception as e:
            print(f"[WARNING] PeriodicTestScheduler.py:365: {type(e).__name__}: {e}")
            return None


# ========== 便捷函数 ==========

def get_periodic_test_scheduler(project_root: str) -> PeriodicTestScheduler:
    """获取 PeriodicTestScheduler 实例。"""
    return PeriodicTestScheduler(project_root)


if __name__ == "__main__":
    # 自测：发现脚本 + 跑一轮轻量测试
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _sched = PeriodicTestScheduler(_root)

    print("=== 脚本分类（★T8 显式清单） ===")
    _disc = _sched.discover_scripts()
    print(f"自动跑·轻量({len(_disc['light'])}): {_disc['light']}")
    print(f"自动跑·重负载({len(_disc['heavy'])}): {_disc['heavy']}")
    print(f"不自动跑({len(_disc['excluded'])}): {_disc['excluded']}")
    print(f"清单登记但文件缺失({len(_disc['missing'])}): {_disc['missing']}")
    print(f"未登记的重型脚本({len(_disc['unregistered'])}): {_disc['unregistered']}")

    print("\n=== 跑一轮轻量测试 ===")
    _r = _sched.run_cycle(include_heavy=False)
    print(f"结果: {_r['total_passed']}/{_r['total_ran']} 通过, "
          f"{_r['total_failed']} 失败")
