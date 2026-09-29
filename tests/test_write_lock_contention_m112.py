# -*- coding: utf-8 -*-
"""T-112a 写锁优化并发压力自检（D186-A/D + 假死探测器可观测化配套验收）。

覆盖：
  1. 按路径分锁：同规范路径返回同一锁、不同路径返回不同锁（消除「写A阻塞写B」全局互斥 convoy）。
  2. 并发写不丢不坏：8 文件 × 4 线程高频写，全部 safe_write_json 返回 True，最终文件可解析且内容自洽。
  3. 退避重试：模拟 Windows 读者持句柄导致的 WinError 32（errno=32）瞬时失败，_replace_with_retry 重试后成功。
  4. 备份 I/O 移出锁外不致死锁：单路径并发 backup=True 写全部成功，且产生 .bak。
  5. 唯一 tmp + 失败清理：写成功后无残留 .tmp 文件。

不依赖生产框架运行（纯单元/并发自测），不触碰生产 data/。
"""
import os
import json
import threading


from nucleus.data.DataAccessLayer import (
    safe_write_json,
    safe_write_text,
    _get_path_lock,
    _replace_with_retry,
)


def test_path_lock_isolation():
    """D186-A：同规范路径→同一锁；不同路径→不同锁（无全局唯一写锁）。"""
    a1 = _get_path_lock(os.path.join("x", "a.json"))
    a2 = _get_path_lock(os.path.join("x", "a.json"))
    b = _get_path_lock(os.path.join("x", "b.json"))
    # 同一路径（含大小写规范化）必须返回同一对象
    assert a1 is a2
    # 不同路径必须返回不同对象（否则仍是全局互斥）
    assert a1 is not b


def test_concurrent_writes_no_loss(tmp_path):
    """8 文件 × 4 线程并发写，无丢失、无损坏、全部返回 True。"""
    files = [tmp_path / f"f{i}.json" for i in range(8)]
    n_threads = 4
    iters = 30
    errors = []
    results = []

    def worker(fid):
        f = files[fid]
        for k in range(iters):
            payload = {"fid": fid, "k": k, "marker": f"t{k}"}
            try:
                ok = safe_write_json(str(f), payload, backup=False)
                results.append(ok)
            except Exception as e:  # noqa: BLE001
                errors.append(f"fid={fid} k={k} {type(e).__name__}: {e}")

    threads = []
    # 每个文件由 n_threads 个线程并发写（8 文件 × 4 线程的真实竞争）
    for fid in range(len(files)):
        for _ in range(n_threads):
            t = threading.Thread(target=worker, args=(fid,))
            threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写出现异常: {errors[:5]}"
    assert all(results), f"存在 safe_write_json 返回 False: {results.count(False)}/{len(results)}"

    # 最终每个文件可解析，且内容是合法最后写入
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "fid" in data and "k" in data

    # 无残留 .tmp（唯一 tmp + 失败清理）
    stray = list(tmp_path.glob("*.tmp"))
    assert not stray, f"存在残留临时文件: {[str(s) for s in stray]}"


def test_replace_with_retry_transient_winerror(monkeypatch):
    """D186-D：读者持句柄导致 os.replace 瞬时 WinError 32，重试后成功。"""
    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            # 模拟 Windows 读者持句柄：PermissionError / WinError 32
            raise OSError(32, "The process cannot access the file because it is being used by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    import tempfile
    d = tempfile.mkdtemp()
    src = os.path.join(d, "s.tmp")
    dst = os.path.join(d, "t.json")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("{}")
    # 不应抛异常
    _replace_with_retry(src, dst)
    assert os.path.exists(dst)
    assert calls["n"] == 3  # 前两次失败 + 第三次成功


def test_backup_outside_lock_no_deadlock(tmp_path):
    """单路径并发 backup=True 写：全部成功且产生 .bak（备份 I/O 在锁外不致死锁）。"""
    target = tmp_path / "single.json"
    n_threads = 4
    iters = 20
    results = []

    def worker():
        for k in range(iters):
            ok = safe_write_json(str(target), {"k": k}, backup=True)
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results), f"backup=True 并发写存在失败: {results.count(False)}/{len(results)}"
    assert target.exists()
    # _keep_backup 应保留最新备份
    assert os.path.exists(str(target) + ".bak")


def test_safe_write_text_roundtrip(tmp_path):
    """safe_write_text 同样走硬化写通道（路径锁 + 唯一 tmp + 退避重试）。"""
    target = tmp_path / "note.txt"
    ok = safe_write_text(str(target), "hello\n", backup=False)
    assert ok
    assert target.read_text(encoding="utf-8") == "hello\n"
    stray = list(tmp_path.glob("*.tmp"))
    assert not stray
