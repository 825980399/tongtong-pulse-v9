# -*- coding: utf-8 -*-
"""169批 C10 门控单测：脉冲纪律审计（保活走心跳 + 业务延时全分类）。

覆盖 tools/pulse_sleep_audit.py：
  - 当前仓库态：审计 PASS（保活 1 处走网关 / 业务延时 10 处全豁免 / 未分类 0）；
  - 反例：注入一处未分类的业务 sleep → 审计 FAIL（防回潮，防未来漏标记）；
  - 反例：保活退化为纯裸 sleep（去掉网关分流）→ 审计 FAIL；
  - 归属判定：嵌套函数取最内层（`_keepalive_loop` != `_start_keepalive_timer`）。
"""
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_audit():
    """按文件路径加载 tools/pulse_sleep_audit.py。"""
    p = os.path.join(_ROOT, "tools", "pulse_sleep_audit.py")
    spec = importlib.util.spec_from_file_location("pulse_sleep_audit_m169", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit_mod = _load_audit()


def test_current_repo_passes():
    r = audit_mod.audit()
    assert r["pass"] is True, r["violations"]
    assert r["gateway_present"] is True
    assert r["keepalive_gateway_split"] is True
    assert r["business_unclassified"] == 0
    assert r["business_exempt"] >= 10
    assert r["keepalive_sleep"] == 1


def test_nested_func_attribution_is_innermost():
    """保活 sleep 归属嵌套内层 `_keepalive_loop`，不是外层 `_start_keepalive_timer`。"""
    r = audit_mod.audit()
    keep = [s for s in r["sleeps"] if s["func"] == audit_mod.KEEPALIVE_DEF]
    assert len(keep) == 1, keep
    assert keep[0]["func"] != "_start_keepalive_timer"


def test_unclassified_business_sleep_fails(monkeypatch):
    """反例：新增一处未标记的业务 sleep → 必须 FAIL。"""
    orig = audit_mod._read_lines

    def fake_lines():
        lines = list(orig())
        # 在既有豁免 sleep（time.sleep(1.5)）之后插入同缩进的未标记 sleep
        for i, l in enumerate(lines):
            if ("[pulse-sleep-exempt]" in l
                    and i + 1 < len(lines)
                    and lines[i + 1].strip().startswith("time.sleep(1.5)")):
                indent = lines[i + 1][:len(lines[i + 1]) - len(lines[i + 1].lstrip())]
                lines.insert(i + 2, indent + "time.sleep(9.9)")
                break
        else:
            raise AssertionError("未找到注入锚点")
        return lines

    monkeypatch.setattr(audit_mod, "_read_lines", fake_lines)
    r = audit_mod.audit()
    assert r["pass"] is False
    assert r["business_unclassified"] == 1
    assert any("未分类" in v for v in r["violations"])


def test_keepalive_without_gateway_fails(monkeypatch):
    """反例：保活退化为纯裸 sleep（移除网关调用）→ 必须 FAIL。"""
    orig = audit_mod._read_lines

    def fake_lines():
        out = []
        for l in orig():
            if "self.wait_heartbeat_pulse(60)" in l:
                # 合法语句替换，但网关调用消失 ⇒ 分流检查应 FAIL
                out.append(l.replace("self.wait_heartbeat_pulse(60)",
                                     "self._heartbeat_pulse.clear()"))
            else:
                out.append(l)
        return out

    monkeypatch.setattr(audit_mod, "_read_lines", fake_lines)
    r = audit_mod.audit()
    assert r["pass"] is False
    assert r["keepalive_gateway_split"] is False
    assert any("wait_heartbeat_pulse" in v for v in r["violations"])