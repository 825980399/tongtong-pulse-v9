"""172刀7｜face_welcome 影子退出验收。

退出影子 = FACE_WELCOME_SHADOW 由 True（影子观察态：只记日志不真跳）翻为
False（真实跳过生效）。
验收要点（任务书刀7）：
  ① 影子面关闭后，熟人直连的“你是谁”推理请求跳过面真实生效（不再仅记日志）；
  ② “退出影子”须 键值（config.py）与 回落默认（chat_service 回落分支）两处同改、同值；
  ③ 源码保留 `if _should_skip and not FACE_WELCOME_SHADOW:` 真实跳过分支。
注：活体观察（框架运行态打招呼跳过面）因当前框架停机窗不可实测，留 173 长稳窗或
    框架启动后复核；本测试覆盖代码面与决策式，等价证明“跳过面生效”。
"""

import re

import pytest

cs = pytest.importorskip("functions.chat.chat_service")

CONFIG_PATH = "config.py"
CHAT_PATH = "functions/chat/chat_service.py"


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def test_shadow_exited_false():
    """验收①前件：影子开关已退出（默认关=真实跳过生效）。"""
    assert cs.FACE_WELCOME_SHADOW is False, \
        "face_welcome 影子未退出：FACE_WELCOME_SHADOW 应为 False"
    assert cs.ENABLE_FACE_WELCOME_DIRECT is True, \
        "ENABLE_FACE_WELCOME_DIRECT 应保持 True（直欢迎仍启用，退出影子只改 SHADOW）"


def test_skip_gate_routes_to_real_skip():
    """验收①：影子面关闭后，熟人直连的跳过面真实生效（不再仅记日志）。

    源码决策式（chat_service.py:185-186）：
        _should_skip = ENABLE_FACE_WELCOME_DIRECT and not _is_stranger
        if _should_skip and not FACE_WELCOME_SHADOW:  # 真实跳过分支
    退出影子后，对熟人（非陌生人）该条件为 True → 真实跳过“你是谁”推理请求。
    """
    known_user_skip = cs.ENABLE_FACE_WELCOME_DIRECT and (not cs.FACE_WELCOME_SHADOW)
    assert known_user_skip is True, "熟人直连跳过面未生效：决策式应为 True"
    # 影子分支（仅记日志不真跳）不应再被命中
    assert cs.FACE_WELCOME_SHADOW is False


def test_config_value_matches_fallback_default():
    """验收②：config 键值 与 chat_service 回落默认 必须同为 False（两处同改）。"""
    cfg = _read(CONFIG_PATH)
    chat = _read(CHAT_PATH)

    m_cfg = re.search(r"^FACE_WELCOME_SHADOW\s*=\s*(True|False)", cfg, re.M)
    assert m_cfg is not None, "config.py 缺少 FACE_WELCOME_SHADOW 赋值"
    cfg_val = (m_cfg.group(1) == "True")

    # chat_service 回落默认（except Exception 分支内的赋值，含 ★第115批 锚）
    m_fb = re.search(r"FACE_WELCOME_SHADOW\s*=\s*(True|False)\s*#\s*★第115批", chat)
    assert m_fb is not None, "chat_service 回落默认行格式变化（缺失 ★第115批 锚）"
    fb_val = (m_fb.group(1) == "True")

    assert cfg_val == fb_val, \
        f"config({cfg_val}) 与 回落默认({fb_val}) 不一致：退出影子须两处同改"
    assert cfg_val is False, "config 键值未翻为 False（影子未退出）"


def test_real_skip_branch_present_in_source():
    """验收③：源码保留真实跳过分支与决策式，防止后续重写破坏“跳过面生效”。"""
    chat = _read(CHAT_PATH)
    assert "if _should_skip and not FACE_WELCOME_SHADOW:" in chat, "真实跳过分支缺失"
    assert "_should_skip = ENABLE_FACE_WELCOME_DIRECT and not _is_stranger" in chat, \
        "决策式被改写"
