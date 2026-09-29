# -*- coding: utf-8 -*-
"""★往期批次 相关任务 门禁 + 往期批次 相关任务 改造：R5 人脸册 桩测。

直接绑定 Production 的 PulseVisualCortex 真实方法体（_face_roster_path /
_save_face_roster / _list_faces / _forget_face）到一个轻量 self，不实例化整个
框架（摄像头/face_recognition/cv2 均在函数内懒加载）。覆盖：
  A env 覆盖 TONGTONG_FACE_ROSTER（须落在 data/ 内，防生物特征外泄）
  B _save_face_roster 落盘加密（脏键过滤 + encoding128 长度=128）
  C _list_faces 只回元数据（绝不回显 encoding128）+ 按 enrolled_at 升序
  D _forget_face 内存+磁盘四处一致删除 + 回落访客
  E 落盘→读回 逐元素等价（生产实现级往返成立，密文透明）
  F TONGTONG_FACE_ROSTER 越界 data/ 被拒绝（fail-closed）
"""
import os
import sys
import types
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.security.face_codec import load_roster  # noqa: E402
from organs.senses.PulseVisualCortex import PulseVisualCortex  # noqa: E402


def _in_data_path(name):
    """名册路径必须落在 data/ 内（相关任务 白名单），用 .t_face_roster 隔离，git-ignored。"""
    _d = os.path.join(ROOT, "data", ".t_face_roster")
    os.makedirs(_d, exist_ok=True)
    return os.path.join(_d, f"{name}_{uuid.uuid4().hex}.json")


def _mk_self(env_path):
    fake = types.SimpleNamespace()
    fake._known_face_encodings = {}
    fake._roster_meta = {}
    fake._roster_hits = {}
    fake._pending_face_encoding = None
    fake._pending_face_time = 0.0
    fake._current_user_name = "访客"
    fake._FACE_ROSTER_PATH = os.path.join(ROOT, "data", "identity", "face_roster.json")
    fake._log = lambda lvl, msg: None
    # 绑定真实生产方法体
    fake._face_roster_path = PulseVisualCortex._face_roster_path.__get__(fake)
    fake._save_face_roster = PulseVisualCortex._save_face_roster.__get__(fake)
    fake._list_faces = PulseVisualCortex._list_faces.__get__(fake)
    fake._forget_face = PulseVisualCortex._forget_face.__get__(fake)
    return fake


def _bind(env_path):
    os.environ["TONGTONG_FACE_ROSTER"] = env_path
    return _mk_self(env_path)


def test_a_env_override():
    p = _in_data_path("roster_a")
    fake = _bind(p)
    assert fake._face_roster_path() == p, "TONGTONG_FACE_ROSTER 未覆盖册路径"


def test_b_save_persist_and_dirty_filter():
    p = _in_data_path("roster_b")
    fake = _bind(p)
    vec = [float(i) * 1e-3 for i in range(128)]
    fake._known_face_encodings["A正常"] = vec
    fake._roster_meta["A正常"] = {"enrolled_at": 100.0, "source": "auto", "hits": 0, "tolerance_override": None}
    # 脏键不应落盘
    fake._known_face_encodings["小林"] = vec
    fake._roster_meta["小林"] = {"enrolled_at": 200.0, "source": "auto", "hits": 0, "tolerance_override": None}
    fake._save_face_roster()
    doc = load_roster(p)  # 密文透明读回
    faces = doc.get("faces", {})
    assert "A正常" in faces, "正常脸未落盘"
    assert "小林" not in faces, "脏键被错误落盘"
    assert len(faces["A正常"]["encoding128"]) == 128, "encoding128 长度≠128"


def test_c_list_metadata_only():
    p = _in_data_path("roster_c")
    fake = _bind(p)
    vec = [float(i) * 1e-3 for i in range(128)]
    fake._known_face_encodings["B晚"] = vec
    fake._roster_meta["B晚"] = {"enrolled_at": 50.0, "source": "manual", "hits": 3, "tolerance_override": 0.45}
    fake._known_face_encodings["A早"] = vec
    fake._roster_meta["A早"] = {"enrolled_at": 10.0, "source": "auto", "hits": 0, "tolerance_override": None}
    lst = fake._list_faces()
    assert isinstance(lst, list) and len(lst) == 2
    for it in lst:
        assert "encoding128" not in it, "回显了 encoding128（隐私泄漏）"
        assert set(["name", "enrolled_at", "enrolled_at_str", "source", "hits", "tolerance_override"]) <= set(it)
    # 按 enrolled_at 升序
    assert [d["name"] for d in lst] == ["A早", "B晚"]


def test_d_forget_consistent():
    p = _in_data_path("roster_d")
    fake = _bind(p)
    vec = [float(i) * 1e-3 for i in range(128)]
    fake._known_face_encodings["B用户"] = vec
    fake._roster_meta["B用户"] = {"enrolled_at": 1.0, "source": "auto", "hits": 0, "tolerance_override": None}
    fake._roster_hits["B用户"] = 0
    fake._current_user_name = "B用户"
    r = fake._forget_face("B用户")
    assert r["status"] == "forgotten", "forget 状态错误"
    assert "B用户" not in fake._known_face_encodings
    assert "B用户" not in fake._roster_meta
    assert fake._current_user_name == "访客", "forget 后未回落访客"
    doc = load_roster(p)
    assert "B用户" not in doc.get("faces", {}), "磁盘册未删除"


def test_e_roundtrip_equivalence():
    p = _in_data_path("roster_e")
    fake = _bind(p)
    vec = [float(i) * 1e-3 for i in range(128)]
    fake._known_face_encodings["A正常"] = vec
    fake._roster_meta["A正常"] = {"enrolled_at": 1.0, "source": "auto", "hits": 0, "tolerance_override": None}
    fake._save_face_roster()
    doc = load_roster(p)
    got = doc["faces"]["A正常"]["encoding128"]
    assert got == vec, "落盘→读回 编码不等价"


def test_f_env_out_of_data_rejected(tmp_path):
    # 越界 data/ 的 env 重定向必须被拒绝（fail-closed）
    p = str(tmp_path / "escape_roster.json")
    fake = _mk_self(p)
    os.environ["TONGTONG_FACE_ROSTER"] = p
    try:
        try:
            fake._face_roster_path()
            assert False, "越界 env 重定向未被拒绝"
        except ValueError as e:
            assert "越界" in str(e) or "data/" in str(e)
    finally:
        os.environ.pop("TONGTONG_FACE_ROSTER", None)


if __name__ == "__main__":
    for fn in [test_a_env_override, test_b_save_persist_and_dirty_filter,
               test_c_list_metadata_only, test_d_forget_consistent,
               test_e_roundtrip_equivalence, test_f_env_out_of_data_rejected]:
        if fn.__name__ == "test_f_env_out_of_data_rejected":
            import tempfile as _t
            fn(_t.mkdtemp())
        else:
            fn()
    print("R5 桩测 6/6 全部通过")
