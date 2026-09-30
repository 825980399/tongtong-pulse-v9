# 第139批 路灯任务书（紧急bug修复 + PulseInnerWorld第二刀拆分）

> 生成：星轨 · 2026-09-27
> 前置分析：烛微第139批技术债务前置分析
> 上一批：第138批（CC检测器+依赖补全+语义缓存L2）

---

## 任务列表

### T-139a 紧急修复：退出确认input()无限阻塞（P1）
**优先级：P1，紧急！**

**问题**：退出的时候，如果有已批准的补丁，会弹input()问是否应用，但是没人回答的话，框架就卡死在那里，僵尸一样不退出也不运行。

**施工内容：**
1. `main.py:3397`的input()加超时兜底：
   - 30秒无输入，按默认值Y继续
   - 用threading+Event包裹input()，超时自动继续
   - 或者加环境变量`PULSE_QUIT_CONFIRM=0`，非交互模式直接跳过确认
2. 加测试：非交互模式下退出不会卡死

---

### T-139b PulseInnerWorld第二刀拆分（P2，大工程）
**优先级：P2**

**目标**：把知识检索簇2367行拆到第二个Mixin文件，主文件从21148行降到18781行，破2万行大关！

---

### 施工步骤S0-S6
#### S0：备份
先做`.bak_batch139`备份，再动手。

#### S1：新建Mixin文件
新建 `organs/brain/pulse_inner_world_knowledge.py`，类名 `PulseInnerWorldKnowledgeMixin`
- 把现在IW的5841-8207行（2367行/24个方法）全部搬过去
- 注意3个@staticmethod要连装饰器一起搬
- import头照抄第一个Mixin就行

#### S2：主文件删除+继承改写
1. 主文件删掉5841-8207行
2. 主类继承改成三段：`class PulseInnerWorld(PulseInnerWorldSupportMixin, PulseInnerWorldKnowledgeMixin, BasePulseOrgan):`
3. 顶部加import第二个Mixin

#### S3：编译+import冒烟
1. `python -m compileall` 全过
2. `ruff --select F821,F811,F401` 三个文件全过
3. import冒烟：三个方法都能解析

#### S4：测试全过
1. test_qica.py（knowledge_retrieve派发测试重点测）
2. test_iw_m31/m33/m34/m35
3. pytest全量核心测试

#### S5：门禁+基线更新
1. ruff F=0 / py_compile全过
2. god_file基线重新锚定：IW从21148改成18781行

#### S6：单commit提交
一个commit搞定：新Mixin+主文件修改，回滚直接revert。

---

## 验收标准

1. T-139a：30秒无输入自动继续，不会卡死
2. PulseInnerWorld.py从21148行降到18781行左右
3. 24个方法全部搬到第二个Mixin
4. 所有测试全过，功能无变化
5. QICA派发（knowledge_retrieve）正常

---

*星轨 · 第139批任务书 · 2026-09-27*
