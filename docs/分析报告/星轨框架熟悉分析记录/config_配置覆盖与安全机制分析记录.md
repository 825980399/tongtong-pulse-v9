# 框架熟悉分析记录：config.py 配置覆盖与安全机制

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`config.py`（约3900行，配置中心）

## 一、文件基本信息

- **用途**：框架全局配置中心，定义所有运行时参数、器官配置、安全策略
- **核心机制**：配置覆盖（环境变量/用户配置文件）、热重载、安全黑名单
- **关键变量**：`_COVERABLE_CONFIGS`（27个可覆盖配置块）、`_HOT_RELOAD_BLACKLIST`（7个受保护配置）

## 二、配置覆盖的三条通道

### 2.1 环境变量覆盖（_apply_env_overrides）
- **触发时机**：模块导入时（config.py:3779）和热重载时（config.py:3502）
- **前缀**：`TTP_`（`_ENV_PREFIX`）
- **覆盖逻辑**：遍历所有 `TTP_` 环境变量，去掉前缀后转小写，在 `_COVERABLE_CONFIGS` 中匹配键名
- **类型转换**：根据原值类型自动转换（bool/int/float/str）
- **★P2-376修复前**：不检查 `_HOT_RELOAD_BLACKLIST`，可绕过权限保护
- **★P2-376修复后**：与 `__load_user_config()` 保持一致，黑名单配置块拒绝环境变量覆盖

### 2.2 用户配置文件覆盖（__load_user_config）
- **文件路径**：`data/config_override.json`（`_CONFIG_OVERRIDE_PATH`）
- **触发时机**：热重载时检测文件变更
- **安全机制**：✅ 有 `_HOT_RELOAD_BLACKLIST` 检查（config.py:3398）
- **COW策略**：先深拷贝→在拷贝上合并→原子替换（避免并发修改问题）
- **A-9死参数清理**：`RUNTIME_PARAMS` 合并前做键交集过滤，防止废弃参数回流复活

### 2.3 热重载（_watch_config_changes）
- **机制**：后台线程监控 `config_override.json` 的 mtime 变化
- **通知器官**：热重载完成后通知34个器官刷新参数
- **安全边界**：黑名单配置不允许通过热重载修改

## 三、安全黑名单机制（_HOT_RELOAD_BLACKLIST）

### 3.1 黑名单内容（7个受保护配置）
| 配置块 | 保护原因 |
|--------|----------|
| REMOTE_API_CONFIG | API密钥安全 |
| SELF_AWARENESS_CONFIG | 隐私层级配置 |
| CONTROLLER_PERMISSION | 权限配置（含文件系统白名单） |
| HEADLESS_BROWSER | 浏览器路径安全 |
| EVOLUTION_CONFIG | 自进化开关（代码修改权限） |
| DIGITAL_LIFE_REGISTRY | 数字生命身份标识 |

### 3.2 黑名单与可覆盖列表的交集
- `_COVERABLE_CONFIGS` 包含27个配置块
- `_HOT_RELOAD_BLACKLIST` 包含7个配置块
- **交集只有1个**：`CONTROLLER_PERMISSION`（权限配置，含文件系统白名单）
- 其他6个黑名单配置不在 `_COVERABLE_CONFIGS` 中，因此环境变量本来就无法覆盖它们
- **但 CONTROLLER_PERMISSION 是关键漏洞**：可通过 `TTP_CONTROLLER_PERMISSION_*` 环境变量绕过文件系统白名单保护

## 四、P2-376修复详情

### 4.1 问题根因
- `_apply_env_overrides()` 遍历 `_COVERABLE_CONFIGS` 时，只检查 `var_dict is None`，不检查 `var_name in _HOT_RELOAD_BLACKLIST`
- 而 `__load_user_config()` 有完整的黑名单检查
- 两条覆盖通道安全策略不一致，环境变量通道成为绕过点

### 4.2 修复方案
- 在 `_apply_env_overrides()` 的 `for var_name, var_dict in _COVERABLE_CONFIGS.items()` 循环中，添加黑名单检查
- 与 `__load_user_config()` 保持一致的安全策略
- 拦截时打印安全日志：`[Config] 安全拦截: 环境变量 'xxx' 试图覆盖黑名单配置 'yyy'，已忽略`

### 4.3 修复验证
- ✅ ruff F类检查：All checks passed
- ✅ Python语法检查：config.py 导入成功
- ✅ 修复生效验证：导入时检测到 `TTP_REMOTE_API_KEY` 试图覆盖 `CONTROLLER_PERMISSION`，已被拦截
- ✅ pytest：69个相关测试全部通过（12个API密钥环境测试 + 57个补丁自应用测试）

## 五、与其他模块的交互

- **导入方**：几乎所有模块都 `import config`，配置变更影响全框架
- **热重载通知**：34个器官注册了配置刷新回调
- **测试覆盖**：test_api_key_env_m33.py（环境变量优先级）、test_patch_auto_approve_m53.py、test_patch_self_apply_m53.py

## 六、发现的其他问题/待确认项

1. **环境变量覆盖的键名匹配是小写精确匹配**：`TTP_REMOTE_API_KEY` 会匹配到 `CONTROLLER_PERMISSION` 中的 `remote_api_key` 键（如果存在），这可能导致意外的配置覆盖。需要确认 `CONTROLLER_PERMISSION` 是否真的有 `remote_api_key` 键。
2. **环境变量覆盖没有COW策略**：直接原地修改 `var_dict[dict_key]`，而 `__load_user_config()` 有COW策略。并发场景下可能有问题，但环境变量通常在启动时设置，运行时不变。
3. **黑名单拦截日志只打印不记录**：建议后续加入安全审计日志，记录所有被拦截的环境变量覆盖尝试。
