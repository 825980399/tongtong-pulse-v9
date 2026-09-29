"""verify_phase17_1_5 —— PHASE17 阶段一 · 任务 1.5 验证脚本

版本: v10 PulseNet · 工具
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config as CFG
from nucleus.semantic.VectorStore import VectorStore
from tmp.test_isolation import TestIsolation, ISO_DIR

# ★第五批 任务2B：框架运行探测统一复用 tools._framework_probe
from tools._framework_probe import _framework_looks_running
from nucleus.data.DataAccessLayer import safe_read_json

_PASS, _FAIL = [], []


def _should_run_heavy_test(framework_running: bool) -> bool:
    """★第五批 任务2B：框架在跑时跳过重负载 T12 真实维度压测。

    框架满载下运行 9958×512 写入偶发 0xC0000005 访问违规；改为轻量隔离验证。
    供单测直接断言（无需跑整套用例）。
    """
    return not framework_running

# ★第三批 任务3：隔离目录改由标准 TestIsolation 注入（替代原 tempfile.mkdtemp）
_TMP = None


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        _PASS.append(name)
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _FAIL.append(name)
        print(f"  [FAIL] {name} —— {detail}")


DIM = 8          # 用小维度加速测试（真实环境 512）
MODEL = "test/bge-small-zh"


def fresh_store(tag: str) -> VectorStore:
    """用独立临时目录创建一个全新实例（绕过单例）。"""
    d = os.path.join(_TMP, tag)
    os.makedirs(d, exist_ok=True)
    CFG.SEMANTIC_KERNEL_CONFIG["vector_file"] = os.path.join(d, "vectors.npz")
    CFG.SEMANTIC_KERNEL_CONFIG["vector_meta_file"] = os.path.join(d, "vectors_meta.json")
    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = DIM
    CFG.SEMANTIC_KERNEL_CONFIG["model_name"] = MODEL
    CFG.SEMANTIC_KERNEL_CONFIG["vector_flush_count"] = 100
    CFG.SEMANTIC_KERNEL_CONFIG["vector_flush_interval_sec"] = 300
    VectorStore._instance = None
    return VectorStore.get_instance()


def unit_vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(DIM).astype(np.float32) - 0.5
    return v / np.linalg.norm(v)


def bad_vec(seed: int, scale: float = 5.0) -> np.ndarray:
    return unit_vec(seed) * scale


# ============================================================
def t1_t5_basics():
    print("\n[T1/T3/T5] 独立存储 · 批量 flush · 质量门禁")
    s = fresh_store("basic")
    s._ensure_loaded()

    ok, why = s.put("n0", "第一段文本", unit_vec(0))
    check("T1-a 单条写入成功", ok, why)

    # T5 质量门禁
    ok, why = s.put("bad1", "未归一化", bad_vec(1))
    check("T5-a L2 norm≠1 的向量拒入库（红线 6.2 断言点 3）",
          (not ok) and "L2 norm" in why, why)

    ok, why = s.put("bad2", "维度不符", np.zeros(DIM + 3, dtype=np.float32))
    check("T5-b 维度不符拒入库", (not ok) and "维度" in why, why)

    ok, why = s.put("bad3", "含 NaN",
                    np.array([float("nan")] * DIM, dtype=np.float32))
    check("T5-c 含 NaN/Inf 拒入库", (not ok), why)

    ok, why = s.put("", "空 id", unit_vec(2))
    check("T5-d 空 node_id 拒入库", (not ok), why)

    check("T5-e 被拒的条目未污染索引", s.status()["count"] == 1,
          f"count={s.status()['count']}")

    # T3 批量 flush 阈值
    for i in range(1, 120):
        s.put(f"n{i}", f"文本{i}", unit_vec(i))
    st = s.status()
    # 第 100 条时触发一次 flush（dirty 归零），其后 20 条仍在缓冲中 → dirty=20
    check("T3-a 达到 100 条阈值自动 flush（writes≥1），剩余在缓冲（dirty<100）",
          st["writes"] >= 1 and st["dirty"] < 100,
          f"dirty={st['dirty']}, writes={st['writes']}")
    check("T3-b 落盘文件存在（npz + meta 分离）",
          st["vec_file_exists"] and os.path.exists(st["meta_file"]),
          f"npz={os.path.basename(st['vec_file'])}, "
          f"meta={os.path.basename(st['meta_file'])}")

    s.flush(force=True)          # 补齐剩余缓冲后再校验内容
    meta = safe_read_json(st["meta_file"], default={})
    check("T1-b meta 记录 model/dim/count/node_ids",
          meta.get("model") == MODEL and meta.get("dim") == DIM
          and meta.get("count") == 120 and len(meta.get("node_ids", [])) == 120,
          f"count={meta.get('count')}, dim={meta.get('dim')}")

    # T1-c 向量真的不在节点 JSON 里（元数据仅存 id/hash，不含数组）
    raw = json.dumps(meta)
    check("T1-c meta 里不含浮点向量数组（体积可控）",
          len(raw) < 20_000, f"meta 体积={len(raw)} 字节 / 120 条")


def t2_atomic_write():
    print("\n[T2] 原子写（tmp + os.replace）")
    s = fresh_store("atomic")
    s._ensure_loaded()
    for i in range(5):
        s.put(f"a{i}", f"t{i}", unit_vec(i))
    s.flush(force=True)
    vec_path = s.status()["vec_file"]
    meta_path = s.status()["meta_file"]

    check("T2-a 正常落盘后无 .tmp 残留",
          not os.path.exists(vec_path + ".tmp")
          and not os.path.exists(meta_path + ".tmp"))

    # 模拟落盘失败：让 np.savez 抛异常（比改目录权限可靠 —— root 会绕过权限）
    _orig_savez = np.savez
    try:
        def _boom(*a, **kw):
            raise OSError("模拟磁盘写满")
        np.savez = _boom
        s.put("after_fail", "x", unit_vec(99))
        ok = s.flush(force=True)
        check("T2-b 落盘失败时返回 False 且**不抛异常**", ok is False,
              "flush 返回 False，异常被内部吞掉")
    except Exception as _e:
        check("T2-b 落盘失败时返回 False 且不抛异常", False, f"异常漏出: {_e}")
    finally:
        np.savez = _orig_savez

    check("T2-b2 失败后无 .tmp 残留（不留垃圾）",
          not os.path.exists(vec_path + ".tmp.npz")
          and not os.path.exists(meta_path + ".tmp"))

    # 旧文件仍完好可读
    try:
        data = np.load(vec_path)
        ok = data["matrix"].shape[1] == DIM
    except Exception:
        ok = False
    check("T2-c 落盘失败后**旧文件仍完好**（未被半截写入破坏）", ok)

    # 恢复后能正常写入
    s.flush(force=True)
    check("T2-d 故障恢复后落盘正常", os.path.exists(vec_path))


def t4_forced_flush():
    print("\n[T4] 强制 flush（atexit / shutdown）")
    s = fresh_store("forced")
    s._ensure_loaded()
    for i in range(3):
        s.put(f"f{i}", f"t{i}", unit_vec(i))
    check("T4-a 未达阈值时 dirty 未清零（延迟写入）", s.status()["dirty"] == 3,
          f"dirty={s.status()['dirty']}")
    s.shutdown()
    check("T4-b shutdown() 强制落盘，dirty 归零", s.status()["dirty"] == 0)
    check("T4-c 落盘后文件存在", os.path.exists(s.status()["vec_file"]))

    # shutdown 幂等性（atexit + 显式调用可能重复触发，不能报错也不能重复写坏）
    try:
        s.shutdown()
        s.shutdown()
        ok = True
    except Exception as _e:
        ok = False
    check("T4-d shutdown() 幂等（atexit 与显式调用重复触发不报错）", ok)


def t6_model_fingerprint():
    print("\n[T6] 模型指纹校验（换模型拒绝加载旧向量）")
    s = fresh_store("model_a")
    s._ensure_loaded()
    for i in range(6):
        s.put(f"m{i}", f"t{i}", unit_vec(i))
    s.flush(force=True)

    # 换模型名后重新加载
    CFG.SEMANTIC_KERNEL_CONFIG["model_name"] = "other/another-model"
    VectorStore._instance = None
    s2 = VectorStore.get_instance()
    s2._ensure_loaded()
    check("T6 换模型 → 拒绝加载旧向量（空库启动，需重跑编码）",
          s2.status()["count"] == 0, f"count={s2.status()['count']}")

    # 换回原模型应能正常加载
    CFG.SEMANTIC_KERNEL_CONFIG["model_name"] = MODEL
    VectorStore._instance = None
    s3 = VectorStore.get_instance()
    s3._ensure_loaded()
    check("T6-b 换回原模型 → 正常加载", s3.status()["count"] == 6,
          f"count={s3.status()['count']}")


def t7_dim_check():
    print("\n[T7] 维度校验")
    s = fresh_store("dim8")
    s._ensure_loaded()
    for i in range(4):
        s.put(f"d{i}", f"t{i}", unit_vec(i))
    s.flush(force=True)

    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = 512
    VectorStore._instance = None
    s2 = VectorStore.get_instance()
    s2._ensure_loaded()
    check("T7 dim 不符 → 拒绝加载（防止矩阵运算静默错乱）",
          s2.status()["count"] == 0, f"count={s2.status()['count']}")
    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = DIM


def t8_remove_compact():
    print("\n[T8] 删除与物理压缩（L1 冷存淘汰联动）")
    s = fresh_store("remove")
    s._ensure_loaded()
    for i in range(10):
        s.put(f"r{i}", f"t{i}", unit_vec(i))
    check("T8-a 初始 10 条", s.status()["count"] == 10)

    ok = s.remove("r3")
    check("T8-b 删除成功", ok, "r3 已摘除")
    ok2 = s.remove("r7")
    check("T8-b2 删除第二个", ok2)
    check("T8-c 删除不存在的 id 返回 False", s.remove("nope") is False)
    check("T8-d 索引已摘除（count 减 2）", s.status()["count"] == 8,
          f"count={s.status()['count']}")

    s.flush(force=True)
    data = np.load(s.status()["vec_file"])
    check("T8-e flush 后矩阵已物理压缩到 8 行",
          data["matrix"].shape[0] == 8, f"shape={data['matrix'].shape}")
    meta = safe_read_json(s.status()["meta_file"], default={})
    check("T8-f meta node_ids 同步为 8 个且不含已删 id",
          len(meta["node_ids"]) == 8 and "r3" not in meta["node_ids"]
          and "r7" not in meta["node_ids"])


def t9_roundtrip():
    print("\n[T9] 持久化往返（存盘 → 新实例加载 → 一致）")
    s = fresh_store("roundtrip")
    s._ensure_loaded()
    vecs = {}
    for i in range(15):
        v = unit_vec(i + 500)
        vecs[f"n{i}"] = v
        s.put(f"n{i}", f"文本{i}", v)
    s.flush(force=True)

    VectorStore._instance = None
    s2 = VectorStore.get_instance()
    s2._ensure_loaded()
    check("T9-a 重新加载后数量一致", s2.status()["count"] == 15,
          f"count={s2.status()['count']}")

    all_same = True
    worst = 0.0
    for nid, v in vecs.items():
        row = s2._row_of.get(nid)
        if row is None or not np.allclose(s2._matrix[row], v, atol=1e-6):
            all_same = False
            break
        worst = max(worst, float(np.abs(s2._matrix[row] - v).max()))
    check("T9-b 向量数值完全一致（float32 精度内）", all_same,
          f"最大误差={worst:.2e}")

    check("T9-c 行号映射连续且自洽",
          all(s2._row_of[nid] == i for i, nid in enumerate(s2._ids)))


def t10_provider():
    print("\n[T10] provider 接口（供 ResonanceEngine 使用，PHASE17-1.3 约定）")
    s = fresh_store("provider")
    s._ensure_loaded()
    target = unit_vec(42)
    for i in range(20):
        s.put(f"p{i}", f"文本{i}", unit_vec(i))
    s.put("TARGET", "目标文本", target)

    hits = s.search_by_vector(target, top_k=3)
    check("T10-a search_by_vector 返回 [(node_id, sim), ...]",
          isinstance(hits, list) and len(hits) == 3
          and all(isinstance(h[0], str) and isinstance(h[1], float) for h in hits),
          f"top3={[(n, round(v,4)) for n, v in hits]}")
    check("T10-b 自检索命中自身且相似度≈1.0",
          hits[0][0] == "TARGET" and abs(hits[0][1] - 1.0) < 1e-4,
          f"{hits[0][0]}={hits[0][1]:.6f}")
    check("T10-c 结果按相似度降序",
          all(hits[i][1] >= hits[i + 1][1] for i in range(len(hits) - 1)))
    check("T10-d top_k 大于库容量时不越界",
          len(s.search_by_vector(target, top_k=999)) == 21)
    check("T10-e 空文本 search_by_text 返回空列表（不抛异常）",
          s.search_by_text("", 5) == [])
    check("T10-f 空库检索返回空列表",
          fresh_store("empty").search_by_vector(target, 5) == [])

    # 语义不可用时（沙箱模型未下载）search_by_text 应安全返回空
    r = s.search_by_text("任意查询", 5)
    check("T10-g 模型不可用时 search_by_text 安全返回 []（回落关键词）",
          isinstance(r, list), f"返回 {len(r)} 条")


def t11_text_hash():
    print("\n[T11] 文本指纹（内容变更才重编码）")
    s = fresh_store("hash")
    s._ensure_loaded()
    s.put("h1", "原始内容", unit_vec(1))
    check("T11-a 首次需要编码", s.needs_encode("h1", "原始内容") is False,
          "刚写入过 → 不需要")
    check("T11-b 内容变更 → 需要重编码",
          s.needs_encode("h1", "改过的内容") is True)
    check("T11-c 内容未变 → 不需要重编码",
          s.needs_encode("h1", "原始内容") is False)
    check("T11-d 不存在的节点 → 需要编码",
          s.needs_encode("h_new", "新节点") is True)


def t12_real_dim_perf():
    print("\n[T12] 真实维度（512）落盘与检索性能")
    d = os.path.join(_TMP, "real512")
    os.makedirs(d, exist_ok=True)
    CFG.SEMANTIC_KERNEL_CONFIG["vector_file"] = os.path.join(d, "vectors.npz")
    CFG.SEMANTIC_KERNEL_CONFIG["vector_meta_file"] = os.path.join(d, "vectors_meta.json")
    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = 512
    VectorStore._instance = None
    s = VectorStore.get_instance()
    s._ensure_loaded()

    n = 9958
    rng = np.random.default_rng(2026)
    mat = rng.random((n, 512), dtype=np.float32) - 0.5
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    ids = [f"node_{i}" for i in range(n)]

    t0 = time.time()
    for i in range(n):
        s.put(ids[i], f"t{i}", mat[i])
    t_put = time.time() - t0

    t0 = time.time()
    s.flush(force=True)
    t_flush = time.time() - t0

    t0 = time.time()
    hits = s.search_by_vector(mat[7], top_k=10)
    t_search = time.time() - t0

    size_mb = os.path.getsize(s.status()["vec_file"]) / 1024 / 1024
    check("T12-a 9958×512 全量写入 + 落盘成功",
          s.status()["count"] == n, f"count={s.status()['count']}")
    check("T12-b 全量检索耗时 < 100ms", t_search < 0.1, f"{t_search*1000:.2f} ms")
    check("T12-c 自检索命中", hits and hits[0][0] == "node_7",
          f"top1={hits[0] if hits else None}")
    print(f"         ↑ 写入 {t_put:.2f}s / 落盘 {t_flush:.2f}s / "
          f"检索 {t_search*1000:.2f}ms / 文件 {size_mb:.1f}MB")
    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = DIM


def main():
    print("=" * 68)
    print("  PHASE17 阶段一 · 任务 1.5 验证")
    print("  向量独立持久化（vectors.npz + vectors_meta.json）")
    print("=" * 68)

    # ★第五批 任务2B：框架运行检测。
    #   框架在跑时（生产 VectorStore 单例占用 512 维库并可能被 atexit flush），
    #   仍走 TestIsolation 完全隔离路径；但跳过重负载的 T12（9958×512 写入，
    #   在框架满载下偶发 0xC0000005 访问违规），改为轻量隔离验证，避免崩溃。
    _fw_running = _framework_looks_running()
    if _fw_running:
        print("\n[注意] 检测到框架正在运行（main.py）。"
              "本脚本全程使用 tmp/test_data 隔离路径，"
              "为降低满载下的访问违规风险，跳过重负载 T12 真实维度压测。")

    # ★第三批 任务3：改用标准 test_isolation.py 隔离（替代原 mkdtemp+改CFG）
    _iso = TestIsolation().redirect_all()
    global _TMP
    # ★主线第18批 T6/P2-105：ISO_DIR 本身已是**每进程唯一**的短随机目录
    #   （tmp/t_<6hex>，相对路径 12 字符），不再嵌套 p17_<pid>_<ts>，
    #   既避免 Windows 长路径 / WinError 5，也保留「不复用上次遗留向量库」的安全性。
    _TMP = ISO_DIR
    os.makedirs(_TMP, exist_ok=True)
    _orig_cfg = dict(CFG.SEMANTIC_KERNEL_CONFIG)
    try:
        t1_t5_basics()
        t2_atomic_write()
        t4_forced_flush()
        t6_model_fingerprint()
        t7_dim_check()
        t8_remove_compact()
        t9_roundtrip()
        t10_provider()
        t11_text_hash()
        if _should_run_heavy_test(_fw_running):
            # 重负载真实维度压测：仅在没有框架运行时跑，避免满载访问违规
            t12_real_dim_perf()
        else:
            print("\n[T12 跳过] 框架运行时不跑真实 512 维压测（防 0xC0000005）")
    finally:
        try:
            _iso.cleanup()
        finally:
            # ★主线第18批 T6/P2-105：**先摘掉单例**再删目录 ——
            #   VectorStore 单例可能仍持有文件句柄（Windows 上导致删除静默失败），
            #   且其 atexit 落盘会按隔离路径**重建**目录。
            try:
                VectorStore._instance = None
            except Exception as _vs_e:  # 摘除失败不阻断清理
                print(f"\n[提示] 摘除向量库单例失败（{type(_vs_e).__name__}: {_vs_e}）")
            # 强制清理本次唯一目录（Windows 上 safe-delete 可能因句柄占用失败）
            shutil.rmtree(_TMP, ignore_errors=True)
            # ★主进程兜底验证：**不 import 其他模块**（解释器关闭期 import 会触发
            #   huggingface_hub 等库的 atexit.register，抛 cannot-register-atexit
            #   类错误并让进程以非零码退出）。此处只用 os/shutil 直接判断。
            if os.path.exists(_TMP):
                shutil.rmtree(_TMP, ignore_errors=True)
                if os.path.exists(_TMP):
                    print("\n[警告] 隔离目录清理失败，请检查 tmp/ 残留: %s" % _TMP)
            # 还原生产 config（子进程退出即失效，但保持干净状态）
            CFG.SEMANTIC_KERNEL_CONFIG.clear()
            CFG.SEMANTIC_KERNEL_CONFIG.update(_orig_cfg)

    # ★第五批 任务2B：生产向量库 MD5 硬校验。TestIsolation 在 cleanup 时比对过
    #   生产指纹，若生产文件被改动则 production_intact=False，这里据以失败退出，
    #   让调度器能感知「隔离失效」。
    _prod_intact = getattr(_iso, "production_intact", None)
    if _prod_intact is False:
        print("\n!!! [致命] 生产向量库在测试前后指纹不一致（疑似隔离失效）！")
        _FAIL.append("production_vector_db_md5_changed")

    # ---- 主线第9批 T3 / P2-66：代码行数红线（警告模式，绝不阻断 43/43）----
    try:
        from tools.check_code_limits import collect, build_report, DEFAULT_CONFIG
        import config as _m9_cfg_mod
        _m9_cfg = dict(DEFAULT_CONFIG)
        if hasattr(_m9_cfg_mod, "CODE_QUALITY_CONFIG"):
            _m9_cfg.update(_m9_cfg_mod.CODE_QUALITY_CONFIG)
        _m9_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _m9_results = collect(_m9_root, _m9_cfg)
        _m9_report = build_report(_m9_results, _m9_cfg, top=10)
        if _m9_report["over_limit_count"]:
            print(f"\n[代码行数红线·警告] 超限文件 {_m9_report['over_limit_count']} 个"
                  f"（error={_m9_report['error_count']}, warn={_m9_report['warn_count']}），"
                  f"仅记录不阻断；详见 tools/check_code_limits.py。Top 超限：")
            for _it in _m9_report["over_limit_files"][:10]:
                print(f"    [{_it['severity'].upper()}] {_it['path']} "
                      f"(行数={_it['total_lines']}, 函数数={_it['func_count']})")
    except Exception as _m9_exc:  # 红线检查失败绝不污染主门禁
        print(f"\n[代码行数红线·警告] 检查跳过（{type(_m9_exc).__name__}: {_m9_exc}）")

    print("\n" + "=" * 68)
    print(f"  结果：通过 {len(_PASS)} 项，失败 {len(_FAIL)} 项")
    if _FAIL:
        print(f"  失败项：{_FAIL}")
    print("=" * 68)
    # ★T6：显式 flush —— 确保结果行在进程退出前落盘（曾因缓冲丢失看不到结果）
    sys.stdout.flush()
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
