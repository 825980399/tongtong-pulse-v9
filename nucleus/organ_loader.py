# -*- coding: utf-8 -*-
"""
organ_loader.py —— 器官加载器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 动态加载与卸载器官模块
机制: 基于OrganMeta类实现，包含6个核心方法
定位: 器官管理层
"""

import importlib
import os
from typing import Any

from nucleus.logger import get_module_logger


_module_logger = get_module_logger("OrganLoader")


class OrganMeta:
    """器官元数据定义"""
    def __init__(self, 
                 name: str,
                 module_path: str,
                 class_name: str,
                 system: str,
                 always_online: bool = True,
                 feature_flag: str | None = None,
                 extra_deps: dict[str, str] | None = None,
                 post_wiring: list[dict[str, str]] | None = None,
                 attr_name: str | None = None):
        """
        Args:
            name: 器官中文名称
            module_path: 模块路径（如 'organs.body.PulseHeart'）
            class_name: 类名（如 'PulseHeart'）
            system: 所属系统（如 'body', 'brain', 'senses' 等）
            always_online: 生命线核心程度标注（bool）。
                        True  = 生命线核心器官，永久在线，不可被 feature_flag 关闭
                                （即使误声明 feature_flag，assemble 也强制创建）。
                        False = 可降级器官，受 feature_flag 开关控制。
                        与 feature_flag 的关系（★技术债治理，语义已统一）：
                        - always_online=True  ⇒ 不应声明 feature_flag（核心不可关）
                        - always_online=False ⇒ 应声明 feature_flag（承载开关语义）
                        - 二者是「核心程度」与「可开关性」两个维度，正常情况互斥互补；
                          validate() 会校验矛盾组合并报错。
            feature_flag: 功能开关名称（如 'enable_vision'），存在时装配阶段
                        依据 FEATURE[feature_flag] 决定是否降级（关闭→置 None）。
                        与 always_online 独立：核心器官可同时有 feature_flag
                        （默认开，仍可被显式关闭）。
            extra_deps: 额外依赖注入（硬依赖），key=属性名, value=框架组件名/器官名。
                        硬依赖参与拓扑排序，必须在创建时就绪。
                        框架组件名: 'node_pool', 'knowledge_tree', 'frequency_codec',
                        'resonance_engine', 'self_awareness', 'interest_model',
                        'white_cell', 'touch', 'system_manager' 等。
            post_wiring: 组装期后置注入（软依赖），列表元素形如
                        {'target': 'qica', 'setter': 'set_qica'}。
                        软依赖不参与拓扑排序，用于前向引用与环依赖的晚绑定回填。
            attr_name: 框架英文属性名（如 'heart', 'cortex'），
                       装配时用于 setattr(framework, attr_name, organ)。
        """
        self.name = name
        self.module_path = module_path
        self.class_name = class_name
        self.system = system
        self.always_online = always_online
        self.feature_flag = feature_flag
        self.extra_deps = extra_deps or {}
        self.post_wiring = post_wiring or []
        self.attr_name = attr_name or name


class OrganLoader:
    """器官加载器"""
    
    # 框架组件名到实际属性名的映射
    _framework_components = {
        'node_pool': 'node_pool',
        'knowledge_tree': 'knowledge_tree',
        'frequency_codec': 'frequency_codec',
        'resonance_engine': 'resonance_engine',
        'self_awareness': 'self_awareness',
        'interest_model': 'interest_model',
        'white_cell': 'white_cell',
        'touch': 'touch',
        'system_manager': 'system_manager',
    }
    
    def __init__(self, framework):
        """
        Args:
            framework: PulseFramework 实例
        """
        self.framework = framework
        self._loaded_organs: dict[str, Any] = {}
    
    def scan_organs_directory(self) -> list[OrganMeta]:
        """
        扫描 organs/ 目录，通过导入模块获取 ORGAN_META。
        返回收集到的元数据列表。
        """
        organs_dir = os.path.join(os.path.dirname(__file__), '..', 'organs')
        metas = []
        
        # 遍历 organs/ 下的所有子目录
        for system_dir in os.listdir(organs_dir):
            system_path = os.path.join(organs_dir, system_dir)
            if not os.path.isdir(system_path) or system_dir.startswith('_'):
                continue
            
            for filename in os.listdir(system_path):
                if not filename.endswith('.py') or filename.startswith('_'):
                    continue
                
                module_name = filename[:-3]  # 去掉 .py
                full_module_path = f"organs.{system_dir}.{module_name}"
                
                try:
                    module = importlib.import_module(full_module_path)
                    if hasattr(module, 'ORGAN_META'):
                        meta_dict = module.ORGAN_META
                        meta = OrganMeta(
                            name=meta_dict.get('name', module_name),
                            module_path=full_module_path,
                            class_name=meta_dict.get('class_name', module_name),
                            system=meta_dict.get('system', system_dir),
                            always_online=meta_dict.get('always_online', True),
                            feature_flag=meta_dict.get('feature_flag'),
                            extra_deps=meta_dict.get('extra_deps', {}),
                            post_wiring=meta_dict.get('post_wiring', []),
                            attr_name=meta_dict.get('attr_name'),
                        )
                        metas.append(meta)
                except Exception as e:
                    _module_logger.error(f"加载模块 {full_module_path} 失败: {e}")
        
        return metas
    
    def load_organs(self, metas: list[OrganMeta] | None = None):
        """
        ★插件化阶段1：仅扫描 ORGAN_META 注册表，不创建器官。

        说明：main.py 的器官装配完全通过「硬编码 _create_organ + load_organs_from_dir」完成，
        且两者已正确注入 node_pool 等依赖。若本方法再根据 ORGAN_META 创建实例，
        会因 extra_deps 为空而覆盖已注入依赖的器官（导致 node_pool 丢失、胃/前额叶崩溃）。

        因此阶段1只建立「器官注册表」（供文档生成/依赖分析/健康面板使用），
        真正「声明式装配」留待插件化阶段2/3（补齐 extra_deps + 拓扑排序）再启用。
        """
        if metas is None:
            metas = self.scan_organs_directory()
        # 保存注册表（供查询），不创建任何器官实例
        self._scanned_metas = metas
        return metas
    def load_organs_from_dir(self, dir_name: str) -> dict[str, Any]:
        """
        ★v18.0新增：从指定器官目录动态加载所有器官。
        
        遍历 organs/{dir_name}/ 目录下的所有 .py 文件，
        动态导入类并创建实例，自动注入 info_field 和 pulse_core。
        
        Args:
            dir_name: organs/下的子目录名（如"immune"、"genetic"、"core"）
        
        Returns:
            {器官类名: 器官实例} 字典
        """
        result = {}
        organs_dir = os.path.join(
            os.path.dirname(__file__), '..', 'organs', dir_name
        )
        if not os.path.isdir(organs_dir):
            return result
        
        # ★P3-3修复：已下线器官黑名单（功能被 PulseInnerWorld 推理链路替代，_simulate_inference 为假实现），
        # 跳过自动加载以消除「订阅了 inference.execute 却无人发射」的幽灵监听。
        _OFFLINE_ORGANS = {"PulseInferenceEngine"}
        for file_name in sorted(os.listdir(organs_dir)):
            if not file_name.endswith(".py") or file_name.startswith("_"):
                continue
            organ_class_name = file_name[:-3]  # 去掉 .py 后缀
            if organ_class_name in _OFFLINE_ORGANS:
                continue
            try:
                # 动态导入模块
                module_path = f"organs.{dir_name}.{organ_class_name}"
                module = __import__(module_path, fromlist=[organ_class_name])
                organ_class = getattr(module, organ_class_name)
                # 使用框架的通用创建方法（自动注入info_field和pulse_core）
                organ = self.framework.create_organ(organ_class, organ_class_name)
                result[organ_class_name] = organ
            except Exception as e:
                _module_logger.error(f"动态加载器官失败({organ_class_name}): {e}")
        
        return result
    def get_all_organs(self) -> list:
        """返回所有已加载的器官实例列表"""
        return list(self.framework.organs.values())