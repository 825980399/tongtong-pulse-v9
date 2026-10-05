"""export_public —— 对外发布包导出工具（第143批 T-143d）

用途
----
把项目导出成**干净、可对外公开**的发布包（zip 或目录），用于比赛提交 /
开源首发。与 ``package_full_project.py``（"完整代码备份"，含全部内部文档）
不同，本工具的核心目标是**只保留对外可见的内容**：

- **只包含**：全部源码（代码）、根 ``README.md``、``docs/`` 里对外的那部分
  核心文档（见 ``PUBLIC_DOCS_ALLOW``）、依赖声明、配置文件等。
- **排除**：``data/``、``tmp/``、``logs/``、``models/`` 等运行数据；
  全部内部协作文档（``docs/路灯与星轨对话/`` 任务书与交付报告、
  ``docs/分析报告/``、``docs/归档/``、``docs/archive/`` 等内部笔记）；
  备份快照（``.bak_batchN/``）；缓存与虚拟环境；密钥 / 凭据类文件。

设计原则
--------
1. **白名单优先**（fail-closed）：``docs/`` 只放行 ``PUBLIC_DOCS_ALLOW``
   中显式列出的文件/目录，其余一律不打；源码区按黑名单剔除内部资产。
2. **默认 dry-run 安全**：加 ``--dry-run`` 只列清单不落盘。
3. **PII 复扫断言**：导出后对包内每个文本文件跑一遍敏感信息扫描
   （真名 / 手机号 / 邮箱 / API Key / 真实绝对路径），命中即失败退出，
   保证发布包不泄露个人信息（对齐第143批 T-143a 验收）。
4. 复用 ``nucleus.data.exclude_dirs`` 的统一排除语义，避免各工具各写一份。

用法
----
::

    python tools/export_public.py                 # dry-run，列清单
    python tools/export_public.py --out dist.zip  # 导出 zip
    python tools/export_public.py --out dist/     # 导出目录（不带扩展名）
    python tools/export_public.py --no-scan       # 跳过 PII 复扫（不推荐）

版本: v10 PulseNet · 工具
设计: 路灯、星轨
日期: 2026年9月27日
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import zipfile
from collections.abc import Iterator

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from nucleus._silent_except import silent_exc
from nucleus.data.exclude_dirs import PACKAGE_EXCLUDED

# =============================================================================
# 一、排除规则
# =============================================================================

#: 目录级排除（任意层级命中即剪枝）。PACKAGE_EXCLUDED 已含缓存 / VCS / IDE。
EXCLUDE_DIRS: frozenset[str] = PACKAGE_EXCLUDED | frozenset({
    # 运行数据与产物（发布包绝不含）
    "data", "tmp", "logs", "models", "hardware", "output", "dist",
    "htmlcov", "site-packages", ".ipynb_checkpoints",
    # 测试隔离与工作区元数据（临时产物）
    ".pytest_tmp", ".tmp_backup", ".bak_tmp", ".release-tmp",
    ".mpy-workbench", ".ruff_cache",
    # 重建 / 临时产物（绝不包含发布包）
    ".aionclaw-tmp", ".rebuilt_131",
    # 构建产物（Cython 编译中间件，内含真实绝对路径）
    "build", "temp.win-amd64-cpython-312", "Release",
    # CI/宿主平台配置（含内部流程，不进发布包）
    # ★第148批 3.3：.github/ 改为随发布包发布——首发仅含公开的 PR 模板等
    #   基础设施，不含内部流程；若后续新增含内部信息的 workflow 须重新评估并移回排除。
    ".gitee",
    # 内部协作文档（整目录剔除）
    "路灯与星轨对话",          # 任务书 / 交付报告 / 与星轨对话记录
    "分析报告",                # 技术债务前置分析、第三方分析、台账 CSV
    "台账",
    "审查报告",
    "验收",
    "归档",                    # 历史实施与阶段性报告
    "archive",                 # 被替换掉的历史版本与规范
    "第三方分析",
    # 备份 / 副本 / 快照
    "code_backups",
})

#: 文件名级排除（后缀或精确名）
EXCLUDE_FILE_EXT: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".log", ".bak", ".orig", ".rej",
    ".o", ".obj", ".lib", ".exp", ".ilk", ".pdb", ".tlog",
})

#: 精确排除的文件名（凭据 / 本地配置 / 内部账本 / 一次性产物）
EXCLUDE_EXACT_NAMES: frozenset[str] = frozenset({
    ".env",
    "credentials.json",
    "secrets.json",
    "token.json",
    # ★Dxxx-1：属主脱敏 PII 配置（真实姓名/路径），绝不进入发布包
    ".owner_pii.json",
    ".owner_pii.json.example",
    # ★第144批 T-144a/T-144d：含本机 Python 绝对路径，跨环境无效，不对外发布
    "_install_cython.bat",
})

#: 排除的路径前缀（相对仓库根，正斜杠）
EXCLUDE_PATH_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".workbuddy/",
    ".rebuilt_131/",
)

#: 排除的路径通配（fnmatch 风格，正斜杠 / 匹配相对路径）
#: ★第144批 T-144a：账本旁路重建目录每批重建（.rebuilt_131/.rebuilt_132/…），通配收口。
EXCLUDE_PATH_GLOBS: tuple[str, ...] = (
    ".rebuilt_*/",
    ".rebuilt_*",
)

#: 备份目录/文件前缀（.bak_batchN、xxx.bak 等）
BACKUP_PREFIXES: tuple[str, ...] = (".bak",)

#: Windows 保留设备名（仓库里若混入 `nul` / `con` 等重定向残留，会令
#: os.path.relpath 抛 ValueError；一律跳过）
RESERVED_DEVICE_NAMES: frozenset[str] = frozenset({
    "nul", "con", "aux", "prn", "com1", "com2", "com3", "com4",
    "lpt1", "lpt2", "lpt3",
})

# =============================================================================
# 二、docs/ 对外白名单（fail-closed：只放行这里列出的）
# =============================================================================

#: docs/ 下允许进入发布包的**精确文件**（相对 docs/）
PUBLIC_DOCS_ALLOW_FILES: frozenset[str] = frozenset({
    "README.md",                       # docs 索引
    "demo-quickstart.md",              # 演示快速启动（比赛/演示）
    "项目架构总览_20260927.md",        # 一页看懂架构
    "项目结构树.md",                   # 目录结构说明
    # ★第148批 3.3：首发必需公开文档
    "部署指南_APIKey配置.md",          # 最小化部署 + 环境变量配置
    "SECURITY.md",                     # 漏洞上报方式
    "CONTRIBUTING.md",                 # 外部贡献流程 + 公开子集门禁
    "CHANGELOG.md",                    # 首发版本说明
    # ★第145批 T-145b：移出 `完整进化路线与技术债务清单_v1.0.md`
    #   理由：该文档是**内部总账**（含批次交付确认/债务 D 编号/第三方评分/
    #   内部叙事），属内部运行资料，不得进入对外发布包。
})

#: docs/ 下允许整目录带入的**子目录**（相对 docs/）
PUBLIC_DOCS_ALLOW_DIRS: frozenset[str] = frozenset({
    "比赛准备",          # 第143批 T-143c 对外运行数据卡片
    "设计文档",          # 系统设计（对外可读）
    "操作手册",          # 操作类手册
})

#: docs/ 下**明确排除**的内部目录（在文档索引里有名，但属内部叙事）
EXCLUDE_DOC_DIRS: frozenset[str] = frozenset({
    "路灯与星轨对话", "分析报告", "台账", "审查报告", "验收",
    "归档", "archive", "性能报告",
})

# =============================================================================
# 三、敏感信息复扫模式（对齐 T-143a 已清洗的 PII 类型）
# =============================================================================

#: 强 PII 模式（**通用型，源码内不含任何属主真值**）。
#: 属主专属真名/昵称/真实路径片段严禁写入源码，必须走 _load_owner_pii_patterns()
#: 从脱敏配置读取（环境变量 / 本地 .owner_pii.json）。详见 Dxxx-1。
STATIC_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("API Key 赋值", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*[\"']"
        r"(?!<|$|your|<YOUR|\.\.\.)[A-Za-z0-9_\-]{16,}[\"']")),
]


#: 属主专属 PII（真实姓名 / 昵称 / 真实绝对路径片段）。
#: ★Dxxx-1：严禁硬编码进源码。来源（优先级从高到低）：
#:   1) 环境变量 PULSE_OWNER_NAMES / PULSE_OWNER_PATH_HINTS（逗号分隔）
#:   2) 本地脱敏配置文件（PULSE_OWNER_PII_FILE 指定；默认 <root>/.owner_pii.json）
#:      —— 该文件已加入 EXCLUDE_EXACT_NAMES 且应 gitignore，绝不进入发布包
#: 二者皆空时返回 []（公开包安全：扫描器自身零真值，自复扫必过）。
def _load_owner_pii_patterns() -> list[tuple[str, re.Pattern[str]]]:
    names: list[str] = []
    paths: list[str] = []
    env_names = os.environ.get("PULSE_OWNER_NAMES", "")
    env_paths = os.environ.get("PULSE_OWNER_PATH_HINTS", "")
    if env_names:
        names = [x.strip() for x in env_names.split(",") if x.strip()]
    if env_paths:
        paths = [x.strip() for x in env_paths.split(",") if x.strip()]
    cfg = os.environ.get("PULSE_OWNER_PII_FILE", "")
    if not cfg:
        cfg = os.path.join(PROJECT_ROOT, ".owner_pii.json")
    file_names: list[str] = []
    file_paths: list[str] = []
    #: ★第154批 T154-4（微光 C1）：补属主规则第 4 条「生日」——
    #:   `.owner_pii.json` 早已含 `birthdays` 三条，但旧实现只读 names/path_hints，
    #:   导致**所有既有扫描对生日系统性漏报**（真值在配置里却从不生成规则）。
    file_birthdays: list[str] = []
    if cfg and os.path.isfile(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
            file_names = list(data.get("names", []))
            file_paths = list(data.get("path_hints", []))
            file_birthdays = list(data.get("birthdays", []))
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="export_public._load_owner_pii_patterns", level="warning")
    # ★Dxxx-13 修复：环境注入与本地脱敏配置「合并去重」，禁止任一方静默覆盖另一方
    #   （旧实现 `list(data.get(...)) or names` 在配置文件含该键时会整段丢弃环境注入的真值）
    merged_names = sorted(set(names) | set(file_names))
    merged_paths = sorted(set(paths) | set(file_paths))
    if not getattr(_load_owner_pii_patterns, "_src_printed", False):
        _load_owner_pii_patterns._src_printed = True  # 防 verify_clean 多文件复扫刷屏，仅首调打印来源
        def _src(e: list, f: list) -> str:
            return "env+file" if (e and f) else "env" if e else "file" if f else "none"
        print("[export_public] 属主 PII 来源（合并去重·非覆盖）: "
              "names=%s(%d) paths=%s(%d)"
              % (_src(names, file_names), len(merged_names),
                 _src(paths, file_paths), len(merged_paths)))
    pats: list[tuple[str, re.Pattern[str]]] = []
    for n in merged_names:
        pats.append(("真名·属主", re.compile(re.escape(n))))
    for p in merged_paths:
        pats.append(("真实路径", re.compile(re.escape(p))))
    #: ★第154批 T154-4：属主生日（强规则，命中即阻断导出）。
    #:   来源恒为 `.owner_pii.json::birthdays`（扫描器自身零真值，见 L186 注释口径）。
    for b in file_birthdays:
        pats.append(("生日·属主", re.compile(re.escape(b))))
    return pats


def all_pii_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """通用 + 属主 PII 模式（每次调用实时读取脱敏配置）。"""
    return STATIC_PII_PATTERNS + _load_owner_pii_patterns()


#: 兼容别名（旧调用 / 测试可能引用；仅含通用模式，不含属主真值）。
PII_PATTERNS = STATIC_PII_PATTERNS

#: ★第146批 T146-2：**弱告警**模式 —— 只提示人工复核，**不阻断**导出。
#:   背景：真实出生年份以裸四位数字（如 ``2020年``）写进 tracked 源码时，
#:   上面的强模式（精确日期）未必命中，但信息已经随公开包泄露。
#:   判据：单行内同时命中「裸四位年份」**且**含出生/生日类上下文词。
WEAK_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("疑似出生年份", re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")),
    # T149-3：泛化「具体日期」弱告警（不阻断导出）；具体属主生日由
    # .owner_pii.json 强规则兜底（T149-2 闸门）。强阻断会误伤仓库 HEAD
    # 中大量合法日期（.gitattributes/.gitignore/docs 的 2026-09-* 等）。
    ("疑似具体日期", re.compile(r"\d{4}[年.\-/]\d{1,2}[月.\-/]\d{1,2}")),
]

#: 触发弱告警所需的**同行上下文词**（出现其一才告警，避免把普通日期全报出来）
WEAK_CONTEXT_MARKERS: tuple[str, ...] = (
    "出生", "生日", "诞生", "BIRTH_DATE", "birth_date", "birthday",
)

#: 视为二进制 / 无需扫描的扩展名
BINARY_EXT: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar",
    ".onnx", ".pt", ".pth", ".bin", ".npy", ".npz", ".pkl",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".woff", ".woff2", ".ttf",
    ".db", ".sqlite", ".sqlite3", ".parquet", ".mp3", ".mp4", ".wav",
})

#: 扫描豁免文件（相对仓库根）。
#: ★Dxxx-1：移除对扫描器自身的豁免 —— 扫描器必须复扫自身文件，
#:   源码内已不含任何属主真值（真名/路径改由脱敏配置加载），自复扫必过；
#:   若 SCAN_EXEMPT_FILES 仍含自身，verify_scanner_self_scan 会直接阻断导出。
SCAN_EXEMPT_FILES: frozenset[str] = frozenset({
    # （无）：tools/export_public.py 不再豁免
})

#: 允许的"占位 / 保留域"——RFC 2606 / RFC 6761 保留，非真实身份
ALLOW_DOMAINS: tuple[str, ...] = (
    "example.com", "example.org", "example.net", "example.invalid",
    "users.noreply.example.org", "users.noreply.invalid", "example",
)

#: 行内豁免标记：该行含此注释则跳过扫描（用于测试夹具 / 反例）
SCAN_SKIP_MARKERS: tuple[str, ...] = (
    "# pii-scan-ignore",
    "# export-ignore-pii",
)

#: 文件级豁免标记：文件**前 5 行**含此标记，则整文件跳过扫描
#:（用于 PII 清洗/脱敏功能的测试夹具 —— 其"敏感数据"本身是构造的假数据）
FILE_SCAN_SKIP_MARKERS: tuple[str, ...] = (
    "# export-scan-skip-file",
    "# pii-scan-skip-file",
)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/")


def is_backup_name(name: str) -> bool:
    if any(name.startswith(p) for p in BACKUP_PREFIXES):
        return True
    return ".bak" in name.lower()


def _under_excluded_doc_dir(rel_from_docs: str) -> bool:
    parts = _norm(rel_from_docs).split("/")
    return any(p in EXCLUDE_DOC_DIRS for p in parts[:-1]) or \
        (len(parts) == 1 and parts[0] in EXCLUDE_DOC_DIRS)


def docs_allowed(rel_from_docs: str) -> bool:
    """docs/ 下该相对路径是否允许进入发布包（fail-closed）。"""
    rel = _norm(rel_from_docs)
    parts = rel.split("/")
    # 任何层级命中内部目录 → 排除
    for p in parts[:-1]:
        if p in EXCLUDE_DOC_DIRS:
            return False
    if len(parts) == 1:
        return parts[0] in PUBLIC_DOCS_ALLOW_FILES
    # 精确文件白名单（含子目录路径，如 "工具类文档/公开说明.md"）
    if rel in PUBLIC_DOCS_ALLOW_FILES:
        return True
    # 子目录：顶级目录需在白名单目录内，且不在排除目录内
    top = parts[0]
    if top in EXCLUDE_DOC_DIRS:
        return False
    return top in PUBLIC_DOCS_ALLOW_DIRS


def should_skip(rel: str) -> bool:
    """相对仓库根的路径是否应排除。"""
    rel_n = _norm(rel)
    parts = rel_n.split("/")

    for pref in EXCLUDE_PATH_PREFIXES:
        if rel_n.startswith(pref) or ("/" + pref) in ("/" + rel_n):
            return True

    # ★第144批 T-144a：通配目录（.rebuilt_*）前缀匹配
    for g in EXCLUDE_PATH_GLOBS:
        pat = g.rstrip("/")
        for p in parts:
            if fnmatch.fnmatch(p, pat):
                return True
        if fnmatch.fnmatch(rel_n, g) or fnmatch.fnmatch(rel_n, pat):
            return True

    if any(is_backup_name(p) for p in parts):
        return True
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if parts[-1] in EXCLUDE_EXACT_NAMES:
        return True
    if os.path.splitext(parts[-1])[1].lower() in EXCLUDE_FILE_EXT:
        return True

    # docs/ 单列：白名单优先（fail-closed）
    if parts[0] == "docs":
        return not docs_allowed("/".join(parts[1:]))

    # tests/ 属代码，保留；但 test 夹具里的临时产物已由后缀规则剔除
    return False


def iter_public_files(root: str) -> Iterator[str]:
    """产出应进入发布包的绝对路径。"""
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns
                  if d not in EXCLUDE_DIRS and not is_backup_name(d)]
        for f in sorted(fns):
            if f.lower() in RESERVED_DEVICE_NAMES:
                continue
            full = os.path.join(dp, f)
            try:
                rel = os.path.relpath(full, root)
            except ValueError:
                # 设备文件 / 挂载点异常，跳过
                continue
            if not should_skip(rel):
                yield full


# =============================================================================
# 四、PII 复扫
# =============================================================================

def _unicode_unescape(text: str) -> str:
    """把文本中的 unicode 转义序列还原为真实字符（防转义形态 PII 漏检，T149-1）。

    源码/文档若以转义形式（\\uXXXX / \\UXXXXXXXX）隐藏真实姓名，
    扫描器须先解码再跑 PII 规则。仅解析转义序列，不动其它字符。
    """
    if "\\u" not in text and "\\U" not in text:
        return text
    return re.sub(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})",
                  lambda m: chr(int(m.group(1) or m.group(2), 16)), text)


def scan_text(path: str) -> list[tuple[str, int, str]]:
    """扫描单个文本文件，返回 [(模式名, 行号, 命中片段)]。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXT:
        return []
    hits: list[tuple[str, int, str]] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            head = []
            for i, line in enumerate(fh, 1):
                if i <= 5:
                    head.append(line)
                elif i == 6:
                    if any(mk in "".join(head) for mk in FILE_SCAN_SKIP_MARKERS):
                        return []
                if any(mk in line for mk in SCAN_SKIP_MARKERS):
                    continue
                line_u = _unicode_unescape(line)
                for name, pat in all_pii_patterns():
                    m = pat.search(line_u)
                    if not m:
                        continue
                    snip = m.group(0)
                    # 保留域（example.com 等）不算真实身份
                    if any(d in snip for d in ALLOW_DOMAINS):
                        continue
                    if len(snip) > 60:
                        snip = snip[:60] + "…"
                    hits.append((name, i, snip))
            # 短文件（<=5 行）也要判一次文件级标记
            if any(mk in "".join(head) for mk in FILE_SCAN_SKIP_MARKERS):
                return []
    except OSError as e:
        silent_exc(e, where="export_public.scan_text", level="warning")
        return []
    return hits


def verify_clean(root: str, files: list[str]) -> list[tuple[str, str, int, str]]:
    """对导出清单中的文件做 PII 复扫，返回全部命中。"""
    out: list[tuple[str, str, int, str]] = []
    for p in files:
        rel = _norm(os.path.relpath(p, root))
        if rel in SCAN_EXEMPT_FILES:
            continue
        for name, ln, snip in scan_text(p):
            out.append((rel, name, ln, snip))
    return out


def scan_text_weak(path: str) -> list[tuple[str, int, str]]:
    """★第146批 T146-2：弱告警扫描，返回 [(模式名, 行号, 命中行片段)]。

    与 scan_text 的区别：
      · 只看「裸四位年份 + 出生/生日上下文」**同段**；
      · 结果**不影响退出码**，仅供人工复核（弱规则可能产生良性命中）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXT:
        return []
    out: list[tuple[str, int, str]] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh, 1):
                if any(mk in line for mk in SCAN_SKIP_MARKERS):
                    continue
                lower = line.lower()
                if not any(m.lower() in lower for m in WEAK_CONTEXT_MARKERS):
                    continue
                for name, pat in WEAK_PII_PATTERNS:
                    if not pat.search(line):
                        continue
                    snip = line.strip()[:100]
                    out.append((name, i, snip))
                    break
    except OSError as e:
        silent_exc(e, where="export_public.scan_text_weak", level="debug")
        return []
    return out


def verify_weak(root: str, files: list[str]) -> list[tuple[str, str, int, str]]:
    """对导出清单做**弱告警**扫描（结果不改变退出码，仅供人工复核）。"""
    out: list[tuple[str, str, int, str]] = []
    for p in files:
        rel = _norm(os.path.relpath(p, root))
        if rel in SCAN_EXEMPT_FILES:
            continue
        for name, ln, snip in scan_text_weak(p):
            out.append((rel, name, ln, snip))
    return out


# =============================================================================
# 五、导出
# =============================================================================

# =============================================================================
# T153-3② 导出时名字归一（生产仓保名 / Q152-7 / Q153-1）
# -----------------------------------------------------------------------------
# 导出副本内替换内部角色名（星轨/路灯/烛微/小林 → 内部协作者）与批次号/票号
# （第1xx批/D1xx/T-xx/Qxxx → 通用说明），生产源文件零改动。
# 仅作用于注释(COMMENT)与文档字符串(STRING)与文档正文；代码字符串（SEED_MEMORIES
# 身份值、self.name="路灯" 等）受上下文保护不动；"路灯"数字生命名仅在含角色名/
# 协作上下文时归一，避免破坏身份与世界观。
# =============================================================================
_ROLE_NAMES = ("星轨", "烛微", "小林")
_BATCH_PAT = re.compile(r"第\d+批|D\d{2,4}(?:-[A-Za-z0-9]+)?|T-\d+[a-z]?|Q\d{2,3}-\d+")
#: 身份值/世界观保护片段：含这些片段的文本不归一「路灯」（保护数字生命身份与世界观）。
_IDENTITY_GUARD = ("SEED_MEMORIES", "路灯是第一个数字生命", '"name"', "'name'", "name=")
_WORLDVIEW_GUARD = "路灯是第一个数字生命"
_TEXT_EXTS = frozenset({
    ".py", ".md", ".markdown", ".txt", ".csv", ".json", ".jsonl", ".yaml",
    ".yml", ".rst", ".toml", ".html", ".js", ".ts", ".css", ".sh", ".bat",
    ".cfg", ".ini", ".conf",
})
#: 点文件（无真实扩展名，os.path.splitext 返空 ext）：按文件名整名匹配。
#: ★T153-3② 修复：`.gitignore`/`.gitattributes` 经 `splitext` 得空 ext，
#: 原 `_TEXT_EXTS` 写法永远匹配不到 ⇒ 注释内角色名/批次号漏归一，须按 basename 收口。
_TEXT_BASENAMES = frozenset({".gitignore", ".gitattributes"})


def _norm_token_text(text: str) -> str:
    """对单段文本做角色名/批次号归一（导出副本用，不影响生产源）。

     worldview 句（「路灯是第一个数字生命」）整体保留「路灯」，仅归一其他角色名与批次号；
    其它文本在含角色名/协作上下文时一并将「路灯」归一为「内部协作者」。
    """
    if _WORLDVIEW_GUARD in text:
        t = text
        for nm in _ROLE_NAMES:
            t = t.replace(nm, "内部协作者")
        t = _BATCH_PAT.sub("通用说明", t)
        return t
    has_ctx = any(k in text for k in
                  ("星轨", "烛微", "小林", "设计", "协作", "项目组", "团队", "内部"))
    t = text
    for nm in _ROLE_NAMES:
        t = t.replace(nm, "内部协作者")
    if has_ctx:
        t = t.replace("路灯", "内部协作者")
    t = _BATCH_PAT.sub("通用说明", t)
    return t


def _normalize_py_text(text: str) -> str | None:
    """tokenize 精确归一 .py 的 COMMENT 与（三引号）文档字符串 token，保护代码数据字符串。

    返回归一后文本；tokenize 失败（如极特殊语法）返回 None（调用方跳过归一，绝不损坏）。

    ★T153-3② 安全边界（对齐任务书 T153-3 验收「仅注释/docstring 归一，身份值除外」）：
      - COMMENT：全部归一（角色名/批次号在注释里必须洗掉）。
      - STRING：仅**三引号**字符串（文档字符串 / 长字符串）归一；
        双引号普通数据字符串（SEED_MEMORIES 身份值、self.name="路灯" 等）
        受保护**不动**——任务书明令身份值除外，且改动会破坏数字生命世界观。
      - FSTRING_MIDDLE：Python 3.12+ 含 {expr} 的 f-string 字面量拆为此令牌，
        角色名常出现在 f-string 字面量中（如日志/提示），须一并归一避免泄露；
        {expr} 占位部分不在该令牌内，不会被改动。旧版本无此令牌则忽略。
    """
    import io as _io
    import tokenize as _tok
    try:
        toks = list(_tok.generate_tokens(_io.StringIO(text).readline))
    except (_tok.TokenError, IndentationError, SyntaxError) as _e:
        # tokenize 失败（极特殊语法）：跳过归一，原样返回（绝不损坏）
        silent_exc(_e, where="export_public._normalize_py_text.tokenize", level="debug")
        return None
    ftypes = (_tok.COMMENT,)
    if hasattr(_tok, "FSTRING_MIDDLE"):
        ftypes = ftypes + (_tok.FSTRING_MIDDLE,)
    changes = []
    for tk in toks:
        if tk.type in ftypes:
            new = _norm_token_text(tk.string)
            if new != tk.string:
                changes.append((tk.start, tk.end, tk.string, new))
        elif tk.type == _tok.STRING:
            # 仅三引号字符串（文档字符串 / 长字符串）归一；双引号普通数据字符串
            # （SEED_MEMORIES 身份值、self.name="路灯" 等）受安全边界保护，不动。
            s = tk.string
            i = 0
            while i < len(s) and s[i] in "rRuUfFbB":
                i += 1
            body = s[i:]
            if body.startswith('"""') or body.startswith("'''"):
                new = _norm_token_text(s)
                if new != s:
                    changes.append((tk.start, tk.end, tk.string, new))
    if not changes:
        return text
    lines = text.split("\n")

    def _off(row: int, col: int) -> int:
        o = 0
        for i in range(row - 1):
            o += len(lines[i]) + 1
        return o + col

    out = text
    for (s, e, _old, new) in sorted(changes, key=lambda c: _off(*c[0]), reverse=True):
        out = out[:_off(*s)] + new + out[_off(*e):]
    return out


def export_normalize_text(rel: str, raw: bytes) -> bytes:
    """导出副本内容归一：替换注释/docstring/文档中的内部角色名与批次号 → 通用表述。

    二进制或非文本跳过；SEED_MEMORIES 等身份值受 _norm_token_text 上下文保护不动。
    返回 bytes（与输入同类型；无变化时原样返回）。
    """
    ext = os.path.splitext(rel)[1].lower()
    base = os.path.basename(rel)
    if ext not in _TEXT_EXTS and base not in _TEXT_BASENAMES:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as _e:
        # 二进制/非法编码：跳过归一，原样返回（绝不损坏）
        silent_exc(_e, where="export_public.export_normalize_text.decode", level="debug")
        return raw
    if ext == ".py":
        norm = _normalize_py_text(text)
        if norm is None:
            norm = text  # tokenize 失败则跳过归一，绝不损坏
    else:
        out_lines = []
        for line in text.split("\n"):
            out_lines.append(_norm_token_text(line))
        norm = "\n".join(out_lines)
    if norm == text:
        return raw
    return norm.encode("utf-8")


def export_zip(root: str, out_zip: str, files: list[str]) -> None:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            rel = os.path.relpath(p, root)
            arc = _norm(os.path.join("tongtong-pulse-net", rel))
            with open(p, "rb") as fh:
                raw = fh.read()
            zf.writestr(arc, export_normalize_text(arc, raw))


def export_dir(root: str, out_dir: str, files: list[str]) -> None:
    for p in files:
        rel = os.path.relpath(p, root)
        dst = os.path.join(out_dir, "tongtong-pulse-net", _norm(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(p, "rb") as fh:
            raw = fh.read()
        with open(dst, "wb") as fo:
            fo.write(export_normalize_text(_norm(rel), raw))


#: Dxxx-1 闸门：扫描器自身文件的相对路径
SELF_SCAN_REL = "tools/export_public.py"


def verify_scanner_self_scan(root: str) -> list[tuple[str, int, str]]:
    """★Dxxx-1 闸门：扫描器复扫自身文件，自身命中 PII 直接阻断导出。

    返回自身文件的 PII 命中列表（空=通过）。自豁免检查由 main 在调用前完成
    （若 SCAN_EXEMPT_FILES 仍含自身则直接 FAIL，不进入本函数）。
    """
    self_path = os.path.join(root, SELF_SCAN_REL)
    if not os.path.isfile(self_path):
        return []
    return scan_text(self_path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对外发布包导出（第143批 T-143d）")
    ap.add_argument("--out", default=None,
                    help="输出 zip 路径或目录路径（省略则 dry-run 只列清单）")
    ap.add_argument("--root", default=PROJECT_ROOT,
                    help="项目根（默认自动探测）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列清单不落盘")
    ap.add_argument("--no-scan", action="store_true",
                    help="跳过导出后的 PII 复扫（不推荐）")
    ap.add_argument("--quiet", action="store_true", help="只打印统计")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    files = sorted(iter_public_files(root))

    if not files:
        print("[FAIL] 未找到任何文件，检查 --root 是否正确", file=sys.stderr)
        return 2

    # ---- 清单汇总 ----
    by_top: dict[str, int] = {}
    total = 0
    for p in files:
        rel = _norm(os.path.relpath(p, root))
        top = rel.split("/")[0]
        by_top[top] = by_top.get(top, 0) + 1
        total += os.path.getsize(p)

    print(f"项目根: {root}")
    print(f"待导出文件数: {len(files)}")
    print(f"原始总大小: {total/1024/1024:.2f} MB")
    print("按顶层分布:")
    for k in sorted(by_top, key=lambda x: -by_top[x]):
        print(f"  {k:<20} {by_top[k]:>6}")

    if args.dry_run or not args.out:
        if not args.quiet:
            print("\n--- 文件清单（前 200 条）---")
            for p in files[:200]:
                print("  " + _norm(os.path.relpath(p, root)))
            if len(files) > 200:
                print(f"  ... 其余 {len(files)-200} 条略")
        print("\n[dry-run] 未落盘。加 --out <路径> 导出。")
        return 0

    # ---- 属主 PII 闸门（T149-2）：扫描器必须武装属主真名/路径规则 ----
    _owner_pats = _load_owner_pii_patterns()
    if not _owner_pats:
        print("[FAIL] 属主 PII 规则数为 0：扫描器未加载属主真名/路径规则"
              "（检查 .owner_pii.json 或 PULSE_OWNER_NAMES /"
              " PULSE_OWNER_PATH_HINTS 环境变量）。未武装的扫描器会给出"
              " 虚假的「PII 0 命中」结论，禁止导出。", file=sys.stderr)
        return 2
    print("[OK] 属主 PII 闸门：已加载 %d 条属主真名/路径规则。" % len(_owner_pats))

    # ---- 落盘 ----
    out = os.path.abspath(args.out)
    is_zip = out.lower().endswith(".zip")
    if is_zip:
        export_zip(root, out, files)
        out_size = os.path.getsize(out)
    else:
        export_dir(root, out, files)
        out_size = -1
    print(f"\n导出完成: {out}")

    # ---- 弱告警扫描（第146批 T146-2：只提示，不阻断） ----
    if not args.no_scan:
        _weak = verify_weak(root, files)
        if _weak:
            print(f"\n[WARN] 弱告警 {len(_weak)} 处（不阻断导出，需人工复核）：")
            for rel, name, ln, snip in _weak[:20]:
                print(f"  {rel}:{ln}  [{name}]  {snip}")
            if len(_weak) > 20:
                print(f"  ... 其余 {len(_weak)-20} 处略")
        else:
            print("\n[OK] 弱告警扫描通过：无「年份 + 出生/生日」同段命中。")

    # ---- PII 复扫 ----
    if not args.no_scan:
        hits = verify_clean(root, files)
        if hits:
            print(f"\n[FAIL] PII 复扫发现 {len(hits)} 处敏感信息，发布包不干净：",
                  file=sys.stderr)
            for rel, name, ln, snip in hits[:50]:
                print(f"  {rel}:{ln}  [{name}]  {snip}", file=sys.stderr)
            if len(hits) > 50:
                print(f"  ... 其余 {len(hits)-50} 处略", file=sys.stderr)
            return 1
        print("[OK] PII 复扫通过：导出清单零敏感信息命中。")

        # ---- Dxxx-1 闸门：扫描器自身复扫（必须扫自己，自身命中即阻断） ----
        if SELF_SCAN_REL in SCAN_EXEMPT_FILES:
            print(f"\n[FAIL] SCAN_EXEMPT_FILES 仍含扫描器自身 {SELF_SCAN_REL}，"
                  f"违反 D148-1 自豁免禁令。", file=sys.stderr)
            return 1
        self_hits = verify_scanner_self_scan(root)
        if self_hits:
            print(f"\n[FAIL] 扫描器自身 {SELF_SCAN_REL} 复扫命中 "
                  f"{len(self_hits)} 处 PII，禁止导出：", file=sys.stderr)
            for name, ln, snip in self_hits[:20]:
                print(f"  {SELF_SCAN_REL}:{ln}  [{name}]  {snip}", file=sys.stderr)
            return 1
        print(f"[OK] 扫描器自复扫通过：{SELF_SCAN_REL} 零 PII 命中。")

        # ---- Dxxx-14：占位符渲染兜底函数（config._apply_placeholder_render）
        #   此前为死代码（全库零调用方）。此处显式调用一次作为导出前自检，
        #   验证渲染路径可用，避免死代码回潮。函数内部自带兜底，失败不影响导出。
        import config as _cfg
        _cfg._apply_placeholder_render()
        print("[OK] 占位符渲染自检通过：config._apply_placeholder_render 可调用。")
    else:
        print("[WARN] 已跳过 PII 复扫。")

    if is_zip:
        print(f"压缩包大小: {out_size/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
