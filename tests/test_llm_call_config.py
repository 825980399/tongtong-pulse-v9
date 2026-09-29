# -*- coding: utf-8 -*-
"""主线第8批 任务3 P2-56/57/58：LLM 调用统一配置 —— 回归测试。

覆盖：
1. config.LLM_CALL_CONFIG 配置块结构/默认值正确
2. get_llm_call_config() 缺失配置时回落保守默认（含 timeout_by_purpose 浅合并）
3. api_rate_limited 上下文管理器：开关关闭零副作用；开启时正确获取/释放并发许可
4. SafeEvolutionExecutor 已收口到 get_llm_call_config（静态校验，避免重复硬编码）
"""
import os
import sys


# 保证仓库根目录在 sys.path，便于直接 `python -m pytest`
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_llm_call_config_block_exists():
    import config
    assert hasattr(config, "LLM_CALL_CONFIG"), "config 缺少 LLM_CALL_CONFIG 配置块"
    cfg = config.LLM_CALL_CONFIG
    assert cfg["default_model"] == "deepseek-v4-flash"
    assert cfg["enable_rate_limit"] is True
    assert isinstance(cfg["rate_limit_per_minute"], int)
    tp = cfg["timeout_by_purpose"]
    assert tp["evolution"] == 30
    assert tp["general"] == 60
    # ★主线第24批：星轨为「免费渠道超时」同步修复了这两项超时
    #   （inner_world_refine 15→30、inner_world_chat 20→45，见 config.LLM_CALL_CONFIG 注释），
    #   测试断言随之同步 —— 否则门禁恒不可达。改动前的断言值为 15 / 20。
    assert tp["inner_world_refine"] == 30
    assert tp["inner_world_chat"] == 45
    # REMOTE_API_CONFIG 必须保留（不破坏既有 LLM 调用）
    assert hasattr(config, "REMOTE_API_CONFIG")


def test_get_llm_call_config_fallback():
    from nucleus.api_rate_limiter import get_llm_call_config
    cfg = get_llm_call_config()
    assert cfg["default_model"] == "deepseek-v4-flash"
    assert cfg["timeout_by_purpose"]["evolution"] == 30
    assert cfg["timeout_by_purpose"]["spiritual"] == 30
    # 开关字段存在
    assert "enable_rate_limit" in cfg
    assert "rate_limit_per_minute" in cfg


def test_api_rate_limited_acquire_release():
    from nucleus.api_rate_limiter import get_api_limiter, api_rate_limited

    lim = get_api_limiter()
    before = lim.get_stats()["in_use"]

    # 关闭：零副作用，不获取许可
    with api_rate_limited(enabled=False):
        mid_off = lim.get_stats()["in_use"]
    after_off = lim.get_stats()["in_use"]
    assert mid_off == before, "enable=False 不应获取并发许可"
    assert after_off == before, "enable=False 不应遗留许可占用"

    # 开启：获取并在退出时释放
    with api_rate_limited(enabled=True):
        mid_on = lim.get_stats()["in_use"]
    after_on = lim.get_stats()["in_use"]
    assert mid_on == before + 1, "enable=True 应获取一个并发许可"
    assert after_on == before, "api_rate_limited 退出应释放许可"


def test_see_call_sites_use_config():
    """静态校验：SafeEvolutionExecutor 不再硬编码模型名/超时，已收口到配置。"""
    see_path = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    with open(see_path, encoding="utf-8") as f:
        src = f.read()
    # 旧的硬编码模型名应已全部消除
    assert '"model": "deepseek-v4-flash",' not in src, "SEE 仍存在硬编码模型名"
    # 旧的直接 timeout=30 调用应已消除（统一走 LLM_CALL_CONFIG）
    assert "timeout=30)" not in src or "timeout=get_llm_call_config()" in src
    # 三处调用应均已接入限流
    # ★主线第33批 T2（P2-195）：语义=「SEE 的每个 LLM 调用点都接限流/配置」，
    #   少接一处即 <3 仍失败；改用 >= 以免新增调用点时假失败。
    assert src.count("api_rate_limited(enabled=get_llm_call_config()") >= 3
    assert src.count('get_llm_call_config().get("default_model"') >= 3
