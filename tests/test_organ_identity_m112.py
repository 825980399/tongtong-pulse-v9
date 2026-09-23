# -*- coding: utf-8 -*-
"""T-112d resolve_organ_key 纯函数单测 + 豁免归一化比对验证。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.organ_identity import resolve_organ_key, resolve_organ_keys


def test_resolve_known_variants():
    # 对话模块命名空间（含带后缀变体与裸名豁免表项）
    assert resolve_organ_key("Web对话-人脸监听") == "对话模块"
    assert resolve_organ_key("企业微信桥接器-回复监听") == "对话模块"
    assert resolve_organ_key("企业微信") == "对话模块"
    assert resolve_organ_key("Web对话") == "对话模块"
    assert resolve_organ_key("wecom") == "对话模块"
    # 时间中枢命名空间
    assert resolve_organ_key("TimeCore") == "时间中枢"
    assert resolve_organ_key("时间中枢") == "时间中枢"
    assert resolve_organ_key("timecore") == "时间中枢"
    # 其他
    assert resolve_organ_key("FunctionLoader") == "FunctionLoader"
    assert resolve_organ_key("framework_self_modify_gate") == "framework_self_modify_gate"


def test_resolve_unknown_returns_lower():
    assert resolve_organ_key("") == ""
    assert resolve_organ_key("  SomeNewOrgan ") == "someneworgan"
    assert resolve_organ_key("GALLBLADDER") == "gallbladder"


def test_rename_hits_same_key():
    # 演示「第5个豁免器官改名」机制：登记规范 key + 变体后即可归一命中
    import nucleus.organ_identity as oi
    _orig = dict(oi.ORGAN_ALIASES)
    try:
        oi.ORGAN_ALIASES["胆"] = ("胆", "胆囊", "gallbladder")
        oi._ALIAS_INDEX = {}
        for _k, _als in oi.ORGAN_ALIASES.items():
            oi._ALIAS_INDEX[_k.lower()] = _k
            for _a in _als:
                oi._ALIAS_INDEX[str(_a).lower()] = _k
        assert resolve_organ_key("胆") == "胆"
        assert resolve_organ_key("胆囊") == "胆"  # 改名变体归一命中
        _exempt = resolve_organ_keys(["胆"])
        assert resolve_organ_key("胆囊") in _exempt
    finally:
        oi.ORGAN_ALIASES = _orig
        oi._ALIAS_INDEX = {}
        for _k, _als in oi.ORGAN_ALIASES.items():
            oi._ALIAS_INDEX[_k.lower()] = _k
            for _a in _als:
                oi._ALIAS_INDEX[str(_a).lower()] = _k


def test_exempt_normalization_dedup():
    # 豁免表裸名归一后去重（企业微信/Web对话 都归到 对话模块）
    assert resolve_organ_keys(["企业微信", "Web对话"]) == {"对话模块"}


if __name__ == "__main__":
    test_resolve_known_variants()
    test_resolve_unknown_returns_lower()
    test_rename_hits_same_key()
    test_exempt_normalization_dedup()
    print("ALL OK")
