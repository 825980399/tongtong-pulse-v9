"""function_meta —— 功能模块元数据定义（v9.5 分层脉冲版）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""


class FunctionMeta:
    """功能模块元数据定义（v9.5）"""
    
    def __init__(self, 
                 name: str,
                 module_path: str,
                 class_name: str,
                 always_on: bool = True,
                 feature_flag: str | None = None,
                 thread_mode: str = "daemon",
                 default_layer: str = "L1"):
        """
        Args:
            name: 功能模块名称（中文，如"对话交互"）
            module_path: 模块路径（如 'functions.chat.chat_service'）
            class_name: 类名（如 'ChatService'）
            always_on: 是否始终在线
            feature_flag: 功能开关名称
            thread_mode: 运行模式 —— "daemon"（守护线程）或 "blocking"（阻塞模式）
            default_layer: 默认脉冲层级（v9.5新增）—— L0/L1/L2/L3
        """
        self.name = name
        self.module_path = module_path
        self.class_name = class_name
        self.always_on = always_on
        self.feature_flag = feature_flag
        self.thread_mode = thread_mode
        self.default_layer = default_layer