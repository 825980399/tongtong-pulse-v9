"""verify_phase17_integration —— PHASE17 阶段一 · 接线（集成）验证

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as CFG
from nucleus.synapsys.ResonanceEngine import ResonanceEngine

_PASS, _FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        _PASS.append(name)
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _FAIL.append(name)
        print(f"  [FAIL] {name} —— {detail}")


_TMP = tempfile.mkdtemp(prefix="p17_integ_")


def use_tmp_store(tag: str) -> None:
    d = os.path.join(_TMP, tag)
    os.makedirs(d, exist_ok=True)
    CFG.SEMANTIC_KERNEL_CONFIG["vector_file"] = os.path.join(d, "vectors.npz")
    CFG.SEMANTIC_KERNEL_CONFIG["vector_meta_file"] = os.path.join(d, "vectors_meta.json")
    from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
    from nucleus.semantic.VectorStore import VectorStore
    VectorStore._instance = None
    AsyncEncodeQueue._instance = None


def reset_state() -> None:
    import nucleus.semantic.SemanticKernelService as S
    S._state.update({"installed": False, "reason": "", "vectors": 0,
                     "queue_running": False, "engine_injected": False})


# ============================================================
def t1_switch_off():
    print("\n[T1] 开关关闭时零侵入")
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = False
    use_tmp_store("off")
    reset_state()

    from nucleus.semantic.SemanticKernelService import get_state, install
    eng = ResonanceEngine()
    eng._semantic_cfg = None
    pool = object()          # 任意占位

    r = install(eng, pool, start_queue=True)
    check("T1-a install 返回 installed=False", r["installed"] is False,
          f"reason={r['reason'][:60]}")
    check("T1-b 未给共振引擎注入 provider",
          eng._vector_provider is None)
    check("T1-c 状态可查询且不抛异常", isinstance(get_state(), dict))

    # 行为零变化：语义 map 恒为空
    q = {"memory_dim": {"frequency_signature": 1.0},
         "payload": {"content": "任意查询"}}
    m = eng._build_semantic_map(q, [{"node_id": "x"}])
    check("T1-d 语义 map 恒为空（走纯关键词）", m == {}, f"={m}")


def t2_switch_on():
    print("\n[T2] 开关打开时正确接线")
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    use_tmp_store("on")
    reset_state()

    from nucleus.semantic.SemanticKernelService import get_state, install
    from nucleus.semantic.VectorStore import get_vector_store

    eng = ResonanceEngine()
    eng._semantic_cfg = None
    store = get_vector_store()
    store._ensure_loaded()

    r = install(eng, None, start_queue=True)
    check("T2-a install 返回 installed=True", r["installed"] is True,
          f"reason={r.get('reason')}")
    check("T2-b provider 已注入共振引擎",
          eng._vector_provider is not None)
    check("T2-c 注入的正是 VectorStore 实例",
          eng._vector_provider is store)
    st = get_state()
    check("T2-d 状态包含向量库信息", "store" in st and "count" in st["store"],
          f"count={st.get('store', {}).get('count')}")
    print(f"         ↑ 编码器状态：{st.get('encoder', {}).get('state')}"
          f"（沙箱无模型 → disabled，属预期）")


def t3_main_wiring():
    print("\n[T3] main.py 接线代码在位（静态检查）")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "main.py"), encoding="utf-8").read()

    check("T3-a 启动流程调用了 install()",
          "_install_semantic_kernel(self.resonance_engine, self.node_pool)" in src)
    check("T3-b 退出流程显式调用 shutdown()（os._exit 会跳过 atexit）",
          "_shutdown_semantic_kernel()" in src)
    check("T3-c 接线被 try/except 包裹（失败不影响主链路）",
          src.count("语义内核接入失败（不影响主链路）") >= 1)
    # 退出落盘必须在**真正的 os._exit 调用**之前。
    # 注意：main.py 注释里也出现过 "os._exit" 字样，用 rfind 定位真实调用点。
    i_shut = src.find("_shutdown_semantic_kernel()")
    i_exit = src.rfind("os._exit(")
    line_shut = src[:i_shut].count("\n") + 1
    line_exit = src[:i_exit].count("\n") + 1
    check("T3-d 落盘调用在真实 os._exit 之前（否则不生效）",
          0 < i_shut < i_exit,
          f"shutdown@L{line_shut} < os._exit@L{line_exit}")


def t4_shutdown():
    print("\n[T4] 退出落盘（os._exit 场景）")
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    use_tmp_store("shutdown")
    reset_state()

    import numpy as np

    from nucleus.semantic.SemanticKernelService import install, shutdown
    from nucleus.semantic.VectorStore import get_vector_store

    store = get_vector_store()
    store._ensure_loaded()
    install(ResonanceEngine(), None, start_queue=True)

    v = np.zeros(512, dtype=np.float32)
    v[0] = 1.0
    ok, _ = store.put("persist_me", "退出前写入", v)
    check("T4-a 写入成功但未达 flush 阈值", ok and store.status()["dirty"] > 0,
          f"dirty={store.status()['dirty']}")

    shutdown()      # ★模拟框架退出（os._exit 前显式调用）
    check("T4-b shutdown 后 dirty 归零（已落盘）",
          store.status()["dirty"] == 0)
    check("T4-c 文件已生成", os.path.exists(store.status()["vec_file"]))

    # 重新加载验证数据还在
    from nucleus.semantic.VectorStore import VectorStore
    VectorStore._instance = None
    s2 = VectorStore.get_instance()
    s2._ensure_loaded()
    check("T4-d 重启后数据仍在（未丢失）", s2.status()["count"] == 1,
          f"count={s2.status()['count']}")


def t5_degrade_chain():
    print("\n[T5] 编码器不可用时整条链路安全降级")
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    use_tmp_store("degrade")
    reset_state()

    from nucleus.semantic.SemanticKernelService import install
    from nucleus.semantic.VectorStore import get_vector_store

    eng = ResonanceEngine()
    eng._semantic_cfg = None
    store = get_vector_store()
    store._ensure_loaded()
    install(eng, None, start_queue=False)

    nodes = [{"node_id": f"d{i}", "frequency_signature": 100.0 + i,
              "hebbian_weight": 0.4, "activation_count": 7,
              "space_path": "/kb/d"}
             for i in range(5)]
    q = {"memory_dim": {"frequency_signature": 100.0},
         "space_dim": {"path": "/kb/d"},
         "payload": {"content": "查询内容"}}

    try:
        res = eng.resonate(q, nodes, top_k=3)
        check("T5-a 检索正常返回结果（未因编码器不可用而空窗）",
              len(res) > 0, f"返回 {len(res)} 条")
        check("T5-b 分数正常（非 0 / 非 NaN）",
              all(r["score"] > 0 for r in res),
              f"top3={[r['score'] for r in res]}")
    except Exception as _e:
        check("T5-a 检索正常返回结果", False, f"抛异常: {type(_e).__name__}: {_e}")

    # 空库时 search_by_text 返回空
    check("T5-c 空库检索返回空列表（不报错）",
          store.search_by_text("任意", 5) == [])


def t6_startup_smoke():
    print("\n[T6] main.py 启动冒烟（复用最近一次启动日志）")
    for path in ("/tmp/startup_wire.log", "/tmp/startup_13.log",
                 "/tmp/startup_05.log"):
        if not os.path.exists(path):
            continue
        txt = open(path, encoding="utf-8", errors="ignore").read()
        if "曈曈已就绪" in txt:
            bad = ("Traceback (most recent call last)" in txt
                   or "语义内核接入失败" in txt)
            check(f"T6 启动日志 {os.path.basename(path)}：就绪且无异常",
                  not bad, "存在 Traceback" if bad else "正常就绪")
            return
    check("T6 启动日志", False, "未找到启动日志（请先运行 python main.py）")


def main():
    print("=" * 68)
    print("  PHASE17 阶段一 · 接线（集成）验证")
    print("  零件装到车上没有？语义内核是否真正接入框架")
    print("=" * 68)

    orig = dict(CFG.SEMANTIC_KERNEL_CONFIG)
    try:
        t1_switch_off()
        t2_switch_on()
        t3_main_wiring()
        t4_shutdown()
        t5_degrade_chain()
        t6_startup_smoke()
    finally:
        CFG.SEMANTIC_KERNEL_CONFIG.clear()
        CFG.SEMANTIC_KERNEL_CONFIG.update(orig)
        from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
        from nucleus.semantic.VectorStore import VectorStore
        VectorStore._instance = None
        AsyncEncodeQueue._instance = None
        shutil.rmtree(_TMP, ignore_errors=True)

    print("\n" + "=" * 68)
    print(f"  结果：通过 {len(_PASS)} 项，失败 {len(_FAIL)} 项")
    if _FAIL:
        print(f"  失败项：{_FAIL}")
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
