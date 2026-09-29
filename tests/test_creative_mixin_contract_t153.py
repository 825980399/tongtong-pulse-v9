"""T153-6 创意 Mixin 契约（P1 层）

任务书 T153-6① 15件参数化清单含 brain/pulse_inner_world_creative，但该模块导出的是
PulseInnerWorldCreativeMixin（Mixin），非独立器官，不承接 BasePulseOrgan 的 5 条器官断言。
本文件按 Mixin 真实形态验证：方法集稳定 + 被宿主 PulseInnerWorld 正确继承。

价值：T153-5 删除了 IW 6 个死方法，若误伤 creative 维度方法，本契约会因
issubclass / hasattr 断言失败而暴露回归。

纪律：每器官<=1文件（本文件为 creative Mixin 专项契约文件）。
"""
import inspect


def test_creative_mixin_method_set_and_inheritance():
    from organs.brain.pulse_inner_world_creative import PulseInnerWorldCreativeMixin as Mixin
    from organs.brain.PulseInnerWorld import PulseInnerWorld

    mixin_methods = [n for n, _ in inspect.getmembers(Mixin, predicate=inspect.isfunction)]
    assert mixin_methods, "PulseInnerWorldCreativeMixin 应定义方法集"

    # 宿主必须正确继承该 Mixin（四段继承之一，T-141 引入）
    assert issubclass(PulseInnerWorld, Mixin), "PulseInnerWorld 必须继承 PulseInnerWorldCreativeMixin"

    # 全部 Mixin 方法须在宿主可见（防止 T153-5 类删方法误伤 creative 维度）
    missing = [mn for mn in mixin_methods if not hasattr(PulseInnerWorld, mn)]
    assert not missing, f"宿主缺失继承的 creative 方法: {missing}"
