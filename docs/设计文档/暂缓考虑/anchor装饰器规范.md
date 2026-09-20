# anchor（插入锚点）与装饰器规范

**适用对象**：所有以「文本锚点 + 字符串替换」方式修改源码的**补丁脚本**（`tmp/patch_*.py`）与人工编辑。
**版本**：v1.0（2026-09-11，主线第21批 T6.2）
**来源**：`docs/完整进化路线与技术债务清单_v1.0.md` §24.2「补丁脚本 anchor 装饰器坑」
**性质**：施工规范（非代码约束），用于避免一类**静默失效**的补丁事故。

---

## 一、术语澄清（重要）

本文的 **anchor** 指：补丁脚本中用于**定位插入位置**的**文本锚点**（一段原文片段）。

> ⚠️ **它不是一个名为 "anchor" 的 Python 装饰器。** 本项目代码中不存在 `@anchor` 装饰器；
> 技术债务清单里的「anchor 装饰器规范」是**简写**，完整含义是
> **「补丁脚本的插入锚点（anchor）与装饰器（decorator）之间的位置关系规范」**。
>
> （背景：主线第16批曾因此踩坑，见第四章错误示例 1。）

本文中的**装饰器**指 Python 标准装饰器，本项目实测使用频次：

| 装饰器 | 次数 | 典型场景 |
|---|---|---|
| `@staticmethod` | 144 | 工具类方法、无 `self` 依赖的助手 |
| `@property` | 34 | 只读属性封装 |
| `@classmethod` | 20 | 类级工厂 / `from_dict` |
| `@abstractmethod` | 5 | 抽象基类接口 |
| `@budget_guard(...)` | 4 | 资源预算闸门（本项目自研） |
| `@functools.wraps` / `@contextmanager` | 2 / 1 | 装饰器实现 / 上下文管理器 |

---

## 二、核心规则

> **R1**：以 `def` 行作插入锚点时，**必须确认它的上一行是否为装饰器**。
> **R2**：若上一行是装饰器，**锚点必须连同装饰器行一起包含**（或改用其他锚点）。
> **R3**：插入后必须做**结构性复核**（见第五章检查清单），不能只看"替换成功"。

**为什么**：字符串替换不懂语法结构。若锚点是 `    def foo(...)`，而它前一行是 `@staticmethod`，
那么"在锚点**之前**插入新方法"会得到：

```
    @staticmethod          ← 装饰器留在了原位
    <新插入的方法>          ← 装饰器被"粘"到了新方法上！
    def foo(...)           ← 目标方法丢失装饰器
```

即：**新方法意外继承了装饰器，原方法静默失去装饰器**。若不跑测试，很难发现。

---

## 三、正确用法（3 个示例）

### 示例 1：锚点包含装饰器整块（推荐）

```python
# —— 补丁脚本片段 ——
OLD = '''    @staticmethod
    def _compute_checksum(nodes) -> str:
'''
NEW = '''    @staticmethod
    def _new_helper(a) -> int:
        return len(a)

    @staticmethod
    def _compute_checksum(nodes) -> str:
'''
assert src.count(OLD) == 1          # ① 锚点唯一性
src = src.replace(OLD, NEW)
```

✅ 装饰器随目标方法一起被"搬"到新位置之后，两者配对关系不变。

### 示例 2：锚点取「无装饰器」的稳定行

```python
OLD = '''class SelfAwarenessEngine:
    """自我认知引擎（可多实例；生产建议用 get_self_awareness_engine() 单例）。"""
'''
NEW = '''class SelfAwarenessEngine:
    """自我认知引擎（可多实例；生产建议用 get_self_awareness_engine() 单例）。"""

    # ★ 新增：本批添加的公共方法
    def integrate_call_graph(self, project_root=None) -> dict:
        ...
'''
```

✅ 类定义行 + docstring 没有装饰器，是最稳的插入锚点之一。

### 示例 3：用「函数末尾 + 下一个 def」双端锚点

```python
OLD = '''        return _out

    def next_method(self):
'''
NEW = '''        return _out

    def inserted_method(self):
        """新方法。"""
        return {}

    def next_method(self):
'''
assert old_count == 1
```

✅ 双端锚点自带"上下文校验"：若中间结构被改动，`count` 会变成 0，脚本直接失败而非错插。
（注意：若 `next_method` 自身有装饰器，**必须把它也写进锚点**。）

---

## 四、错误用法（2 个示例）

### 错误 1：以 `def` 单行为锚点，忽略上一行的装饰器（**第16批真实事故**）

```python
# ❌ 错误
OLD = "    def _compute_node_list_checksum(nodes: list[PulseNode]) -> str:\n"
NEW = ("    def _snapshot_diagnostic_enabled() -> bool:\n"
       "        ...\n\n"
       + OLD)
```

**实际后果**（第16批实测）：

- 原文件是 `@staticmethod` + `def _compute_node_list_checksum(...)`
- 插入后 `@staticmethod` 被留给了**新插入的方法**
- `_compute_node_list_checksum` 失去 `@staticmethod`
- 症状：`TypeError: _compute_node_list_checksum() takes 1 positional argument but 2 were given`
- 由**既有单测**当场捕获（若当时没有该测试，将静默进入生产）

**修法**：锚点改为 `"    @staticmethod\n    def _compute_node_list_checksum(...)\n"`。

### 错误 2：锚点太短 / 太通用，匹配到多处

```python
# ❌ 错误：`def load(` 在本文件出现多次
OLD = "    def load(self):\n"
src = src.replace(OLD, NEW)     # 没有 count 断言 → 全部被替换
```

**后果**：多个方法的实现被同一段代码覆盖，且**不会有任何报错**。

**正确做法**（本项目铁律）：

```python
c = src.count(OLD)
assert c == 1, "anchor count=%d" % c     # 锚点必须唯一
src = src.replace(OLD, NEW)
```

---

## 五、插入后检查清单（必做）

| # | 检查项 | 命令 / 方法 |
|---|---|---|
| 1 | **语法** | `python -m py_compile <file>` |
| 2 | **静态检查** | `ruff check --select F,E402 <file>` |
| 3 | **模块可导入**（★`py_compile` 抓不到 `NameError`） | `python -c "import importlib; importlib.import_module('<mod>')"` |
| 4 | **装饰器配对** | 在改动区域前后各看 5 行，确认每个 `def` 的装饰器归位 |
| 5 | **行尾符未变** | `open(p,'rb').read().count(b'\r\n')` 与改前一致（本项目多为 CRLF） |
| 6 | **既有测试** | `pytest <相关测试文件>` 全过（尤其覆盖该方法的用例） |

> **本项目铁律补充**：`py_compile` 与 `ruff` **都不执行模块级代码**，抓不到 `NameError` /
> 装饰器错位导致的 `TypeError`。**第 3 项「模块导入冒烟」不可省。**

---

## 六、迁移指南（把现有函数改为 anchor 安全形式）

1. **定位**：找出补丁脚本中所有 `def ` 开头的单行锚点：
   ```bash
   grep -n '^OLD = .*def \|^    OLD = .*def ' tmp/patch_*.py
   ```
2. **上溯一行**：对每个命中，打开目标文件确认其上一行是否以 `@` 开头。
3. **改造锚点**：若是装饰器，把装饰器行并入锚点（含正确缩进）。
4. **加唯一性断言**：确保 `assert src.count(OLD) == 1`。
5. **复跑六项检查**（第五章）。
6. **保留审计痕迹**：补丁脚本不删除（本项目 `tmp/patch_*.py` 共 139 个，作为施工审计链留存）。

### 附：为何补丁脚本要用「文本锚点」而非 AST 改写

本项目历史批次中，文本锚点方案在**巨型文件**（`main.py` 3600+ 行、`PulseController.py` 数千行）
上表现更可控：改动可见、diff 精确、失败可立即发现。
代价就是本文所述的**锚点选择纪律** —— 规则 R1/R2/R3 即为此而生。

---

**文档结束**（v1.0 / 2026-09-11 / 主线第21批）
