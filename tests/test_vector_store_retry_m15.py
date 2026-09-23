# -*- coding: utf-8 -*-
"""
test_vector_store_retry_m15.py —— 主线第15批 任务6 门控单测（P2-95 落盘重试）

覆盖：开关默认值、失败原因分类、备用目录定位、可写性预检、
      源码接线护栏（重试循环 / 备用落盘 / 原因入日志）。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.semantic.VectorStore import VectorStore  # noqa: E402

_VS_SRC = os.path.join(_PROJECT_ROOT, "nucleus", "semantic", "VectorStore.py")


def _src():
    with open(_VS_SRC, encoding="utf-8") as f:
        return f.read()


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestRetryConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertIs(getattr(config, "ENABLE_VECTOR_STORE_RETRY", None), True)
        self.assertEqual(getattr(config, "VECTOR_STORE_MAX_RETRIES", None), 3)

    def test_config_read(self):
        self.assertEqual(VectorStore._flush_retry_config(), (True, 3))
        with _Switch(ENABLE_VECTOR_STORE_RETRY=False):
            self.assertEqual(VectorStore._flush_retry_config()[0], False)
        with _Switch(VECTOR_STORE_MAX_RETRIES=-9):
            self.assertEqual(VectorStore._flush_retry_config()[1], 0)


class TestErrorClassification(unittest.TestCase):
    def test_winerror5_is_permission(self):
        r = VectorStore._describe_flush_error(
            PermissionError("[WinError 5] 拒绝访问。: 'a' -> 'b'"))
        self.assertIn("权限拒绝", r)

    def test_file_not_found(self):
        r = VectorStore._describe_flush_error(FileNotFoundError("No such file"))
        self.assertIn("目标路径不存在", r)

    def test_disk_full(self):
        r = VectorStore._describe_flush_error(OSError("No space left on device"))
        self.assertIn("磁盘空间不足", r)

    def test_generic(self):
        r = VectorStore._describe_flush_error(ValueError("weird"))
        self.assertEqual(r, "ValueError")

    def test_none(self):
        self.assertEqual(VectorStore._describe_flush_error(None), "未知")


class TestBackupDir(unittest.TestCase):
    def test_backup_dir_under_data_for_production_path(self):
        """生产路径（data/ 之下）→ 约定目录 data/vector_store_backup/。"""
        vs = VectorStore.__new__(VectorStore)
        vs._vec_path = os.path.join(_PROJECT_ROOT, "data", "knowledge", "vectors.npz")
        d = vs._flush_backup_dir()
        self.assertTrue(d.replace("\\", "/").endswith("/data/vector_store_backup"), d)
        self.assertTrue(os.path.isabs(d))

    def test_backup_dir_sibling_for_isolated_path(self):
        """测试隔离路径（data/ 之外）→ 就地建目录，避免污染生产 data/。"""
        vs = VectorStore.__new__(VectorStore)
        vs._vec_path = os.path.join(_PROJECT_ROOT, "tmp", "test_data", "vectors.npz")
        d = vs._flush_backup_dir()
        self.assertIn("test_data", d.replace("\\", "/"))
        self.assertTrue(d.replace("\\", "/").endswith("/vector_store_backup"), d)
        self.assertNotIn("/data/vector_store_backup", d.replace("\\", "/"))


class TestPrecheck(unittest.TestCase):
    def test_precheck_ok_for_tmp_dir(self):
        vs = VectorStore.__new__(VectorStore)
        p = os.path.join(_PROJECT_ROOT, "tmp", "_vs_precheck_probe.npz")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self.assertTrue(vs._precheck_flush_target(p))

    def test_precheck_false_for_missing_dir(self):
        vs = VectorStore.__new__(VectorStore)
        self.assertFalse(vs._precheck_flush_target("Z:/no/such/dir/x.npz"))


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_retry_loop_present(self):
        self.assertIn("for _attempt in range(1, _attempts + 1):", self.src)
        self.assertIn("落盘重试成功（第", self.src)

    def test_exponential_backoff(self):
        self.assertIn("min(1.0, 0.1 * (2 ** (_attempt - 1)))", self.src)

    def test_backup_fallback_present(self):
        self.assertIn("_flush_to_backup(tmp_vec, tmp_meta)", self.src)
        self.assertIn("已转存备用目录", self.src)

    def test_reason_logged(self):
        self.assertIn("原因={_reason}", self.src)

    def test_precheck_wired(self):
        self.assertIn("self._precheck_flush_target(self._vec_path)", self.src)

    def test_atomic_write_preserved(self):
        """原子写（tmp + os.replace）不得被改动破坏。"""
        self.assertIn("os.replace(tmp_vec, self._vec_path)", self.src)
        self.assertIn("os.replace(tmp_meta, self._meta_path)", self.src)

    def test_switch_off_single_attempt(self):
        self.assertIn("_attempts = (1 + _max_retries) if _retry_on else 1", self.src)


if __name__ == "__main__":
    unittest.main()
