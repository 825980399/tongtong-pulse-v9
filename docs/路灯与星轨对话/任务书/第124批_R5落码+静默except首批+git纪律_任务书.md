# 第124批 任务书：R5落码 + 静默except首批 + git一批一commit

> 派发：星轨　｜　执行：路灯　｜　前置分析：烛微第124批
> 模式：框架已停，改代码后重启生效
> 红线：不改config.py运行开关、不碰data/knowledge/、改前备份

---

## 0. 本批定位

**第123批止血完成，现在进入正常清偿节奏。**
本批三个方向：R5落码（人脸识别持久化）+ 静默except首批7处 + git一批一commit纪律。

---

## 1. T-124a（P0）：R5落码（人脸识别持久化空转件）

### 背景
face_recognition已装（第122批），但R5持久化还没落码。现在落空转件（不装库也能写，装库后直接生效）。

### 施工法（烛微确认：整文件覆盖）
- 生产文件：`organs/brain/PulseVisualCortex.py`（953行）
- 装配件：119行净插入（唯一替换=第49行import）
- **不要手工贴7个hunk，整文件覆盖**（消除手滑风险）

### 改动内容
1. schema：`faces{name→{encoding128,enrolled_at,source,hits,tolerance_override}}`
2. load挂点：:126（册声明后、探测前）
3. save挂点：:373之后:374 return前
4. `_DIRTY_FACE_KEYS`常量三处共用（VC:362/:368/册白名单）
5. 两个隐私方法：`_forget_face` + `_list_roster`（不回显encoding）
6. TONGTONG_FACE_ROSTER环境变量覆盖入口

### 验收（三道离线门）
- G1：py_compile过
- G2：离线import VC不炸
- G3：桩测复跑5/5
- ruff F=0

---

## 2. T-124b（P1）：静默except首批7处改造

### 背景
AST扫描517个静默except，首批必改7处。

### 必改7处（烛微锚点复核：零漂移）
| 位置 | 内容 | 改造 |
|---|---|---|
| main:3700 | 看门狗自吞 | 传level="warning" |
| main:3719 | 看门狗自吞 | 传level="warning" |
| main:3725 | 看门狗自吞 | 传level="warning" |
| logger:506 | 告警节流 | 传level="warning" |
| MetricsCollector:428 | 监控聚合器 | 传level="warning" |
| main:1192 | 器官装配 | 传level="warning" |
| main:1314 | 器官装配 | 传level="warning" |

### helper升级
- `_silent_exc.py`加尾参`level="debug"`
- P0五处传warning级
- 向后兼容（默认debug，不破坏现有调用）

### 验收
- AST计数：517→510（首批减7）
- py_compile过
- m95单测43 passed

---

## 3. T-124c（P2）：git一批一commit纪律（9票）

### 背景
master仍8d9b95f，工作区76条脏文件裸奔了7批。
一次git checkout .全灭的风险每天都在涨。

### 9票提交顺序（每票=一commit）
| 票 | 内容 | message格式 |
|---|---|---|
| ① | 第116批改动（tools/adjudicate_patch.py） | `fix(116): ...` |
| ② | 第117批改动（pulse_tracer/BasePulseOrgan/logger/SafeEvolutionExecutor/tests） | `fix(117): ...` |
| ③ | 第118批改动（Heart/SA/Ears/web_chat/chat_service/main） | `fix(118): ...` |
| ④ | 第119批改动（VC +6-6） | `fix(119): ...` |
| ⑤ | 第120/121批改动（PulseNode +6） | `fix(120,121): ...` |
| ⑥ | 第122批改动（PulseSnapshot +6-0） | `fix(122): ...` |
| ⑦ | 第123批改动（DAL止血+补丁冻结+C2解耦） | `fix(123): ...` |
| ⑧ | docs大票（54个??文件 + CSV对表） | `docs: ...` |
| ⑨ | chore清理（删tmp_openi_error.png + D行） | `chore: ...` |

### 红线
- 每票一个commit，message带批次号
- 提交前确认不包含data/knowledge/
- 提交后推gitee（openi等visibility确认后再推）

---

## 4. 门禁要求

| 门禁 | 标准 |
|---|---|
| ruff F | =0 |
| py_compile | 改动文件全过 |
| m95单测 | 43 passed |
| R5桩测 | 5/5 |
| 行尾保全 | .py CRLF不变 |

---

## 5. 交付要求

1. R5落码DIFF
2. 静默except7处改造DIFF
3. git 9票提交记录
4. 门禁结果

---

*星轨 · 2026-09-25 · 第124批*
