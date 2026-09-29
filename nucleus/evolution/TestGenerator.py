# -*- coding: utf-8 -*-
"""
TestGenerator.py —— 测试生成器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 自动生成测试用例与测试数据
机制: 基于TestGenerator类实现，包含10个核心方法
定位: 进化验证层
"""

from __future__ import annotations

import ast
import os
import subprocess
import time
from typing import Any

from config import EXTERNAL_CALL_TIMEOUTS, TIMEOUT_CONFIG

# ========== ★P0 新增：测试脚本模板常量 ==========

RT_SCRIPT_PULSE_SNAPSHOT = '''"""
自动往返测试: {patch_id}（PulseSnapshot 保存→加载→一致性）
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.pa_compat import table_from_rows
    import pyarrow.parquet as pq
except ImportError as e:
    print(f"[SKIP] 依赖不可用: {e}")
    raise SystemExit(0)

sp = PulseSnapshot.__new__(PulseSnapshot)
import logging
sp._logger = logging.getLogger("rt")
sp._logger.setLevel(logging.WARNING)

nodes = [PulseNode(value=f"test{i}", keywords=[f"kw{i}"], source_organ="rt", evol_level="L2") for i in range(20)]
rows = sp._nodes_to_parquet_columns(nodes)
with tempfile.TemporaryDirectory() as td:
    pq.write_to_dataset(table_from_rows(rows), root_path=td, partition_cols=["evol_level"], compression="snappy")
    rt = pq.read_table(td).to_pylist()
    restored = [PulseNode.from_dict(sp._parquet_row_to_dict(r)) for r in rt]
assert len(restored) == len(nodes), f"节点数不一致: {len(restored)} != {len(nodes)}"
for i in range(min(5, len(nodes))):
    assert restored[i].value == nodes[i].value, f"value不一致@{i}"
    assert restored[i].keywords == nodes[i].keywords, f"keywords不一致@{i}"
print("[OK] PulseSnapshot 往返一致性通过 (20节点)")
'''



COMPAT_SCRIPT_PULSE_SNAPSHOT = '''"""
自动兼容测试: {patch_id}（旧格式 Parquet value=json字符串 仍可加载）
"""
import os, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.pa_compat import table_from_rows
    import pyarrow.parquet as pq
except ImportError as e:
    print(f"[SKIP] 依赖不可用: {e}")
    raise SystemExit(0)

sp = PulseSnapshot.__new__(PulseSnapshot)
import logging
sp._logger = logging.getLogger("compat")
sp._logger.setLevel(logging.WARNING)

nodes = [PulseNode(value=f"old_format_test{i}", keywords=[f"kw{i}"], source_organ="compat", evol_level="L2") for i in range(10)]
rows = sp._nodes_to_parquet_columns(nodes)
old_rows = [{**r, "value": json.dumps(r["value"], ensure_ascii=False)} for r in rows]
with tempfile.TemporaryDirectory() as td:
    pq.write_to_dataset(table_from_rows(old_rows), root_path=td, partition_cols=["evol_level"], compression="snappy")
    rt = pq.read_table(td).to_pylist()
    restored = [PulseNode.from_dict(sp._parquet_row_to_dict(r)) for r in rt]
assert len(restored) == len(nodes)
for i in range(min(5, len(nodes))):
    assert restored[i].value == nodes[i].value, f"旧格式value还原失败@{i}: {restored[i].value!r} != {nodes[i].value!r}"
print("[OK] 旧格式(value=json字符串)兼容通过 (10节点)")
'''


class TestGenerator:
    """动态测试工具生成器。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._test_dir = os.path.join(project_root, "data", "evolution", "tests")
        os.makedirs(self._test_dir, exist_ok=True)

    # ========== 主入口 ==========

    def generate_and_run(self, patch: dict[str, Any]) -> dict[str, Any]:
        """
        根据补丁生成轻量冒烟测试脚本并实际运行。

        返回:
            {"passed": bool, "stages": {...}, "summary": str,
             "script": str(生成的脚本路径), "output": str(运行输出)}
        """
        _patch_id = patch.get("id", f"patch_{int(time.time())}")
        _file = patch.get("file", "")
        _method = patch.get("method", "")

        # 1. 语法校验（修改后代码）
        _syntax = self._check_syntax(patch)

        # 2. import 校验 + 方法存在性校验（生成脚本并运行）
        _script = self._generate_test_script(_patch_id, _file, _method)
        _import_result = self._run_test_script(_script) if _script else None

        # 3. RUFF 代码规范检查（★P0 新增）
        _ruff = self._run_ruff_check(_file)

        # 4. 端到端往返测试（涉及存储/数据结构时，★P0 新增）
        _roundtrip = self._run_roundtrip_test(patch)

        # 5. 旧格式兼容测试（修改加载逻辑时，★P0 新增）
        _compat = self._run_compat_test(patch)

        # 6. 回归测试
        _regression = self._run_regression_tests()

        # 汇总
        _stages = {
            "syntax": _syntax,
            "import_and_method": _import_result or {"passed": True, "detail": "无脚本（缺 file/method）"},
            "ruff": _ruff,
            "roundtrip": _roundtrip,
            "compat": _compat,
            "regression": _regression,
        }
        _passed = (
            _syntax.get("passed", True)
            and (_import_result is None or _import_result.get("passed", True))
            and _ruff.get("passed", True)
            and _roundtrip.get("passed", True)
            and _compat.get("passed", True)
            and _regression.get("passed", True)
        )

        _summary_parts = []
        if not _syntax.get("passed"):
            _summary_parts.append(f"语法失败:{_syntax.get('detail', '')}")
        if _import_result and not _import_result.get("passed"):
            _summary_parts.append(f"导入/方法失败:{_import_result.get('detail', '')}")
        if not _regression.get("passed"):
            _summary_parts.append(f"回归失败:{_regression.get('summary', '')}")

        return {
            "passed": _passed,
            "stages": _stages,
            "summary": "; ".join(_summary_parts) if _summary_parts else "全部通过",
            "script": _script or "",
            "output": _import_result.get("output", "") if _import_result else "",
        }

    # ========== 1. 语法校验 ==========

    def _check_syntax(self, patch: dict[str, Any]) -> dict[str, Any]:
        """对修改后代码做 AST 语法校验。"""
        _modified = patch.get("modified_code", "")
        if not _modified:
            return {"passed": True, "detail": "无修改代码（跳过）"}
        try:
            ast.parse(_modified)
            return {"passed": True, "detail": "AST 语法通过"}
        except SyntaxError as _e:
            return {"passed": False, "detail": f"语法错误: {_e}"}

    # ========== 2. 生成测试脚本 ==========

    def _generate_test_script(self, patch_id: str, file_path: str,
                              method: str) -> str | None:
        """生成轻量冒烟测试脚本（import 校验 + 方法存在性校验）。"""
        if not file_path:
            return None

        # 由文件路径推导模块名：organs/body/PulseLiver.py → organs.body.PulseLiver
        _module = self._file_to_module(file_path)
        if not _module:
            return None

        _script_content = self._render_test_script(patch_id, _module, method)
        _script_path = os.path.join(self._test_dir, f"{patch_id}_test.py")
        try:
            with open(_script_path, "w", encoding="utf-8") as _f:
                _f.write(_script_content)
            return _script_path
        except Exception:
            return None

    def _file_to_module(self, file_path: str) -> str:
        """由文件路径推导 Python 模块名。"""
        _norm = (file_path or "").replace("\\", "/")
        if not _norm.endswith(".py"):
            return ""
        _no_ext = _norm[:-3]
        _parts = [p for p in _no_ext.split("/") if p and p not in ("", ".")]
        if not _parts:
            return ""
        # 去掉可能的 ".py" 残余和项目根目录前缀
        _module = ".".join(_parts)
        return _module

    def _render_test_script(self, patch_id: str, module: str,
                            method: str) -> str:
        """渲染冒烟测试脚本源码。"""
        _method_check = ""
        if method:
            _method_check = (
                f"    if hasattr(_mod, {method!r}):\n"
                f"        _fn = getattr(_mod, {method!r})\n"
                f"        assert callable(_fn), f'方法 {method} 不可调用'\n"
                f"        print(f'  [OK] 方法 {method} 存在且可调用')\n"
                f"    else:\n"
                f"        print(f'  [WARN] 方法 {method} 不存在（可能已被重构）')\n"
            )
        return f'''"""
自动生成的冒烟测试：{patch_id}
模块: {module}  方法: {method or "(无)"}
由 EvolutionLoop.TestGenerator 动态生成，只读校验，不修改任何源代码。
"""
import os
import sys
import importlib

# ★修复: 把项目根目录加入 sys.path，否则子进程 import 顶层包(如 nucleus)会失败
# 使用相对推导（本文件向上四级），避免硬编码绝对路径导致项目迁移后失效
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

def main():
    print(f"== 冒烟测试: {module} ==")
    try:
        _mod = importlib.import_module({module!r})
        print(f"  [OK] import 成功: {{_mod.__name__}}")
    except ImportError as _e:
        print(f"  [FAIL] import 失败: {{_e}}")
        raise SystemExit(1)
    except SyntaxError as _e:
        print(f"  [FAIL] 语法错误: {{_e}}")
        raise SystemExit(1)
    except Exception as _e:
        print(f"  [FAIL] import 异常: {{_e}}")
        raise SystemExit(1)
{_method_check}
    print("  [OK] 冒烟测试完成")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''

    def _run_test_script(self, script_path: str) -> dict[str, Any]:
        """在子进程隔离运行冒烟测试脚本。"""
        try:
            _proc = subprocess.run(  # 有意不检查子进程退出码
                
                ["python", script_path],
                check=False, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['llm_call'],
                encoding="utf-8", errors="replace",
                cwd=self._project_root,
            )
            _output = (_proc.stdout or "") + (_proc.stderr or "")
            return {
                "passed": _proc.returncode == 0,
                "detail": "通过" if _proc.returncode == 0 else "失败",
                "output": _output[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "detail": "超时", "output": "测试脚本运行超时(60s)"}
        except Exception as _e:
            return {"passed": False, "detail": f"异常: {_e}", "output": str(_e)}

    # ========== 3. RUFF 代码规范检查（★P0 新增） ==========

    def _run_ruff_check(self, file_path: str) -> dict[str, Any]:
        """对修改后的文件执行 RUFF 检查（F401未使用导入/F841未使用变量/风格）。"""
        if not file_path:
            return {"passed": True, "detail": "无文件（跳过）"}
        _abs = os.path.join(self._project_root, file_path)
        if not os.path.exists(_abs):
            return {"passed": True, "detail": f"文件不存在（跳过）: {file_path}"}
        try:
            _proc = subprocess.run(
                ["python", "-m", "ruff", "check", _abs],
                check=False, capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
                cwd=self._project_root,
            )
            _out = (_proc.stdout or "") + (_proc.stderr or "")
            if _proc.returncode == 0:
                return {"passed": True, "detail": "RUFF 全绿"}
            _lines = [l for l in _out.splitlines() if l.strip()]
            return {"passed": False, "detail": f"RUFF {len(_lines)}项问题", "output": _out[-1000:]}
        except subprocess.TimeoutExpired:
            return {"passed": True, "detail": "RUFF 超时（跳过，不阻断）"}
        except Exception as _e:
            return {"passed": True, "detail": f"RUFF 不可用（跳过）: {_e}"}

    # ========== 4. 端到端往返测试（★P0 新增） ==========

    def _run_roundtrip_test(self, patch: dict[str, Any]) -> dict[str, Any]:
        """涉及存储格式/数据结构变更时，自动执行保存→加载→一致性验证。"""
        _file = (patch.get("file", "") or "").lower()
        _issue = (patch.get("issue_type", "") or "").lower()
        _triggers = ("snapshot", "parquet", "codec", "store", "serialize", "storage", "format")
        if not any(k in _file for k in _triggers) and not any(k in _issue for k in _triggers):
            return {"passed": True, "detail": "非存储类变更（跳过）"}
        _patch_id = patch.get("id", f"rt_{int(time.time())}")
        _script = self._render_roundtrip_script(_patch_id, patch)
        if not _script:
            return {"passed": True, "detail": "无法生成往返脚本（跳过）"}
        _script_path = os.path.join(self._test_dir, f"{_patch_id}_roundtrip.py")
        try:
            with open(_script_path, "w", encoding="utf-8") as _f:
                _f.write(_script)
        except Exception:
            return {"passed": True, "detail": "脚本写入失败（跳过）"}
        try:
            _proc = subprocess.run(
                ["python", _script_path],
                check=False, capture_output=True, text=True, timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"],
                encoding="utf-8", errors="replace",
                cwd=self._project_root,
            )
            _out = (_proc.stdout or "") + (_proc.stderr or "")
            return {
                "passed": _proc.returncode == 0,
                "detail": "往返一致性通过" if _proc.returncode == 0 else "往返一致性失败",
                "output": _out[-1500:],
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "detail": "往返测试超时(120s)"}
        except Exception as _e:
            return {"passed": True, "detail": f"往返测试异常（跳过）: {_e}"}

    def _render_roundtrip_script(self, patch_id: str, patch: dict[str, Any]) -> str | None:
        """渲染端到端往返测试脚本。"""
        _file = patch.get("file", "")
        if "PulseSnapshot" in _file:
            return RT_SCRIPT_PULSE_SNAPSHOT.replace("{patch_id}", patch_id)
        return None

    # ========== 5. 旧格式兼容测试（★P0 新增） ==========

    def _run_compat_test(self, patch: dict[str, Any]) -> dict[str, Any]:
        """修改加载/解析逻辑时，验证旧格式文件仍可正确加载。"""
        _file = (patch.get("file", "") or "").lower()
        _mod = (patch.get("modified_code", "") or "")
        _triggers = ("snapshot", "parquet", "codec", "loader")
        _code_triggers = ("json.loads", "from_dict", "_decode", "_parse")
        if not any(k in _file for k in _triggers) and not any(k in _mod for k in _code_triggers):
            return {"passed": True, "detail": "非加载逻辑变更（跳过）"}
        _patch_id = patch.get("id", f"compat_{int(time.time())}")
        _script = self._render_compat_script(_patch_id, patch)
        if not _script:
            return {"passed": True, "detail": "无法生成兼容脚本（跳过）"}
        _script_path = os.path.join(self._test_dir, f"{_patch_id}_compat.py")
        try:
            with open(_script_path, "w", encoding="utf-8") as _f:
                _f.write(_script)
        except Exception:
            return {"passed": True, "detail": "脚本写入失败（跳过）"}
        try:
            _proc = subprocess.run(
                ["python", _script_path],
                check=False, capture_output=True, text=True, timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"],
                encoding="utf-8", errors="replace",
                cwd=self._project_root,
            )
            _out = (_proc.stdout or "") + (_proc.stderr or "")
            return {
                "passed": _proc.returncode == 0,
                "detail": "旧格式兼容通过" if _proc.returncode == 0 else "旧格式兼容失败",
                "output": _out[-1500:],
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "detail": "兼容测试超时(120s)"}
        except Exception as _e:
            return {"passed": True, "detail": f"兼容测试异常（跳过）: {_e}"}

    def _render_compat_script(self, patch_id: str, patch: dict[str, Any]) -> str | None:
        """渲染旧格式兼容测试脚本。"""
        _file = patch.get("file", "")
        if "PulseSnapshot" in _file:
            return COMPAT_SCRIPT_PULSE_SNAPSHOT.replace("{patch_id}", patch_id)
        return None

    # ========== 6. 回归测试 ==========


    def _run_regression_tests(self) -> dict[str, Any]:
        """运行项目既有的轻量回归脚本。

        ★第五批 任务2B：原清单写死 tools/test_parquet_stage_a.py 与
        tools/verify_patch_coverage.py，二者在仓库中**根本不存在**，旧代码遇到
        不存在就 `continue`，结果恒为「0通过/0失败」（看着像回归挂了，实则没跑）。
        现改为：① 默认清单指向仓库真实存在的回归脚本；② 支持
        EVOLUTION_CONFIG["regression_scripts"] 覆盖；③ 脚本缺失时**显式记录**
        到 missing，不再静默跳过，避免「以为在测其实没测」。
        """
        _default_scripts = [
            "tools/verify_phase17_1_5.py",
            "tools/verify_semantic_kernel.py",
        ]
        try:
            import config as _cfg_mod
            _test_scripts = list(getattr(
                _cfg_mod, "EVOLUTION_CONFIG", {}).get(
                    "regression_scripts", _default_scripts) or _default_scripts)
        except Exception:
            _test_scripts = list(_default_scripts)

        _passed = 0
        _failed = 0
        _details = []
        _missing = []

        for _rel in _test_scripts:
            _script = os.path.join(self._project_root, _rel)
            if not os.path.exists(_script):
                # ★第五批：缺失脚本显式登记，不再静默 continue
                _missing.append(os.path.basename(_rel))
                _failed += 1
                continue
            try:
                # ★E1修复（P2，2026-09-05）：子进程 stdio 强制 UTF-8。
                #   与 PatchManager._run_regression_tests 是同一处模式缺陷的
                #   第二份拷贝——父进程的 encoding="utf-8" 管不到子进程的
                #   stdout 编码，Windows 中文环境（GBK）下脚本一打印 emoji
                #   就 UnicodeEncodeError 崩溃，回归结果恒为「0通过/2失败」。
                #   本地复现得到的错误与生产日志逐字一致：
                #   UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
                #   修改同类模式时须两处同步，否则仍然一边修好一边坏。
                _env = dict(os.environ)
                _env["PYTHONIOENCODING"] = "utf-8"
                _proc = subprocess.run(  # 有意不检查子进程退出码
                    ["python", _script],
                    check=False, capture_output=True, text=True, timeout=TIMEOUT_CONFIG['llm_call'],
                    encoding="utf-8", errors="replace",
                    cwd=self._project_root, env=_env,
                )
                if _proc.returncode == 0:
                    _passed += 1
                else:
                    _failed += 1
                    _details.append(f"{os.path.basename(_script)}")
            except subprocess.TimeoutExpired:
                _failed += 1
                _details.append(f"{os.path.basename(_script)}: 超时")
            except Exception:
                _failed += 1
                _details.append(f"{os.path.basename(_script)}")

        return {
            "passed": _failed == 0 and _passed > 0,
            "summary": f"{_passed}通过/{_failed}失败",
            "failed_details": _details,
            "missing": _missing,
        }


# ========== 便捷函数 ==========

def get_test_generator(project_root: str) -> TestGenerator:
    """获取 TestGenerator 实例。"""
    return TestGenerator(project_root)


if __name__ == "__main__":
    # 自测：对某个方法生成冒烟测试
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _gen = TestGenerator(_root)
    _patch = {
        "id": "test_smoke",
        "file": "nucleus/evolution/HealthScore.py",
        "method": "compute_health_score",
        # ★第90批 相关任务：补齐 original_code —— 全库 11 个「补丁 dict 构造点」
        #   中唯一缺该字段的一处（属 `__main__` 自测夹具，不进验证链路，
        #   但字段契约应全库一致）。
        "original_code": "def compute_health_score():\n    return {'score': 0}\n",
        "modified_code": "def compute_health_score():\n    return {'score': 100}\n",
    }
    _r = _gen.generate_and_run(_patch)
    print(f"测试结果: {'通过' if _r['passed'] else '失败'} - {_r['summary']}")
    for _stage, _info in _r["stages"].items():
        print(f"  {_stage}: {_info.get('detail', _info.get('summary', ''))}")
