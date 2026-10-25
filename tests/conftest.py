# -*- coding: utf-8 -*-
"""pytest 公共 fixture：项目根加入 sys.path、提供临时 VectorStore 工厂、进化测试基目录。

注意：本 sandbox 不具备向量编码器（BAAI/bge），故 VectorStore 测试用 FakeEncoder
（仅做维度/有限性校验）配合 `put(node, text, vec)` 直接写预计算向量，绕开编码器；
检索用真实 VectorEncoder.cosine_matrix 静态方法（import 不加载模型）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ★第158批 N-8（P1）：pytest 运行期框架日志重定向到 logs/pytest.log，
#   避免测试专用日志（VectorStore 模拟磁盘写满 / 进程池「模拟重建失败」等）
#   污染生产 pulse.log。复用 logger.py 既有 PULSE_LOG_FILE 隔离机制
#   （子进程通过环境变量指定独立日志文件）。仅当未由父进程显式指定时设置，
#   以尊重 PeriodicTestScheduler 自身可能注入的隔离日志路径。
os.environ.setdefault("PULSE_LOG_FILE", os.path.join(ROOT, "logs", "pytest.log"))

import numpy as np
import pytest

from nucleus._silent_except import silent_exc
from nucleus.semantic.VectorStore import VectorStore


class FakeEncoder:
    """替代向量编码器：仅校验维度与有限性，不加载模型。"""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def verify_vector(self, vec) -> tuple[bool, str]:
        arr = np.asarray(vec, dtype=np.float32).ravel()
        if arr.ndim != 1 or arr.shape[0] != self.dim:
            return False, f"dim mismatch: expect {self.dim} got {arr.shape[0]}"
        if not np.all(np.isfinite(arr)):
            return False, "non-finite values"
        return True, "ok"


def build_store(base_dir, dim: int = 4, model: str = "test-model",
                preloaded: bool = True) -> VectorStore:
    """构造一个指向临时目录、使用 FakeEncoder 的 VectorStore 实例。

    preloaded=True：跳过磁盘加载（用于空库写入/检索测试）。
    preloaded=False：从磁盘加载（用于持久化往返测试）。
    """
    s = VectorStore()
    s._vec_path = os.path.join(str(base_dir), "vectors.npz")
    s._meta_path = os.path.join(str(base_dir), "vectors_meta.json")
    s._dim = dim
    s._model = model
    s._encoder = FakeEncoder(dim)
    s._matrix = np.zeros((0, dim), dtype=np.float32)
    s._ids = []
    s._row_of = {}
    s._hash_of = {}
    s._updated = {}
    s._dirty = 0
    s._quality_flag_provider = None  # 关闭 quality_flag 过滤，便于断言原始检索结果
    s._loaded = bool(preloaded)
    if not preloaded:
        s._ensure_loaded()
    return s


@pytest.fixture
def tmp_store(tmp_path):
    """一个已就绪的空 VectorStore（临时目录）。"""
    return build_store(tmp_path, preloaded=True)


@pytest.fixture
def store_builder():
    """返回 build_store 工厂，便于在测试中构造第二个实例（如持久化往返）。"""
    return build_store


def _mute_project_logs_handlers():
    """★B156-6 + 第158批 N-8：给指向项目 logs/ 的 FileHandler 挂丢弃过滤器，返回 [(handler, filter)]。

    ★N-8 修正：原实现对所有 logs/ 下的 FileHandler 一律丢弃（保 pulse.log 干净但
    一并丢失测试日志）。现改为「允许 logs/pytest.log 写出」——因为 pytest 运行期
    框架根日志器（经 PULSE_LOG_FILE 环境变量）即落 logs/pytest.log，
    该文件本就是 N-8 规定的测试面日志归集点，不应被丢弃。
    """
    import logging

    _muted = []
    try:
        _logs_root = os.path.abspath(os.path.join(ROOT, "logs")).replace(
            "\\", "/").lower()
        _pytest_log = os.path.abspath(os.path.join(ROOT, "logs", "pytest.log")).replace(
            "\\", "/").lower()

        def _is_logs_handler(h):
            _bf = getattr(h, "baseFilename", None)
            if not _bf:
                return False
            _abf = os.path.abspath(_bf).replace("\\", "/").lower()
            if _abf == _pytest_log:
                return False  # ★N-8：pytest.log 本身允许写入（不再丢弃）
            return _abf.startswith(_logs_root + "/")

        def _drop(_record):
            return False

        _loggers = [logging.getLogger()]
        try:
            _loggers += [logging.getLogger(_n)
                         for _n in logging.root.manager.loggerDict]
        except Exception as _e:
            silent_exc(_e, where="tests.conftest._mute_project_logs_handlers:loggerDict")
        for _lg in _loggers:
            for _h in list(getattr(_lg, "handlers", []) or []):
                if _is_logs_handler(_h):
                    _h.addFilter(_drop)
                    _muted.append((_h, _drop))
    except Exception as _e:
        silent_exc(_e, where="tests.conftest._mute_project_logs_handlers")
        _muted = []
    return _muted


def _restore_muted_logs_handlers(muted):
    """★B156-6：搜肉隔离期挂上的丢弃过滤器。"""
    for _h, _f in (muted or []):
        try:
            _h.removeFilter(_f)
        except Exception as _e:
            silent_exc(_e, where="tests.conftest._restore_muted_logs_handlers")


@pytest.fixture(scope="session", autouse=True)
def _isolate_project_log_files():
    """★主线第22批 T3/P2-121 + 第158批 N-8：会话级隔离「写往项目 logs/ 目录」的 FileHandler。

    背景：pytest 运行会触发大量**测试专用**日志（VectorStore 模拟磁盘写满/
    模型变更/维度不符 50+ 条、进程池「模拟重建失败」ERROR 5 条、
    CallGraphAnalyzer 语法错误跳过 31 条），经 root FileHandler 写进**生产**
    logs/pulse.log，淹没真实故障线索。

    做法（第158批 N-8 演进）：
      - pytest 启动期由本 conftest 顶部设置 ``PULSE_LOG_FILE=logs/pytest.log``，
        框架根日志器（含 VectorStore / 进程池）在 pytest 运行期即直接落
        ``logs/pytest.log``，``pulse.log`` 只收真实运行事件；
      - 仅对指向生产 ``pulse.log`` 的 FileHandler 挂丢弃过滤器（不替换 handler 结构），
        ``logs/pytest.log`` 目标被显式放行（见 ``_mute_project_logs_handlers``），
        故 caplog / assertLogs 等测试内捕获能力完全不受影响。
    任何异常都不阻塞测试。
    """
    # ★B156-6 修正：原实现依赖 gitignored 的 tmp.test_log_isolation
    #   （未入库 → 静默降级为 no-op，logs/ 隔离从未真正生效）。
    #   改为内联实现，去除对 gitignored 模块的依赖
    #   （符合前置分析 T-A03「改为测试自建 fixture」结论）。
    _muted = _mute_project_logs_handlers()
    yield
    _restore_muted_logs_handlers(_muted)


# ============================================================ 框架运行期守卫（主线第57批 T1 / P2-391）
def pytest_configure(config):
    """注册 production_data 标记，避免未知标记告警。"""
    config.addinivalue_line(
        "markers",
        "production_data: 标记读取生产 data/ 目录的现状断言型测试；"
        "框架运行时由本 conftest 自动跳过（P2-391）",
    )


def is_framework_running():
    """★第57批 T1 / P2-391：检测生产框架是否在运行（实时改写 data/ 的进程）。

    框架运行时，读取生产 data/ 的现状断言型测试应跳过，避免被实时改写导致
    假失败并污染生产数据。

    判定（任一为真即视为运行中）：
      1. data/runtime.lock 存在；
      2. 进程检测：命令行**指向** main.py 的进程在跑
         （★第89批 T-89c：改用 psutil 读命令行；原 Windows `tasklist /fo csv`
          输出不含命令行，"main.py" 恒不命中 → 该分支恒假，详见 _detect_framework_process）。
         探测失败时**不**保守判定为运行中（避免沙箱/CI 环境误跳过全部生产测试）。

    可用环境变量 PULSE_TEST_FRAMEWORK_RUNNING 强制覆盖：
      "1" 强制视为运行中；"0" 强制视为已停止（便于本地/CI 验证两种场景）。
    """
    _env = os.environ.get("PULSE_TEST_FRAMEWORK_RUNNING")
    if _env == "1":
        return True
    if _env == "0":
        return False
    if _framework_lock_present():
        return True
    return _detect_framework_process()


def _framework_lock_present() -> bool:
    """★第57批 T1：`data/runtime.lock` 是否存在（独立成函数，便于单测注入）。"""
    return os.path.exists(os.path.join(ROOT, "data", "runtime.lock"))


def _cmdline_is_framework_main(cmdline) -> bool:
    """★第89批 T-89c：命令行是否**指向框架入口** main.py。

    精确匹配「某个参数的文件名恰为 main.py」，而不是子串包含 ——
    子串匹配会误命中「命令行正文里提到 main.py」的进程
    （如 `python -c "import ast; ast.parse(open('main.py').read())"`）。
    """
    for _a in cmdline or []:
        try:
            if os.path.basename(str(_a)).lower() == "main.py":
                return True
        except Exception:
            continue
    return False


def _iter_main_py_cmdlines():
    """★第89批 T-89c：用 psutil 枚举「命令行指向 main.py」的进程（排除本进程）。

    返回命令行列表；psutil 不可用时返回 ``None`` —— 以便调用方区分
    「探测可用但没找到」([]) 与「无法探测」(None) 两种情形。
    """
    try:
        import psutil
    except Exception:
        return None
    _self_pid = os.getpid()
    _out = []
    for _p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if _p.info.get("pid") == _self_pid:
                continue
            _cl = _p.info.get("cmdline") or []
            if _cmdline_is_framework_main(_cl):
                _out.append(" ".join(str(_a) for _a in _cl))
        except Exception:
            continue
    return _out


def _detect_framework_process() -> bool:
    """★第89批 T-89c：进程探测 —— 改用可读「命令行」的方式。

    ★根因（2026-09-20 实测）：原实现 Windows 走 ``tasklist /fo csv``，而该输出
    **不含命令行参数**（实测 15290 字节输出里 "main.py" 出现 **0** 次），
    故 ``"main.py" in _raw`` 恒假 —— 框架明明在跑（PID 26392 ``python.exe main.py``，
    `logs/pulse.log` mtime 秒级刷新）却判为"未运行"，
    ``production_data`` 测试不被跳过 → 必然假失败（第88批 m47 即此）。

    分层探测（psutil 优先）：
      1. **psutil**（`requirements.txt` 既有依赖：``psutil>=5.9.0``，项目内 17 个模块在用）
         —— 枚举命令行并精确匹配参数文件名 == main.py；
      2. POSIX 回退 ``pgrep -af main.py``；
      3. Windows 无 psutil 时回退 ``wmic process get commandline``
         （部分受限环境被安全策略拒绝，故包在 try 内）；
      4. 最后才回退 ``tasklist /fo csv``（★已知不含命令行，仅"尽力而为"，
         其结果不足以判定"未运行"）。
    探测失败一律返回 False（沿用原语义：不保守判定为运行中，
    避免沙箱/CI 环境误跳过全部生产数据测试）。
    """
    _cl = _iter_main_py_cmdlines()
    if _cl is not None:
        return bool(_cl)
    try:
        import subprocess
        if os.name != "nt":
            _raw = subprocess.run(
                ["pgrep", "-af", "main.py"], capture_output=True, timeout=5,
            ).stdout
            return "main.py" in (_raw.decode("utf-8", errors="replace") or "")
        try:
            _raw = subprocess.run(
                ["wmic", "process", "get", "commandline"],
                capture_output=True, timeout=8,
            ).stdout
            _txt = (_raw.decode("utf-8", errors="replace") or "").replace('"', "")
            if "main.py" in _txt:
                return True
        except Exception:
            pass
        _raw = subprocess.run(
            ["tasklist", "/fo", "csv"], capture_output=True, timeout=5,
        ).stdout
        _txt = (_raw.decode("utf-8", errors="replace") or "").replace('"', "")
        return "main.py" in _txt
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """★第57批 T1：框架运行时，自动跳过标记 production_data 的测试。"""
    if not is_framework_running():
        return
    _reason = "框架运行中，跳过生产数据读取测试"
    for _item in items:
        if "production_data" in _item.keywords:
            _item.add_marker(pytest.mark.skip(reason=_reason))


def _framework_running_skip_if_needed():
    """★第57批 T1：框架运行时抛出 pytest.skip（统一守卫核心逻辑，可单测）。"""
    if is_framework_running():
        pytest.skip("框架运行中，跳过生产数据读取测试")


@pytest.fixture
def guard_production_data():
    """内联守卫：框架运行时跳过当前测试（供未使用标记的生产数据读取测试调用）。"""
    _framework_running_skip_if_needed()
    yield


# ============================================================ ★366刀N4：测试 data/ 沙箱根＋哨兵守卫
#: 停机态全量回归会改写生产 data/（365 T1 实测：`data/evolution/effect_verify.json` 哈希变化）。
#: 本段在**测试侧**兜住：跑测前备份哨兵 → 跑测后比对 → 变化则还原并报告。
#: 不在生产代码加分支（任务书红线）；`PULSE_DATA_GUARD_STRICT=1` 时变化直接 fail。
_M366_SENTINELS = (
    os.path.join("data", "patches", "pending_patches.json"),
    os.path.join("data", "patches", "patch_history.json"),
    os.path.join("data", "evolution", "effect_verify.json"),
)
_M366_SANDBOX_REL = os.path.join("tmp", "pytest_data_sandbox")


def m366_sandbox_dir() -> str:
    """测试沙箱根（`tmp/pytest_data_sandbox`）——**不落 `data/`、不进审计池**。"""
    _d = os.path.join(ROOT, _M366_SANDBOX_REL)
    os.makedirs(_d, exist_ok=True)
    return _d


def m366_file_sha(path: str) -> str:
    """文件 sha256 前 16 位；不存在记 `absent`（不伪造空哈希）。"""
    import hashlib as _h
    _p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.isfile(_p):
        return "absent"
    _x = _h.sha256()
    with open(_p, "rb") as f:
        for _b in iter(lambda: f.read(1 << 20), b""):
            _x.update(_b)
    return _x.hexdigest()[:16]


def m366_snapshot_sentinels() -> dict:
    """三个哨兵文件的哈希快照（键＝相对路径）。"""
    return {p: m366_file_sha(p) for p in _M366_SENTINELS}


def m366_backup_sentinels(backup_dir: str | None = None) -> dict:
    """把哨兵文件备份到沙箱根（返回 {相对路径: 备份绝对路径 或 None}）。"""
    import shutil as _sh
    _bak = backup_dir or m366_sandbox_dir()
    os.makedirs(_bak, exist_ok=True)
    _out = {}
    for _rel in _M366_SENTINELS:
        _src = os.path.join(ROOT, _rel)
        if not os.path.isfile(_src):
            _out[_rel] = None
            continue
        _dst = os.path.join(_bak, _rel.replace(os.sep, "__") + ".bak")
        _sh.copy2(_src, _dst)
        _out[_rel] = _dst
    return _out


def m366_diff_and_restore(before: dict, backups: dict) -> dict:
    """比对哨兵哈希；有变化则从备份还原并报告（无可还原源 ⇒ 如实记 not_restorable）。"""
    import shutil as _sh
    _after = m366_snapshot_sentinels()
    _changed, _restored, _failed = {}, [], []
    for _rel, _h0 in (before or {}).items():
        _h1 = _after.get(_rel)
        if _h1 == _h0:
            continue
        _changed[_rel] = {"before": _h0, "after": _h1}
        _bak = (backups or {}).get(_rel)
        if not _bak or not os.path.isfile(_bak):
            _failed.append(_rel)
            continue
        try:
            _sh.copy2(_bak, os.path.join(ROOT, _rel))
            _restored.append(_rel)
        except OSError as _e:
            _failed.append("%s:%s" % (_rel, type(_e).__name__))
    _now = m366_snapshot_sentinels()
    return {"changed": _changed, "restored": _restored, "failed": _failed,
            "clean_after": all(_now.get(_r) == _h for _r, _h in (before or {}).items())}
# ---------------------------------------------------------------- N4 续：写面重定向
#: ★★ 这张表是**实测**得来的，不是静态 grep 猜的（`logs/full_regression/n4_writemap/`）：
#: 54 件"有 data/ 字面量且有写操作"的候选里，**真写生产 data/ 的只有 4 条**，
#: 其余都是 `should_skip_dir("data/x")` 这类纯字符串断言，或 `tmp_path/"data"/...` 自造结构。
#:
#: 手法：把模块级 REL 常量 patch 成**绝对路径** —— `os.path.join(root, abs)` 会丢弃 root
#: （os.path.join 语义），于是路径单点转向沙箱，**不改生产代码、不改被测测试**。
_M366_WRITE_REDIRECTS = (
    ("nucleus.evolution.criteria_window", "CONVERGE_VOIDED_LEDGER_REL",
     "data/reports/converge_voided.jsonl",
     "作废台账；常量仅用于 converge_voided_ledger_path 拼接，不参与读取决策"),
    ("nucleus.evolution.evolution_budget", "_M335_BUDGET_REL",
     "data/evolution/evolution_budget.json",
     "进化预算侧账；常量仅用于 _m335_budget_path 拼接"),
)

#: 设计内沙箱面：生产侧白名单要求名册路径**必须**在 data/ 下（T-134b），
#: 测试因此用独立子目录 `data/.t_face_roster/`（git-ignored）。
#: 这不是污染 ⇒ 全树探测里**不记为脏**，但仍在报告里可见（记 designed 计数）。
_M366_DESIGNED_SUBDIRS = ("data/.t_face_roster/",)


def m366_redirect_plan(tmp_root: str) -> list:
    """按重定向表算目标路径（纯函数，便于测试）：[(模块, 属性, 目标绝对路径)]。"""
    _out = []
    for _mod, _attr, _rel, _why in _M366_WRITE_REDIRECTS:
        _tail = _rel.split("/")[1:]
        _out.append((_mod, _attr, os.path.join(str(tmp_root), "data", *_tail), _why))
    return _out


@pytest.fixture(autouse=True)
def m366_redirect_data_writes(tmp_path, monkeypatch):
    """把已知台账写入点重定向到 pytest 沙箱（框架运行时不启用、可环境变量关）。

    `PULSE_DATA_REDIRECT=0` 关闭 —— 用于"就是要写生产 data/"的对照实验。
    """
    if is_framework_running() or os.environ.get("PULSE_DATA_REDIRECT", "1") == "0":
        yield {}
        return
    import importlib
    _done: dict = {}
    for _mod, _attr, _tgt, _why in m366_redirect_plan(str(tmp_path)):
        try:
            _m = importlib.import_module(_mod)
        except ImportError as _e:
            print("[N4v3] 重定向跳过（模块不可导入）%s: %s" % (_mod, type(_e).__name__))
            continue
        if not hasattr(_m, _attr):
            print("[N4v3] 重定向跳过（属性已改名，需重核表）%s.%s" % (_mod, _attr))
            continue
        os.makedirs(os.path.dirname(_tgt), exist_ok=True)
        monkeypatch.setattr(_m, _attr, _tgt, raising=False)
        _done["%s.%s" % (_mod, _attr)] = _tgt
    yield _done


# ---------------------------------------------------------------- v2：全树写入探测
#: `data/` 顶层含大量流水账（evolution 4514 / probe 1579 / reports 641…），整体备份不可行
#: （4.9G），但**清单扫描只要 0.87s** ⇒ 用清单 diff 探测写入面，只报告不还原。
#: 例外是三件哨兵：它们有备份，跑完整真还原（v1 能力保留）。
_M366_TREE_REPORT = "last_tree.json"

#: 逐测试归因累加器：{nodeid: {...}}，只记脏例（跨用例累积，报告每例刷新一次）
_M366_DIRTY_CASES: dict = {}


def m366_snapshot_tree() -> dict:
    """`data/` 全树清单：{相对posix路径: [size, mtime_ns]}（只 stat，不读内容）。"""
    _root = os.path.join(ROOT, "data")
    _out: dict = {}
    if not os.path.isdir(_root):
        return _out
    for _dirpath, _dirnames, _filenames in os.walk(_root):
        for _fn in _filenames:
            _p = os.path.join(_dirpath, _fn)
            try:
                _st = os.stat(_p)
            except OSError as _e:
                print("[N4v2] stat 失败（跑测期间被删/占用）%s: %s"
                      % (_p, type(_e).__name__))
                continue
            _out[os.path.relpath(_p, ROOT).replace("\\", "/")] = [
                int(_st.st_size), int(_st.st_mtime_ns)]
    return _out


def m366_diff_tree(before: dict, after: dict, cap: int = 200,
                   ignore: tuple = ()) -> dict:
    """三态差异：新增/删除/修改（各截断到 `cap` 条，另记总数）。

    `ignore`：设计内沙箱面前缀（如 `data/.t_face_roster/`）—— 单独记 `n_designed`，
    **不计入 `dirty`**，但仍出现在明细里（不隐藏，只分类）。
    """
    _b = before or {}
    _a = after or {}
    _ig = tuple(ignore or ())

    def _is_designed(_k: str) -> bool:
        return any(_k.startswith(p) for p in _ig)
    _added = sorted(set(_a) - set(_b))
    _removed = sorted(set(_b) - set(_a))
    _modified = sorted(k for k in (set(_a) & set(_b)) if _a[k] != _b[k])
    _designed = [k for k in (_added + _removed + _modified) if _is_designed(k)]
    _real_add = [k for k in _added if not _is_designed(k)]
    _real_rm = [k for k in _removed if not _is_designed(k)]
    _real_mod = [k for k in _modified if not _is_designed(k)]
    return {
        "n_before": len(_b), "n_after": len(_a),
        "n_added": len(_real_add), "n_removed": len(_real_rm),
        "n_modified": len(_real_mod), "n_designed": len(_designed),
        "added": _real_add[:cap], "removed": _real_rm[:cap],
        "modified": _real_mod[:cap], "designed": _designed[:cap],
        "dirty": bool(_real_add or _real_rm or _real_mod),
    }


def _m366_write_report(name: str, payload: dict) -> None:
    """报告落沙箱根（不进 data/）。写失败只 print，不掩盖被测结果。"""
    import json as _json
    try:
        _p = os.path.join(m366_sandbox_dir(), name)
        with open(_p, "w", encoding="utf-8") as _f:
            _json.dump(payload, _f, ensure_ascii=False, indent=1)
    except OSError as _e:
        print("[N4v2] 报告写盘失败 %s: %s: %s" % (name, type(_e).__name__, _e))


def m366_tree_guard_enabled() -> bool:
    """全树探测默认**开**（单次 0.87s，可忽略）；`PULSE_DATA_TREE_GUARD=0` 可关。"""
    return os.environ.get("PULSE_DATA_TREE_GUARD", "1") != "0"


def m366_per_test_attribution() -> bool:
    """逐测试归因默认**关**（每例 +1.7s）；`PULSE_DATA_TREE_GUARD=1` 时开。"""
    return os.environ.get("PULSE_DATA_TREE_GUARD") == "1"



@pytest.fixture(scope="session", autouse=True)
def m366_data_sentinel_guard():
    """★366刀N4：会话级哨兵守卫（框架运行中不启用——那时由 production_data 标记跳过）。

    停机态：备份 → 跑测 → 比对 → 变化则还原（`PULSE_DATA_GUARD_STRICT=1` 时 fail）。
    报告落 `tmp/pytest_data_sandbox/last_guard.json`，供全量回归取证。
    """
    import json as _json
    if is_framework_running():
        yield {"state": "skipped_framework_running"}
        return
    _before = m366_snapshot_sentinels()
    _backups = m366_backup_sentinels()
    _tree_on = m366_tree_guard_enabled()
    _tree0 = m366_snapshot_tree() if _tree_on else {}
    _rep = {"state": "armed", "before": _before,
            "tree_on": _tree_on, "tree_files": len(_tree0)}
    yield _rep
    _res = m366_diff_and_restore(_before, _backups)
    _rep.update({"state": "checked", "result": _res})
    if _tree_on:
        _td = m366_diff_tree(_tree0, m366_snapshot_tree(),
                             ignore=_M366_DESIGNED_SUBDIRS)
        # 三件哨兵已在上面还原，故从树差异里剔除（否则同一件事报两遍）
        for _k in ("added", "modified", "removed"):
            _td[_k] = [x for x in _td[_k] if x not in _M366_SENTINELS]
        _td["n_added"] = len(_td["added"])
        _td["n_modified"] = len(_td["modified"])
        _td["n_removed"] = len(_td["removed"])
        _td["dirty"] = bool(_td["added"] or _td["modified"] or _td["removed"])
        _rep["tree"] = _td
        if _td["dirty"]:
            print("[N4v2][WARN] 跑测期间 data/ 全树变动：新增=%d 修改=%d 删除=%d"
                  % (_td["n_added"], _td["n_modified"], _td["n_removed"]))
            for _k in ("modified", "added", "removed"):
                for _x in _td[_k][:12]:
                    print("        %-9s %s" % (_k, _x))
        _m366_write_report(_M366_TREE_REPORT, {
            "state": "checked", "scope": "session", "tree": _td})
    try:
        _p = os.path.join(m366_sandbox_dir(), "last_guard.json")
        with open(_p, "w", encoding="utf-8") as f:
            _json.dump(_rep, f, ensure_ascii=False, indent=1)
    except OSError as _e:
        print("[N4] 守卫报告写盘失败：%s: %s" % (type(_e).__name__, _e))
    if _res["changed"]:
        print("[N4][WARN] 生产 data/ 哨兵在跑测期间被改写：%s（已还原=%s，失败=%s）"
              % (sorted(_res["changed"]), _res["restored"], _res["failed"]))
    if os.environ.get("PULSE_DATA_GUARD_STRICT") == "1" and not _res["clean_after"]:
        pytest.fail("[N4] 生产 data/ 哨兵未恢复到跑测前状态：%s" % (_res,), pytrace=False)


@pytest.fixture
def sandbox_data_root(tmp_path):
    """★366刀N4：写类测试的沙箱 data 根（opt-in）。

    用法：`def test_x(sandbox_data_root): p = sandbox_data_root / "patches" / "x.json"`。
    产物落在 pytest 的 tmp_path 下，**不进 `data/`、不进审计池**。
    """
    _root = tmp_path / "data"
    _root.mkdir(parents=True, exist_ok=True)
    return _root


@pytest.fixture(autouse=True)
def m366_per_test_data_attribution(request):
    """逐测试归因（`PULSE_DATA_TREE_GUARD=1` 才启用）：定位**哪个用例**写了 data/。

    每例前后各扫一次全树；只记脏例，报告累积落 `last_tree_per_test.json`。
    默认关（每例 +1.7s）；单文件探测时开，全量回归时关。
    """
    if is_framework_running() or not m366_per_test_attribution():
        yield
        return
    _b = m366_snapshot_tree()
    yield
    _d = m366_diff_tree(_b, m366_snapshot_tree(),
                         ignore=_M366_DESIGNED_SUBDIRS)
    if not _d["dirty"]:
        return
    _item = getattr(request, "nodeid", "?")
    _acc = _M366_DIRTY_CASES
    _acc[_item] = {k: _d[k] for k in ("n_added", "n_modified", "n_removed")}
    _acc[_item].update({"added": _d["added"][:20], "modified": _d["modified"][:20],
                        "removed": _d["removed"][:20]})
    print("[N4v2] 用例写了 data/：%s 新增=%d 修改=%d 删除=%d"
          % (_item, _d["n_added"], _d["n_modified"], _d["n_removed"]))
    _m366_write_report("last_tree_per_test.json",
                       {"state": "collecting", "scope": "per_test",
                        "dirty_cases": _acc})
