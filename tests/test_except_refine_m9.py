# -*- coding: utf-8 -*-
"""主线第9批 T2 / P2-72：高风险 except Exception 精细化机制测试。

直接驱动 tmp/patch_t2_m9.py 的 process_file，对合成源码做改写断言，
覆盖：JSON 单一读取收窄、兜底补日志、幂等、已有日志不重复、单行 handler 跳过。
"""
import ast
import os
import sys
import tempfile

# 加载施工脚本（ruff.toml 已按 tmp/patch_* 前缀排除，不影响全库 F 门禁）
_TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp")
if _TMP not in sys.path:
    sys.path.insert(0, _TMP)
import patch_t2_m9 as m  # noqa: E402


def _write_tmp(src: str):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="t2test_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(src)
    return path


def _parse_ok(path):
    ast.parse(open(path, encoding="utf-8").read())


def test_json_read_narrowed_and_logged():
    """单一 json.load 读取：except Exception 收窄为 (ValueError, OSError) 且补 WARNING 日志。"""
    src = (
        "import json\n"
        "def f(p):\n"
        "    try:\n"
        "        x = json.load(open(p))\n"
        "    except Exception:\n"
        "        return None\n"
    )
    path = _write_tmp(src)
    try:
        r = m.process_file(path)
        out = open(path, encoding="utf-8").read()
        _parse_ok(path)
        assert r["json_narrow"] == 1, r
        assert "except (ValueError, OSError) as e:" in out, out
        assert "[WARNING]" in out, out
    finally:
        os.remove(path)


def test_bare_except_gets_log():
    """兜底无日志的 except Exception（高风险非 json 单一读取，如 requests.get）：
    补 WARNING 日志，保留 except Exception 兜底。"""
    src = (
        "import requests\n"
        "def f(url):\n"
        "    try:\n"
        "        r = requests.get(url, timeout=5)\n"
        "    except Exception:\n"
        "        cleanup()\n"
    )
    path = _write_tmp(src)
    try:
        r = m.process_file(path)
        out = open(path, encoding="utf-8").read()
        _parse_ok(path)
        assert r["log_add"] == 1, r
        assert "[WARNING]" in out, out
        # 非 json 单一读取分支：保留 except Exception 兜底
        assert "except Exception as e:" in out, out
    finally:
        os.remove(path)


def test_idempotent_rerun():
    """幂等：连续两次改写，第二次不产生重复日志。"""
    src = (
        "import json\n"
        "def f(p):\n"
        "    try:\n"
        "        x = json.load(open(p))\n"
        "    except Exception:\n"
        "        return None\n"
    )
    path = _write_tmp(src)
    try:
        m.process_file(path)
        out1 = open(path, encoding="utf-8").read()
        r2 = m.process_file(path)
        out2 = open(path, encoding="utf-8").read()
        _parse_ok(path)
        assert out1 == out2, "二次运行不应改变源码"
        assert r2["json_narrow"] == 0 and r2["log_add"] == 0, r2
        assert out2.count("[WARNING]") == 1, out2
    finally:
        os.remove(path)


def test_handler_with_existing_log_not_double_logged():
    """handler 已有日志：不再补日志，且收窄为 (ValueError, OSError)（无未用变量，避免 F841）。"""
    src = (
        "import json\n"
        "def f(p):\n"
        "    try:\n"
        "        x = json.load(open(p))\n"
        "    except Exception:\n"
        "        logger.error('load failed')\n"
    )
    path = _write_tmp(src)
    try:
        r = m.process_file(path)
        out = open(path, encoding="utf-8").read()
        _parse_ok(path)
        assert r["json_narrow"] == 1, r
        assert r["log_add"] == 0, r
        # 已有日志使用自身 logger.error，不应引入未使用变量 e
        assert "except (ValueError, OSError):" in out, out
        assert "as e:" not in out, out
        assert out.count("[WARNING]") == 0, out
    finally:
        os.remove(path)


def test_single_line_handler_skipped():
    """单行 handler（body 与 except 同行）：跳过，避免插入错位。"""
    src = (
        "import json\n"
        "def f(p):\n"
        "    try:\n"
        "        x = json.load(open(p))\n"
        "    except Exception: pass\n"
    )
    path = _write_tmp(src)
    try:
        r = m.process_file(path)
        out = open(path, encoding="utf-8").read()
        _parse_ok(path)
        assert r["json_narrow"] == 0 and r["log_add"] == 0, r
        assert out == src, "单行 handler 应原样保留"
    finally:
        os.remove(path)
