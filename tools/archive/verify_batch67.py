"""第67批验收测试 - 配置项和模块导入验证"""
import sys

sys.path.insert(0, r'<PROJECT_ROOT>')

print("=== 1. 配置项验证 ===")
import config

config_items = [
    'SNAPSHOT_ASYNC_SAVE',
    'SNAPSHOT_SAVE_TIMEOUT',
    'FULL_SAVE_INTERVAL',
    'INCREMENTAL_MAX_NODES',
    'SNAPSHOT_INCREMENTAL_LOG_MAX_LINES',
    'ENABLE_KAL',
    'KAL_STORAGE_BACKEND',
    'KAL_CACHE_SIZE',
    'QUEUE_DEPTH_WARNING_THRESHOLD',
    'WRITE_GUARD_FORCE_ENV',
]
for item in config_items:
    val = getattr(config, item, 'NOT FOUND')
    status = '✅' if val != 'NOT FOUND' else '❌'
    print(f"  {status} {item} = {val}")

print("\n=== 2. 模块导入验证 ===")
modules = [
    ('nucleus.mnemosyne.PulseSnapshot', 'PulseSnapshot'),
    ('nucleus.knowledge_access_layer', 'KnowledgeAccessLayer'),
    ('nucleus.runtime_metrics', 'AdaptiveFrequencyController'),
    ('nucleus.data.write_guard', 'resolve_env'),
]
for mod_path, class_name in modules:
    try:
        mod = __import__(mod_path, fromlist=[class_name])
        obj = getattr(mod, class_name, None)
        if obj:
            print(f"  ✅ {mod_path}.{class_name} 导入成功")
        else:
            print(f"  ❌ {mod_path}.{class_name} 不存在")
    except Exception as e:
        print(f"  ❌ {mod_path} 导入失败: {e}")

print("\n=== 3. KAL单例验证 ===")
try:
    from nucleus.knowledge_access_layer import KnowledgeAccessLayer
    kal1 = KnowledgeAccessLayer()
    kal2 = KnowledgeAccessLayer()
    if kal1 is kal2:
        print(f"  ✅ KAL单例验证通过 (id={id(kal1)})")
    else:
        print(f"  ❌ KAL单例验证失败 (id1={id(kal1)}, id2={id(kal2)})")
except Exception as e:
    print(f"  ❌ KAL单例验证异常: {e}")

print("\n=== 4. WriteGuard环境判定验证 ===")
try:
    from nucleus.data.write_guard import env_reason, resolve_env
    env = resolve_env()
    reason = env_reason()
    print(f"  ✅ 当前环境判定: {env}")
    print(f"  ✅ 判定依据: {reason}")
    if env == 'production':
        print("  ✅ 框架进程正确判定为production（WriteGuard误判已修复）")
    else:
        print(f"  ⚠️ 框架进程判定为{env}，需要确认是否正确")
except Exception as e:
    print(f"  ❌ WriteGuard环境判定异常: {e}")

print("\n=== 5. 自适应降频控制器验证 ===")
try:
    from nucleus.runtime_metrics import AdaptiveFrequencyController, assess_load_level
    controller = AdaptiveFrequencyController()
    controller.register('test_operation', normal_interval=60, min_interval=300)
    controller.register('critical_operation', normal_interval=1, is_critical=True)
    
    load = assess_load_level()
    print(f"  ✅ 当前负载等级: {load}")
    
    interval_normal = controller.get_interval('test_operation')
    interval_critical = controller.get_interval('critical_operation')
    print(f"  ✅ 普通操作间隔: {interval_normal}秒")
    print(f"  ✅ 关键操作间隔: {interval_critical}秒 (应为0，不降频)")
    
    if interval_critical == 0:
        print("  ✅ 关键操作白名单验证通过")
    else:
        print("  ❌ 关键操作白名单验证失败")
except Exception as e:
    print(f"  ❌ 自适应降频控制器异常: {e}")

print("\n=== 验收测试完成 ===")
