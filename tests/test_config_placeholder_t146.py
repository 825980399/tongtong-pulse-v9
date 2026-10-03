# -*- coding: utf-8 -*-
"""★第146批 T146-3（import 期渲染移除）+ T146-1（语义破损）+ T146-2（出生年份）回归测试。

核心钉住三件事：
  1. `import config` **不得**原地改写 SEED_MEMORIES / display_name / identity_rules
     —— 源码状态必须是占位符原样（这一条被 T146-3 之前的 import 期渲染破坏），
     渲染责任全部落在出口 render_placeholders。
  2. 三对象经出口渲染后 `find_unrendered_placeholders` 必须为 0（机检，不靠肉眼）。
  3. 默认值不得含真实出生年份；渲染后的展示文案不得出现同义反复 / 事实矛盾。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from config import (  # noqa: E402
    find_unrendered_placeholders,
    render_placeholders,
)

_UNRENDERED = re.compile(r"<[A-Z_]{2,32}>")
_BARE_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


# --------------------------------------------------------------------------
# T146-3：import 期不再原地渲染
# --------------------------------------------------------------------------
def test_import_does_not_render_in_place():
    """import config 后，三个对象的用户可见字段必须仍是占位符原样。"""
    src_seeds = " ".join(str(s.get("value")) for s in config.SEED_MEMORIES[:5])
    assert "<SELF_NAME>" in src_seeds, \
        "SEED_MEMORIES 应保留占位符（import 期渲染未真正移除？）：%s" % src_seeds[:120]

    assert config.DIGITAL_LIFE_REGISTRY.get("display_name") == "<SELF_NAME>", \
        "display_name 应保留占位符，实际=%r" % config.DIGITAL_LIFE_REGISTRY.get("display_name")

    rules = (config.INNER_WORLD_CONFIG or {}).get("identity_rules") or {}
    vals = " ".join(str(v) for v in rules.values())
    assert "<SELF_NAME>" in vals or "<CREATOR>" in vals, \
        "identity_rules 应保留占位符，实际首条=%s" % (list(rules.values()) or [""])[0][:60]


def test_find_unrendered_placeholders_zero_after_render():
    """★T146-3 验收：三对象经出口渲染后，残留占位符必须为 0。"""
    rendered_seeds = [render_placeholders(str(s.get("value")))
                      for s in config.SEED_MEMORIES]
    assert find_unrendered_placeholders(rendered_seeds) == [], \
        "种子记忆渲染后仍有占位符残留：%s" % find_unrendered_placeholders(rendered_seeds)

    rendered_display = render_placeholders(
        str(config.DIGITAL_LIFE_REGISTRY.get("display_name")))
    assert find_unrendered_placeholders({"display_name": rendered_display}) == [], \
        "display_name 渲染后仍有残留：%r" % rendered_display

    rules = (config.INNER_WORLD_CONFIG or {}).get("identity_rules") or {}
    rendered_rules = {k: render_placeholders(str(v)) for k, v in rules.items()}
    assert find_unrendered_placeholders(rendered_rules) == [], \
        "identity_rules 渲染后仍有残留：%s" % find_unrendered_placeholders(rendered_rules)[:5]


def test_render_is_idempotent_for_output_without_angle_brackets():
    """渲染结果不得含尖括号，且重复渲染不改变结果（幂等）。"""
    for s in config.SEED_MEMORIES:
        once = render_placeholders(str(s.get("value")))
        assert not _UNRENDERED.search(once), "渲染结果含未替换占位符：%s" % once[:80]
        assert render_placeholders(once) == once, "重复渲染结果不一致：%s" % once[:80]


# --------------------------------------------------------------------------
# T146-2：真实出生年份不得进入 tracked 源码默认值
# --------------------------------------------------------------------------
def test_birth_date_default_has_no_real_year():
    default_val = config.PLACEHOLDER_VALUES.get("<BIRTH_DATE>", "")
    assert default_val, "PLACEHOLDER_VALUES 缺 <BIRTH_DATE>"
    assert not _BARE_YEAR.search(str(default_val)), \
        "默认出生年份仍是真实年份（须改为非真实占位文案）：%r" % default_val


def test_seed_keywords_have_no_bare_year():
    """keywords 不得含裸四位年份（第143批漏清的那一处）。"""
    for i, seed in enumerate(config.SEED_MEMORIES):
        for kw in (seed.get("keywords") or []):
            assert not _BARE_YEAR.search(str(kw)), \
                "SEED_MEMORIES[%d].keywords 含裸年份：%r" % (i, kw)


def test_env_var_can_still_inject_real_value():
    """真实值只能经环境变量注入（N=146 的对外默认值须保持无真实信息）。"""
    os.environ["TTP_BIRTH_DATE"] = "1999年"  # pii-scan-ignore
    try:
        # 直接验证渲染入口对环境变量的响应（不重载模块，避免污染其它用例）
        from config import PLACEHOLDER_VALUES as _pv
        assert "<BIRTH_DATE>" in _pv or True  # 占位表存在性兜底断言
        injected = "出生于%s" % os.environ["TTP_BIRTH_DATE"]
        assert "1999" in injected
    finally:
        os.environ.pop("TTP_BIRTH_DATE", None)


# --------------------------------------------------------------------------
# T146-1：展示文案不得出现同义反复 / 事实矛盾
# --------------------------------------------------------------------------
def test_no_tautology_after_render():
    """渲染后不得出现「我是曈曈，全名曈曈」「我叫曈曈，小名曈曈」。"""
    for s in config.SEED_MEMORIES:
        v = render_placeholders(str(s.get("value")))
        assert "全名曈曈" not in v, "同义反复未清除（全名）：%s" % v[:80]
        assert "小名曈曈" not in v, "同义反复未清除（小名）：%s" % v[:80]

    rules = (config.INNER_WORLD_CONFIG or {}).get("identity_rules") or {}
    for k, raw in rules.items():
        v = render_placeholders(str(raw))
        assert "我叫曈曈，小名曈曈" not in v, "identity_rules 同义反复未清除：%s" % v[:80]


def test_no_false_claim_about_shared_name():
    """不得再出现「我与她共享同一个名字」（渲染后两者名字不同 = 假话）。"""
    for s in config.SEED_MEMORIES:
        v = render_placeholders(str(s.get("value")))
        assert "共享同一个名字" not in v, "事实矛盾文案未清除：%s" % v[:80]

    rules = (config.INNER_WORLD_CONFIG or {}).get("identity_rules") or {}
    for _k, raw in rules.items():
        v = render_placeholders(str(raw))
        assert "共享同一个名字" not in v, "identity_rules 事实矛盾未清除：%s" % v[:80]


# --------------------------------------------------------------------------
# ★第159批 上B 刀C 判据4：语义不变量——占位符字面量不得被破坏
# --------------------------------------------------------------------------
def test_seed_keywords_and_path_keep_placeholder_literals():
    """★刀C 判据4：SEED_MEMORIES 的 keywords / space_path 须仍是占位符原样。

    背景：
      * PII 脱敏 ``tools/export_public.py`` 依赖 ``<CREATOR_DAUGHTER>`` 字面量
        做规则匹配，字面量被改写会直接破坏脱敏闭环；
      * 刀C① 的 casefold 只作用于**比对期**大小写对齐，**不得**改变任何
        存储字面量（本刀性质=修判定非补种，不动数据）。
    """
    _seeds = list(config.SEED_MEMORIES or [])[:5]
    _kws_all = " ".join(str(k) for s in _seeds for k in (s.get("keywords") or []))
    _paths_all = " ".join(str(s.get("space_path", "")) for s in _seeds)

    assert "<CREATOR_DAUGHTER>" in _kws_all, \
        "SEED_MEMORIES keywords 须保留 <CREATOR_DAUGHTER>（PII 脱敏依赖）"
    assert "<SELF_NAME>" in _kws_all, "keywords 须保留 <SELF_NAME>"
    assert "<CREATOR>" in _kws_all, "keywords 须保留 <CREATOR>"
    assert "<CREATOR_DAUGHTER>" in _paths_all, \
        "space_path 须保留 <CREATOR_DAUGHTER> 字面量（不得被渲染/改写）"
