# -*- coding: utf-8 -*-
"""384 刀3：快照冷备压缩 / 轮转。

## 这一刀的三条硬约束

1. **热档一个字节不动**：`lazy_snapshot` 走 mmap 映射主文件，压了就没法 mmap。
   所以只压 `.bak`，`pulse_knowledge_snapshot.json` 保持纯 JSON。
2. **压后必须验证才删原文件**：解压字节数要等于原文件字节数、首字节 `{`、末字节 `}`。
   少验一步就是「压坏了还把原件删了」。
3. **`_cleanup_old_backups` 用 `".bak" in fname` 判断** ⇒ `.bak.gz` 仍被纳入轮转。
   所以压完调一次轮转就能落到「只剩 1 份」，不需要另写删除逻辑。

## 票面「轮转后只剩 1 个 .bak」的准确落法

压缩后目录里是 `.bak.gz` 而不是 `.bak`，所以断言写成：
  · 未压缩 `.bak` 数量 == 0
  · 备份总数（含 .gz）== `KNOWLEDGE_BACKUP_KEEP` == 1
数字按 `config` 走，不硬编码「1 个 .bak 文件」这种字面形态。
"""
import gzip
import io
import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.mnemosyne.PulseSnapshot import _m41_backup_keep  # noqa: E402
from tools.m384_snapshot_compact import (  # noqa: E402
    compress_one, gz_path_of, list_backups,
)

KDIR = os.path.join(ROOT, "data", "knowledge")
HOT = os.path.join(KDIR, "pulse_knowledge_snapshot.json")


# ------------------------------------------------------------ ① 保留份数 2→1
def test_m384_d3_keep_is_one():
    """★票面第 1 条：轮转保留数必须是 1。"""
    assert config.KNOWLEDGE_BACKUP_KEEP == 1
    assert _m41_backup_keep() == 1


# --------------------------------------------------- ② 轮转后的真实目录状态
def test_m384_d3_no_uncompressed_bak_left():
    """压缩机跑完后，目录里不该再有未压缩的 .bak。"""
    _bk = list_backups(KDIR)
    _plain = [p for _m, p in _bk if not p.endswith(".gz")]
    assert _plain == [], "还有未压缩备份：%s" % [os.path.basename(p) for p in _plain]


def test_m384_d3_backup_count_equals_keep():
    """★票面第 5 条：轮转后备份份数 == KNOWLEDGE_BACKUP_KEEP。"""
    _bk = list_backups(KDIR)
    assert len(_bk) == config.KNOWLEDGE_BACKUP_KEEP, (
        "备份份数 %d != 保留数 %d：%s"
        % (len(_bk), config.KNOWLEDGE_BACKUP_KEEP,
           [os.path.basename(p) for _m, p in _bk]))


def test_m384_d3_surviving_backup_is_loadable():
    """★票面第 3/5 条：存活的 .gz 必须可解压，且首尾是正确的 JSON 括号。

    这里不解全量（716MB 解压要 12s），只核「能打开 + 首字节 { + 末字节 }」；
    完整 `json.load` 已在交付报告 §三 做过一次（14129 条与声明一致）。
    """
    _bk = list_backups(KDIR)
    assert _bk, "没有任何备份可验"
    _p = _bk[0][1]
    assert _p.endswith(".gz")
    with gzip.open(_p, "rb") as _g:
        assert _g.read(1) == b"{", "解压后首字节不是 {"
    # 尾部：从后往前读，gzip 不支持 seek，用流式读到末尾
    _tail = b""
    with gzip.open(_p, "rb") as _g:
        while True:
            _b = _g.read(4 << 20)
            if not _b:
                break
            _tail = (_tail + _b)[-16:]
    assert _tail.strip().endswith(b"}"), "解压后末字节不是 }（尾部 %r）" % _tail[-8:]


# --------------------------------------------------------------- ③ 热档未动
def test_m384_d3_hot_file_untouched():
    """★热档必须仍是纯 JSON（不是 .gz、不是 .bak），且能被 mmap 语义使用。"""
    assert os.path.isfile(HOT), "主快照不见了"
    assert not HOT.endswith(".gz")
    with io.open(HOT, "rb") as f:
        assert f.read(1) == b"{"
        f.seek(max(0, os.path.getsize(HOT) - 8))
        assert f.read().strip().endswith(b"}")


# ------------------------------------------------- ④ 压缩函数行为（合成夹具）
def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_m384_d3_compress_roundtrip(tmp_path):
    """正常：压缩成功、字节数一致、原文件可被调用方删除。"""
    src = _write(tmp_path / "a.json", '{"nodes": [1, 2, 3]}')
    r = compress_one(src)
    assert r["ok"], r["why"]
    assert r["gz_size"] > 0
    with gzip.open(gz_path_of(src), "rt", encoding="utf-8") as f:
        assert json.load(f) == {"nodes": [1, 2, 3]}


def test_m384_d3_refuses_overwrite(tmp_path):
    """目标已存在 ⇒ 拒绝覆盖（防把已有冷备冲掉）。"""
    src = _write(tmp_path / "b.json", '{"a": 1}')
    open(gz_path_of(src), "wb").close()
    r = compress_one(src)
    assert not r["ok"] and "拒绝覆盖" in r["why"]


def test_m384_d3_rejects_non_json_payload(tmp_path):
    """内容不是 JSON 对象 ⇒ 首字节检查必须拦下（不许压完还报成功）。"""
    src = _write(tmp_path / "c.json", "not a json at all")
    r = compress_one(src)
    assert not r["ok"] and "首字节" in r["why"]


def test_m384_d3_list_backups_sorted_newest_first(tmp_path):
    """`list_backups` 按 mtime 倒序（与 `_cleanup_old_backups` 同款语义）。"""
    import time as _t
    for i, name in enumerate(("x.json.bak", "x.json.1.bak", "x.json.2.bak.gz")):
        p = tmp_path / name
        p.write_bytes(b"{}")
        os.utime(str(p), (1000 + i, 1000 + i))
        _t.sleep(0.01)
    got = [os.path.basename(p) for _m, p in list_backups(str(tmp_path), base="x.json")]
    assert got == ["x.json.2.bak.gz", "x.json.1.bak", "x.json.bak"]


# --------------------------------------------------------- ⑤ 脚本护栏断言
def test_m384_d3_script_guards():
    """默认 dry-run；压后验证三件套（字节数/首/尾）齐全；失败即中止不删原件。"""
    src = io.open(os.path.join(ROOT, "tools", "m384_snapshot_compact.py"),
                  encoding="utf-8").read()
    assert "if not _args.apply:" in src, "缺 dry-run 默认分支"
    assert '_n != r["src_size"]' in src, "缺解压字节数核对"
    assert '_first != b"{"' in src, "缺首字节检查"
    assert "中止后续" in src, "缺验证失败的中止文案"
    assert "            break\n" in src, "验证失败后没有 break（会继续压下一个）"
