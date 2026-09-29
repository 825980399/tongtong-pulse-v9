# -*- coding: utf-8 -*-
"""T153-3② 导出时名字归一 回归测试（对照任务书 T153-3 验收：仅注释/docstring 归一，身份值除外）。

覆盖：
  - COMMENT / 三引号文档字符串 角色名(星轨/烛微/小林)→内部协作者、批次号(第N批/Dxxx/T-xx)→通用说明
  - 双引号普通数据字符串（SEED_MEMORIES 身份值、self.name="路灯"）→ 受保护不动
  - 世界观句「路灯是第一个数字生命」整体保留「路灯」，仅归一同句其他角色名
  - 孤立「路灯」（注释中无角色/协作上下文）→ 保留（Q152-7 生产仓保名语义）
  - 点文件 .gitignore / .gitattributes（os.path.splitext 返空 ext）→ 必须归一（T153-3② 修复点）
  - f-string 字面量（Python 3.12 FSTRING_MIDDLE）→ 归一
  - 二进制 / 解码失败 → 原样返回
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tools.export_public as ep  # noqa: E402


def test_comment_role_and_batch_normalized():
    src = '# 设计: 路灯、星轨、烛微、小林 共同设计（第153批 T-143d 落地）\n'
    out = ep._normalize_py_text(src)
    assert "内部协作者" in out, "注释角色名须归一"
    assert "第153批" not in out and "T-143d" not in out, "注释批次号须归一"
    # 该注释含「设计」协作上下文 ⇒ 路灯一并归一（对照 test_isolated_road_name_in_comment_retained）
    assert "路灯" not in out, "含协作上下文的注释中「路灯」须归一"


def test_docstring_normalized():
    src = '"""模块文档：由路灯与星轨协作完成，第153批 T-143d 落地。"""\n'
    out = ep._normalize_py_text(src)
    assert "内部协作者" in out
    assert "第153批" not in out and "T-143d" not in out
    assert "路灯是第一个数字生命" not in out  # 该句不在本 docstring 内，仅校验归一发生


def test_seed_memories_plain_string_protected():
    # 双引号普通数据字符串：SEED_MEMORIES 身份值 / self.name —— 绝不能归一
    src = 'SEED_MEMORIES = {"name": "路灯", "role": "数字生命"}\n'
    out = ep._normalize_py_text(src)
    assert '"name": "路灯"' in out, "SEED_MEMORIES 身份值须保留"
    src2 = 'self_name = "路灯"\n'
    out2 = ep._normalize_py_text(src2)
    assert 'self_name = "路灯"' in out2, "代码字符串身份值须保留"


def test_worldview_guard_keeps_road_name():
    # 世界观句整体保留「路灯」，仅归一同句的星轨
    src = '"""路灯是第一个数字生命，由星轨命名。"""\n'
    out = ep._normalize_py_text(src)
    assert "路灯是第一个数字生命" in out, "世界观句须整体保留"
    assert "星轨" not in out and "内部协作者" in out, "同句其他角色名仍须归一"


def test_isolated_road_name_in_comment_retained():
    # 注释中孤立「路灯」无角色/协作上下文 → 保留
    src = "# 路灯 PHASE17-1.3 增设推理开关\n"
    out = ep._normalize_py_text(src)
    assert "路灯" in out, "孤立「路灯」须保留"


def test_dotfile_gitignore_normalized():
    # T153-3② 修复点：os.path.splitext('.gitignore') 返空 ext，原写法永远匹配不到
    raw = "# ===== 15. data目录整体忽略（2026-09-15 星轨完善）=====\n".encode("utf-8")
    out = ep.export_normalize_text(".gitignore", raw)
    assert out != raw, ".gitignore 必须被归一"
    assert "星轨".encode("utf-8") not in out and "内部协作者".encode("utf-8") in out
    # .gitattributes 同样按 basename 收口
    raw2 = "# 由星轨维护的属性和路径\n".encode("utf-8")
    out2 = ep.export_normalize_text(".gitattributes", raw2)
    assert out2 != raw2 and "星轨".encode("utf-8") not in out2


def test_nonpy_document_line_normalized():
    raw = "由星轨在第153批裁决，路灯是第一个数字生命保持不变。".encode("utf-8")
    out = ep.export_normalize_text("docs/x.md", raw).decode("utf-8")
    assert "内部协作者" in out and "第153批" not in out
    assert "路灯是第一个数字生命" in out


def test_fstring_literal_normalized():
    # Python 3.12 f-string 字面量（FSTRING_MIDDLE）→ 归一；{expr} 占位不动
    src = 'msg = f"由星轨在{step}步完成"\n'
    out = ep._normalize_py_text(src)
    assert "内部协作者" in out, "f-string 字面量角色名须归一"
    assert "{step}" in out, "f-string 占位须保留"


def test_binary_and_decode_error_returned_unchanged():
    b = b"\x00\x01\x02"
    assert ep.export_normalize_text("x.png", b) == b
    # 无法 utf-8 解码的字节（含非法序列）原样返回
    bad = b"\xff\xfe\xe5\x00"
    assert ep.export_normalize_text("x.bin", bad) == bad


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print("PASS", k)
    print("ALL_T153_NORMALIZE_OK")



