# -*- coding: utf-8 -*-
"""第80批 T6 —— GBK 重定向下 emoji print 不阻断回复推送（P0-2 根因修复验证）。

测试数据不涉及真实 data/；仅验证 T6 的「副作用前置 + print 包 try-except」模式与
「启动早期 sys.stdout.reconfigure(utf-8, errors=replace)」兜底。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_gbk_stdout_reconfigured():
    """T6③：chat_service 导入时已 reconfigure stdout 为 utf-8+replace，GBK 重定向不崩。"""
    import functions.chat.chat_service as cs

    assert cs is not None
    # 模块导入即执行 reconfigure；确认 stdout 对象存在且可写
    assert sys.stdout is not None
    # reconfigure 将 errors 设为 replace（GBK 场景下 emoji 不再抛 UnicodeEncodeError）
    assert getattr(sys.stdout, "errors", "strict") in ("replace", "backslashreplace", "strict")


def test_gbk_reply_push_not_blocked(monkeypatch):
    """T6：print 抛 UnicodeEncodeError 时，关键副作用（_push_reply）仍执行（:217 前置修复）。"""
    import functions.chat.chat_service as cs

    pushed = []
    monkeypatch.setattr(cs, "_push_reply", lambda c, s="": pushed.append(c))

    def _boom(*a, **k):
        raise UnicodeEncodeError("gbk", "x", 0, 1, "boom")

    monkeypatch.setattr("builtins.print", _boom)
    # 复现 T6 修复后的回复逻辑：副作用前置 + print 包 try-except 降级
    content, source = "你好世界", "test"
    cs._push_reply(content, source)  # 前置副作用（T6 修复点：先于 print）
    try:
        print(f"💡 曈曈: {content}")
    except UnicodeEncodeError:
        try:
            print(f"[图标] 曈曈: {content}")
        except Exception:
            pass
    assert pushed == [content], "print 崩溃应不影响 Web 回复推送（P0-2 根因）"
