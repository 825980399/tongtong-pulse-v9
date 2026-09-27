"""package_full_project —— 完整项目打包工具（供小林下载到本地测试）

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from __future__ import annotations
from nucleus.data.exclude_dirs import PACKAGE_EXCLUDED  # ★第55批 T4（统一排除清单）


import os
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, PROJECT_ROOT)
# ★主线第12批 T2/P2-82：备份目录统一排除（含未来批次），避免备份快照打进交付 zip
from tools.audit_utils import is_backup_name, is_backup_path  # noqa: E402
# ★第145批 T-145c：复用对外发布工具的 fail-closed 白名单（单一真值源）。
#   背景：本工具原以黑名单排除，实测把 内部总账 / docs 分析报告97 / 归档91 /
#   archive21 / 第三方分析 / .pytest_tmp 等大面积内部资产打进了包。
#   改为直接复用 export_public.should_skip（docs 只放行白名单），杜绝漂移。
from tools.export_public import should_skip as _public_should_skip  # noqa: E402



def _m55_unified_excludes() -> bool:
    """★第55批 T4 灰度开关：关掉可回退到第55批前的各自定义清单。"""
    try:
        import config as _m55_cfg

        return bool(getattr(_m55_cfg, "ENABLE_EXCLUDE_DIRS_UNIFIED", True))
    except Exception:
        return True

_M55_LEGACY_EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "node_modules",
                ".pytest_cache", ".mypy_cache", "venv", ".venv"}

#: ★第55批 T4：统一清单（基础项来自 exclude_dirs，新增目录只需改一处）
EXCLUDE_DIRS: frozenset[str] = (
    PACKAGE_EXCLUDED if _m55_unified_excludes() else _M55_LEGACY_EXCLUDE_DIRS
)
EXCLUDE_FILE_EXT = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".log"}
# data/ 与 logs/ 只保留目录骨架，不打内容
SKELETON_ONLY = {"data", "logs"}

# ★第145批 T-145c：交付包专属安全排除（仅本工具生效，不动共享常量）。
#   理由：本工具把**整个项目**打进 zip，必须挡住不该外发的运行资料。
#   - tmp/                 ：临时脚本/诊断产物/导出试验包
#   - docs/路灯与星轨对话/ ：内部协作记录（任务书/交付报告/前置分析）
PACKAGE_LOCAL_EXCLUDE_DIRS: frozenset[str] = frozenset({"tmp", ".workbuddy"})
#: 相对项目根的**精确路径**排除（内部文档目录等）
PACKAGE_LOCAL_EXCLUDE_PATHS: frozenset[str] = frozenset({
    "docs/路灯与星轨对话",
})
#: 敏感文件名（凭证 / 本地覆盖配置），无论位于何处一律排除
PACKAGE_SENSITIVE_FILES: frozenset[str] = frozenset({
    ".env", ".env.local", "config_override.json",
    "credentials.json", "secrets.json",
})


def should_skip(rel_path: str) -> bool:
    _rel = rel_path.replace("\\", "/")
    parts = _rel.split("/")
    # ★第145批 T-145c：先经统一对外白名单（fail-closed，docs 只放行白名单）
    if _public_should_skip(_rel):
        return True
    # ★第145批 T-145c：敏感文件名优先拦截
    if parts[-1] in PACKAGE_SENSITIVE_FILES:
        return True
    # ★第145批 T-145c：交付包专属排除目录
    if any(p in PACKAGE_LOCAL_EXCLUDE_DIRS for p in parts):
        return True
    # ★第145批 T-145c：精确路径排除（内部文档目录）
    if any(_rel == p or _rel.startswith(p + "/") for p in PACKAGE_LOCAL_EXCLUDE_PATHS):
        return True
    # 备份目录/文件（.bak* 及其它历史命名）一律排除
    if any(is_backup_name(p) for p in parts):
        return True
    # 文件名级备份（如 const.py.bak_mainline8，其名字不以 .bak 开头）
    if is_backup_path(parts[-1]):
        return True
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if parts[0] in SKELETON_ONLY and len(parts) > 1:
        return True
    return os.path.splitext(parts[-1])[1].lower() in EXCLUDE_FILE_EXT


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(PROJECT_ROOT), "曈曈_PulseNet_v9_完整代码.zip")

    n_file = 0
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS
                       and d not in PACKAGE_LOCAL_EXCLUDE_DIRS
                       and not is_backup_name(d)]
            # ★第145批 T-145c：精确路径剪枝（内部文档目录）
            _drel = os.path.relpath(root, PROJECT_ROOT).replace("\\", "/")
            dirs[:] = [d for d in dirs
                       if not any((_drel + "/" + d) == p or (_drel + "/" + d).startswith(p + "/")
                                  for p in PACKAGE_LOCAL_EXCLUDE_PATHS)]
            rel_root = os.path.relpath(root, PROJECT_ROOT)  # noqa: F841
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, PROJECT_ROOT)
                if should_skip(rel):
                    continue
                zf.write(full, os.path.join("tongtong-pulse-v9", rel))
                n_file += 1
                total += os.path.getsize(full)

        # data/ 与 logs/ 的目录骨架（空目录占位，保证解压后结构完整）
        for sk in SKELETON_ONLY:
            sk_path = os.path.join(PROJECT_ROOT, sk)
            if not os.path.isdir(sk_path):
                continue
            for sub in sorted(os.listdir(sk_path)):
                sub_full = os.path.join(sk_path, sub)
                if os.path.isdir(sub_full):
                    zf.writestr(
                        f"tongtong-pulse-v9/{sk}/{sub}/.gitkeep",
                        "# 目录占位 —— 真实数据在本机，请勿用压缩包覆盖\n")

    print(f"打包完成: {out}")
    print(f"  文件数: {n_file}")
    print(f"  原始大小: {total/1024/1024:.1f} MB")
    print(f"  压缩包: {os.path.getsize(out)/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
