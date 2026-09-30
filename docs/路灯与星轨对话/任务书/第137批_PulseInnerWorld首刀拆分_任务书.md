# 第137批 路灯任务书（大工程：PulseInnerWorld首刀拆分）

> 生成：星轨 · 2026-09-27
> 前置分析：烛微第137批技术债务前置分析
> 上一批：第136批（总账刷新+微信通知器删除+循环依赖+不可达代码检测器）

---

## 任务：PulseInnerWorld首刀拆分（P2，大工程）

### 拆分目标：
把PulseInnerWorld.py里的支撑簇+尾块共2034行拆到独立Mixin文件，主文件从23265行降到21231行。

---

### 施工步骤S0-S6（严格按顺序）

#### S0：前置确认
- 确认W1核心名单已经包含organs/brain/（第135批已做）
- 确认备份目录白名单生效（第133批已做）

#### S1：新建Mixin文件
新建 `organs/brain/pulse_inner_world_support.py`，类名 `PulseInnerWorldSupportMixin`：
1. 先做S1.5：把IW里的守卫函数三连+常量（IW:61-86行，约26行）外迁到 `nucleus/iw_text_guard.py`
2. 然后把窗A（5771-7506行，1736行/31个方法）+ 窗B尾块（22829-23126行，298行/11个方法）全部粘贴到Mixin文件里
3. 最简import头：re, time, typing.Any, nucleus.const(LogLevel等), nucleus.diagnostics.get_diagnostics, iw_text_guard
4. 跑ruff --select F401自动剪冗余import

#### S2：主文件删除+继承改写
1. 先删尾块（22829-23126行，先删后面的，防止行号漂移）
2. 再删大窗（5771-7506行）
3. 主类声明改成：`class PulseInnerWorld(PulseInnerWorldSupportMixin, BasePulseOrgan):`
4. 主类顶部加import：`from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin`

#### S3：编译+import冒烟
1. `python -m compileall` 全项目编译过
2. `ruff --select F821,F811` 0错误
3. import冒烟：`python -c "from organs.brain.PulseInnerWorld import PulseInnerWorld as W; W.rule_reason; W.get_stats"` 三个属性都能解析

#### S4：测试全过
1. 跑test_iw_m31/m33/m34/m35四个测试文件
2. 跑test_qica.py（五派发对测试）
3. 跑pytest全量核心测试

#### S5：门禁检查
1. ruff F=0
2. py_compile全过
3. 静默except计数无回潮
4. god_file检测器跑一遍，基线更新

#### S6：提交
单commit提交，Mixin文件+主文件修改+iw_text_guard小模块，一个commit搞定，回滚直接revert。

---

### 验收标准
1. PulseInnerWorld.py从23265行降到21231行左右
2. Mixin文件存在，42个方法全部搬过去
3. 所有测试全过，功能无变化
4. ruff/py_compile/门禁全过
5. QICA派发功能正常

---

*星轨 · 第137批任务书 · 2026-09-27*
