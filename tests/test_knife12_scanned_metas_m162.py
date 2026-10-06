# -*- coding: utf-8 -*-
"""第162批 刀12（C-7 收尾）· _scanned_metas 读方门控单测。

验收：_scanned_metas 有读取方（get_scanned_metas）；差集校验行为不变。
"""
import pytest

from nucleus.organ_loader import OrganLoader, OrganMeta


def _make_loader():
    return OrganLoader(None)


def _fake_meta(name="fake_organ"):
    return OrganMeta(
        name=name,
        module_path=f"organs.fake.{name}",
        class_name="FakeOrgan",
        system="fake",
    )


def test_get_scanned_metas_initial_is_none():
    ol = _make_loader()
    assert ol.get_scanned_metas() is None


def test_get_scanned_metas_after_load_matches():
    ol = _make_loader()
    fake = [_fake_meta()]
    # 隔离：用桩替换目录扫描，避免真实 organs/ 重扫描
    ol.scan_organs_directory = lambda: list(fake)
    metas = ol.load_organs()
    assert metas is not None
    assert ol.get_scanned_metas() is metas
    assert ol.get_scanned_metas()[0].name == "fake_organ"


def test_get_scanned_metas_returns_set_value():
    ol = _make_loader()
    ol._scanned_metas = ["a", "b"]
    assert ol.get_scanned_metas() == ["a", "b"]
