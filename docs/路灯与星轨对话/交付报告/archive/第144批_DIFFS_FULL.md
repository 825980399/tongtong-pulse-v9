# 第144批 DIFFS FULL

> 生成：路灯 · 2026-09-27 · 提交 `d6f6cfb`（13 文件，+102/−1737）
> ⚠️ 本文件对出现过的敏感信息做掩码：真实路径 → `<PROJECT_ROOT>` / `<SAFEGUARD_ROOT>`；真名 → `<自述名>`。

## numstat
```
12      0   .gitignore
0    1662   .rebuilt_131/pending_patches.json      (git rm --cached)
0      14   _install_cython.bat                    (git rm --cached)
2       2   config.py
7       7   docs/完整进化路线与技术债务清单_v1.0.md
0      21   docs/路灯与星轨对话/交付报告/第122批_pip_install.log   (git rm --cached)
0      16   docs/路灯与星轨对话/交付报告/第122批_离线冒烟.log      (git rm --cached)
7       5   nucleus/data/DataAccessLayer.py
2       2   nucleus/reasoning/PatchManager.py
28      3   nucleus/self_awareness/quality_score_v2.py
1       1   organs/brain/PulseCodeLearner.py
23      3   organs/senses/visual_engines/ocr_engine.py
20      1   tools/export_public.py
```

## 关键改动摘要

### .gitignore（+12）
新增第 16 节：`.rebuilt_*/`、`docs/**/*.log`、`_install_cython.bat`、`*.pyx.bak`。

### config.py（+2/−2）
```diff
-        r"<PROJECT_ROOT>\workspace",
+        os.path.join(_PROJECT_ROOT, "workspace"),
```
```diff
-        r"<HOME>\.ssh",   # 通用化：不硬编码真实用户名
+        r"**\.ssh",       # 通用化：不硬编码真实用户名/家目录（跨平台匹配任意层级 .ssh）
```

### nucleus/self_awareness/quality_score_v2.py（+28/−3）
新增 `_find_ruff()`（动态查找：`shutil.which` → 解释器同目录 `Scripts/ruff.exe`、`Scripts/ruff`、`bin/ruff`、`ruff.exe`、`ruff`）；
`count_ruff_f()` 改用 `_find_ruff()`，去除写死 `D:/Program Files/Python312/Scripts/ruff.exe` 兜底。

### nucleus/data/DataAccessLayer.py（+7/−5）
`safe_read_json` 编码链前置 `utf-8-sig`（透明剥离 UTF-8 BOM；无 BOM 行为不变）。

### organs/senses/visual_engines/ocr_engine.py（+23/−3）
Tesseract 路径去写死：环境变量 `TESSERACT_CMD` → `shutil.which("tesseract")` → 常见安装位置列表（ProgramFiles / ProgramFiles(x86) / LOCALAPPDATA / /usr/bin / /usr/local/bin）。

### tools/export_public.py（+20/−1）
新增 `EXCLUDE_PATH_GLOBS = (".rebuilt_*/", ".rebuilt_*")` + `fnmatch` 导入；`should_skip` 增加通配匹配；`EXCLUDE_EXACT_NAMES` 增加 `_install_cython.bat`。

### docs/完整进化路线与技术债务清单_v1.0.md（+7/−7）
PII 掩码：`<PROJECT_ROOT>\logs\pulse.log(.1)` → `<PROJECT_ROOT>\logs\pulse.log(.1)`；
`<SAFEGUARD_ROOT>`（5 处）→ `<SAFEGUARD_ROOT>`；`我叫任宥曈` → `我叫<自述名>`。

### 注释/docstring 掩码收口
- `nucleus/reasoning/PatchManager.py`（+2/−2）：`<PROJECT_ROOT>` → `<项目根>`
- `organs/brain/PulseCodeLearner.py`（+1/−1）：`<PROJECT_ROOT>` → `<项目根>`

## 出库文件（git rm --cached，本地保留）
- `.rebuilt_131/pending_patches.json`（-1662）
- `_install_cython.bat`（-14）
- `docs/路灯与星轨对话/交付报告/第122批_pip_install.log`（-21）
- `docs/路灯与星轨对话/交付报告/第122批_离线冒烟.log`（-16）

---

*路灯 · 第144批 · 2026-09-27*
