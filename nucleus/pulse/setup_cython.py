# -*- coding: utf-8 -*-
"""
setup_cython.py —— Cython配置

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: Cython编译配置与构建脚本
机制: 函数式模块，包含0个工具函数
定位: 性能基础设施层
"""
import os
import sys
from Cython.Build import cythonize
from setuptools import Extension, setup
import glob
import shutil



"""
setup_cython.py —— Cython编译脚本（v23.0增强版）
编译命令: cd nucleus/pulse && python setup_cython.py build_ext --inplace
★v23.0增强：加入-O3最高编译优化 + 兼容任意运行目录 + 边界检查关闭
生成产物: _frequency_codec_cy.cp312-win_amd64.pyd（或对应平台的 .so 文件）
"""


# ★v23.0新增：确保脚本在任意目录下运行时都能正确定位 pyx 文件
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYX_PATH = os.path.join(_CURRENT_DIR, "_frequency_codec_cy.pyx")

# ★v23.0新增：根据编译器选择优化参数
# Windows MinGW 使用 -O3，MSVC 使用 /O2
if sys.platform == "win32" and "--compiler=mingw32" in sys.argv:
    _extra_args = ["-O3"]
elif sys.platform == "win32":
    # /O2 最大化速度 + /GL 全程序优化（配合链接 /LTCG 实现 LTO）
    _extra_args = ["/O2", "/GL"]
else:
    _extra_args = ["-O3"]

# ★v25.1新增：LTO 链接优化（MSVC /LTCG，MinGW/GCC -flto）
if sys.platform == "win32" and "--compiler=mingw32" in sys.argv:
    _lto_link_args = ["-flto"]
elif sys.platform == "win32":
    _lto_link_args = ["/LTCG"]
else:
    _lto_link_args = ["-flto"]

ext_modules = [
    Extension(
        "_frequency_codec_cy",
        sources=[_PYX_PATH],
        extra_compile_args=_extra_args,
        extra_link_args=_lto_link_args,
    ),
    # ★v23.0新增：共振得分计算Cython加速模块
    Extension(
        "_resonance_cy",
        sources=[os.path.join(_CURRENT_DIR, "..", "synapsys", "_resonance_cy.pyx")],
        extra_compile_args=_extra_args,
        extra_link_args=_lto_link_args,
    ),
    # ★v23.0新增：振荡场纯数值计算Cython加速模块
    Extension(
        "_oscillon_cy",
        sources=[os.path.join(_CURRENT_DIR, "..", "field", "_oscillon_cy.pyx")],
        extra_compile_args=_extra_args,
        extra_link_args=_lto_link_args,
    ),
    # ★v25.1新增：批量余弦相似度Cython加速模块（GPU降级路径的CPU热路径）
    Extension(
        "_cosine_cpu_cy",
        sources=[os.path.join(_CURRENT_DIR, "..", "gpu", "_cosine_cpu_cy.pyx")],
        extra_compile_args=_extra_args,
        extra_link_args=_lto_link_args,
    ),
]


# ★四期：跨平台扩展名（Windows → .pyd，Linux/macOS → .so）
_EXT = ".pyd" if sys.platform == "win32" else ".so"

setup(
    ext_modules=cythonize(
        ext_modules,
        language_level=3,
        compiler_directives={
            'boundscheck': False,
            'wraparound': False,
            'cdivision': True,          # ★v25.1：C 语义整数除法（加速）
            'initializedcheck': False,  # ★v25.1：跳过内存视图初始化检查
            'embedsignature': False,    # ★v25.1：不嵌入签名（减小体积/加速）
        },
    ),
)

# ★四期修复：编译后自动移动模块到正确目录（双平台 .pyd/.so）
# --inplace 只把扩展文件放在当前目录（nucleus/pulse/），需移动到各模块所在目录。
# ★修复：用 shutil.move（而非 copy2）移动，移动后当前目录不再残留源文件，
#   避免下次编译产生重复的「复制走了但源文件没删」垃圾（如 pulse/_oscillon_cy.* 残留）。
_synapsys_dir = os.path.join(_CURRENT_DIR, "..", "synapsys")
if os.path.exists(_synapsys_dir):
    _resonance_ext_files = glob.glob(os.path.join(_CURRENT_DIR, f"_resonance_cy*{_EXT}"))
    for _ext_file in _resonance_ext_files:
        _target = os.path.join(_synapsys_dir, os.path.basename(_ext_file))
        shutil.move(_ext_file, _target)
        print(f"[setup_cython] 已移动: {os.path.basename(_ext_file)} → nucleus/synapsys/")

_field_dir = os.path.join(_CURRENT_DIR, "..", "field")
if os.path.exists(_field_dir):
    _oscillon_ext_files = glob.glob(os.path.join(_CURRENT_DIR, f"_oscillon_cy*{_EXT}"))
    for _ext_file in _oscillon_ext_files:
        _target = os.path.join(_field_dir, os.path.basename(_ext_file))
        shutil.move(_ext_file, _target)
        print(f"[setup_cython] 已移动: {os.path.basename(_ext_file)} → nucleus/field/")

_gpu_dir = os.path.join(_CURRENT_DIR, "..", "gpu")
if os.path.exists(_gpu_dir):
    _cosine_ext_files = glob.glob(os.path.join(_CURRENT_DIR, f"_cosine_cpu_cy*{_EXT}"))
    for _ext_file in _cosine_ext_files:
        _target = os.path.join(_gpu_dir, os.path.basename(_ext_file))
        shutil.move(_ext_file, _target)
        print(f"[setup_cython] 已移动: {os.path.basename(_ext_file)} → nucleus/gpu/")