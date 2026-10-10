# -*- coding: utf-8 -*-
# export-scan-skip-file  (本文件全部为构造的假 PII 夹具，非真实身份)
"""T-133a 日志脱敏层单元测试（6 正 + 6 反 + 作用域/开关/性能）。

验收判据（fc133 七卡）：
  - 6 正：敏感信息脱敏后含对应 <REDACTED*>，漏杀为 0；
  - 6 反：正常文本 / 短 sk- / monkey / tokenize / user@host(无点) / 裸 identity 词
          脱敏后不含 <REDACTED，误伤为 0；
  - 作用域：在 pulse.* 子 logger 的 handler 上挂 SanitizingFilter，子 logger 记录被脱敏
           （机制活体实验结论：logger 级 filter 不吃子 logger，必须 handler 级）；
  - 开关：enabled=False 时 record 原样不变；
  - 性能：7 式对 500 行约 17us/行（CI 用宽松上界防抖动，实测打印）。

注：Windows 路径正则的反斜杠一律用 chr(92) 构造，规避 heredoc / MSYS 路径归一化把
    类似反斜杠的字面量转成 / 的陷阱（与 sanitizer 内部固化写法一致）。
"""
import logging
import time

from nucleus.logging.sanitizer import SanitizingFilter, sanitize

_BS = chr(92)  # 反斜杠，规避 \ 在字面量/heredoc 中被归一化


class _CaptureHandler(logging.Handler):
    """捕获 LogRecord 的测试 handler（不真正写盘）。"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


# ============ 6 正（漏杀为 0） ============
def test_pos_sk_api_key():
    _s = sanitize("调用凭证 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ12 发起请求")
    assert "<REDACTED_KEY>" in _s
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ12" not in _s


def test_pos_api_key_assignment():
    _s = sanitize("api_key=supersecretvalue123 已加载")
    assert "api_key=<REDACTED>" in _s
    assert "supersecretvalue123" not in _s


def test_pos_bearer_token():
    # 独立 Bearer（不带 authorization: 前缀，避免被 api_key 式部分吞掉）
    _s = sanitize("使用 Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xyz 访问接口")
    assert "Bearer <REDACTED>" in _s
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xyz" not in _s


def test_pos_phone():
    _s = sanitize("联系电话 13812345678 请回拨")
    assert "<REDACTED_PHONE>" in _s
    assert "13812345678" not in _s


def test_pos_email():
    _s = sanitize("联系 admin@example.com 处理工单")
    assert "<REDACTED_EMAIL>" in _s
    assert "admin@example.com" not in _s


def test_pos_face_roster_path():
    _s = sanitize("加载 C:" + _BS + "data" + _BS + "face_roster.json 失败")
    assert "<REDACTED_FACE_PATH>" in _s
    assert "face_roster.json" not in _s


# 额外：identity 目录路径（第七卡，独立于 6 正做覆盖）
def test_pos_identity_path():
    # identity 正则只屏蔽到 identity\ 目录（含尾部反斜杠），文件名可保留
    _s = sanitize("读取 D:" + _BS + "users" + _BS + "identity" + _BS + "profile.json")
    assert "<REDACTED_ID_PATH>" in _s
    assert "identity" not in _s
    assert "users" not in _s


# ============ 6 反（误伤为 0） ============
def test_neg_normal_text():
    _s = sanitize("系统启动完成，心跳正常，队列深度 12")
    assert "<REDACTED" not in _s


def test_neg_short_sk_like():
    # sk- 后不足 16 位，不应命中
    _s = sanitize("变量 sk-abc 已定义")
    assert "<REDACTED" not in _s


def test_neg_monkey_word():
    # 含 key 但不是 api_key / token / secret / authorization
    _s = sanitize("monkey 在树上玩耍")
    assert "<REDACTED" not in _s


def test_neg_tokenize_word():
    # tokenize 含 token 但后面不是 =/:，不命中 assignment 式
    _s = sanitize("调用 tokenizer.tokenize 分词")
    assert "<REDACTED" not in _s


def test_neg_user_at_host():
    # 无点的 @ 不是邮箱
    _s = sanitize("连接 user@host 本地服务")
    assert "<REDACTED" not in _s


def test_neg_bare_identity_word():
    # identity 词但无 Windows 路径前缀，不命中 identity 路径式
    _s = sanitize("执行 identity verification 校验")
    assert "<REDACTED" not in _s


# ============ 作用域：handler 级覆盖子 logger ============
def test_scope_child_logger_handler():
    _lg = logging.getLogger("pulse.test.sanitize_scope")
    _lg.handlers.clear()
    _lg.propagate = False
    _h = _CaptureHandler()
    _h.addFilter(SanitizingFilter(enabled=True))
    _lg.addHandler(_h)
    try:
        _lg.warning("异常手机号 13800138000 已记录")
        assert len(_h.records) == 1
        assert "<REDACTED_PHONE>" in _h.records[0].msg
        assert "13800138000" not in _h.records[0].msg
    finally:
        _lg.handlers.clear()


def test_scope_args_redacted():
    _lg = logging.getLogger("pulse.test.sanitize_args")
    _lg.handlers.clear()
    _lg.propagate = False
    _h = _CaptureHandler()
    _h.addFilter(SanitizingFilter(enabled=True))
    _lg.addHandler(_h)
    try:
        _lg.info("用户 %s 登录失败", "admin@example.com")
        assert len(_h.records) == 1
        # args 经 filter 改写，email 在 args[0] 中
        assert "<REDACTED_EMAIL>" in _h.records[0].args[0]
        assert "admin@example.com" not in _h.records[0].args[0]
        # 格式化后同样脱敏
        assert "<REDACTED_EMAIL>" in _h.format(_h.records[0])
    finally:
        _lg.handlers.clear()


# ============ 开关：enabled=False 不脱敏 ============
def test_disabled_filter_noop():
    _lg = logging.getLogger("pulse.test.sanitize_disabled")
    _lg.handlers.clear()
    _lg.propagate = False
    _h = _CaptureHandler()
    _h.addFilter(SanitizingFilter(enabled=False))
    _lg.addHandler(_h)
    try:
        _lg.warning("手机号 13812345678 原样保留")
        assert len(_h.records) == 1
        assert "13812345678" in _h.records[0].msg
        assert "<REDACTED" not in _h.records[0].msg
    finally:
        _lg.handlers.clear()


# ============ 性能基准 ============
def test_perf_500_lines():
    _samples = [
        "系统心跳正常 key=val",
        "加载 C:" + _BS + "data" + _BS + "face_roster.json",
        "用户 admin@example.com 登录",
        "手机号 13812345678 已登记",
        "token=abcdefghijklmnopqrstuvwxyz",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc",
    ] * 100  # 600 行，取前 500
    _lines = _samples[:500]
    _t0 = time.perf_counter()
    for _ln in _lines:
        sanitize(_ln)
    _dt = time.perf_counter() - _t0
    _per = (_dt / 500) * 1e6
    print("sanitize perf: {:.2f} us/line (target 17)".format(_per))
    # 宽松上界防 CI 抖动（设计目标 17us/行）
    assert _per < 500.0
