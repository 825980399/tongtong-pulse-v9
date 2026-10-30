# -*- coding: utf-8 -*-
"""402 刀1 判据：事件字典生成器（L2 语义软着陆 · 只落台账不阻断热路径）。

★这组测试守的是「T0 实测纠正过的三处口径」，不是票面数字：
  1. 票面 223 = 189 条**字面量**事件常量 + 34 条 `PulseLayer`/`ErrorCode` 等
     非事件枚举（真水分）；189 之外还有 16 条 `Event` 注册表类引用别名
     ⇒ 条目 205 / 唯一值 185。票面「4 组重复」只对字面量层面成立。
  2. 事件实参可能是**关键字**（`event_type=ChatEvent.X`，全库 68 处）。
     只扫位置参数会把在发的事件判成"零发射"（实测虚报 73 → 真值 50）。
  3. consumers 必须**双源**：器官走 `get_resonance_conditions()`，
     直调 `register_condition` 的只有 15 处对话/UI ⇒ 只认后者会漏掉全部器官订阅。
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

_TOOL = os.path.join(_ROOT, "tools", "m402_build_event_dictionary.py")


def _load_tool():
    spec = importlib.util.spec_from_file_location("m402_eventdict_tool", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_tool()

_CACHE = {}


def _dict():
    """整批只构建一次（构建要遍历 400+ 文件，约 3~5 秒）。"""
    if "d" not in _CACHE:
        _CACHE["d"] = M.build_dictionary(_ROOT)
    return _CACHE["d"]


class TestConstants(unittest.TestCase):
    """常量提取口径。"""

    def test_01_only_event_classes_counted(self):
        """只算 `*Event` 类；非事件枚举单独记，不进事件表。"""
        c = M.collect_constants(_ROOT)
        cls_names = {e["class"] for e in c["entries"]}
        self.assertTrue(all(x.endswith("Event") for x in cls_names),
                        "事件常量只能来自 *Event 类，混入: %s"
                        % sorted(x for x in cls_names if not x.endswith("Event")))
        # 非事件枚举必须被识别出来（票面 223 的水分）
        self.assertGreaterEqual(sum(c["non_event_classes"].values()), 30,
                                "非事件枚举应当被单独识别（票面 223 的水分）")
        for must in ("PulseLayer", "ErrorCode", "LogLevel"):
            self.assertIn(must, c["non_event_classes"])

    def test_02_ticket_223_decomposed(self):
        """★票面 223 / 219 / 「4 组重复」三个数字的精确构成。

        223 = 189 条**字面量**事件常量 + 34 条非事件枚举；
        189 之外还有 16 条 `Event` 注册表类引用别名（`HEART_BEAT = HeartEvent.BEAT`）
        ⇒ 条目 205 条、唯一值 185 个。
        票面的「4 组重复」只对字面量层面成立：189 - 185 = 4。
        """
        c = M.collect_constants(_ROOT)
        lit = [e for e in c["entries"] if not e["resolve_from"]]
        ali = [e for e in c["entries"] if e["resolve_from"]]
        water = sum(c["non_event_classes"].values())
        self.assertEqual(len(lit), 189, "字面量事件常量条数")
        self.assertEqual(len(ali), 16, "Event 注册表类引用别名条数")
        self.assertEqual(len(c["entries"]), len(lit) + len(ali), "entries = 字面量 + 别名")
        self.assertEqual(len(lit) + water, 223, "★票面 223 的真实构成")
        uniq = {e["value"] for e in lit}
        self.assertEqual(len(uniq), 185, "字面量唯一事件值")
        self.assertEqual(len(uniq) + water, 219, "★票面 219 的真实构成")
        self.assertEqual(len(lit) - len(uniq), 4, "★票面「4 组重复」只对字面量层面成立")
        self.assertTrue(all(e["value"] for e in ali), "类引用别名必须全部解析成功")

    def test_03_class_reference_resolved(self):
        """`HEART_BEAT = HeartEvent.BEAT` 这类类引用必须解析出真实值。"""
        c = M.collect_constants(_ROOT)
        by_key = {"%s.%s" % (e["class"], e["name"]): e for e in c["entries"]}
        reg = by_key.get("Event.HEART_BEAT")
        self.assertIsNotNone(reg, "Event 注册表里应有 HEART_BEAT")
        self.assertEqual(reg["value"], "heart.beat")
        self.assertTrue(reg["resolve_from"], "该条应是类引用而来（resolve_from 非空）")


class TestDictionaryShape(unittest.TestCase):
    """产物结构（票面验收第 2 条：每条有 payload/producers/consumers）。"""

    def test_10_every_event_has_required_fields(self):
        d = _dict()
        need = ("class", "version", "payload", "producers", "consumers",
                "required_keys", "never_emitted")
        for v, e in d["events"].items():
            for f in need:
                self.assertIn(f, e, "事件 %s 缺字段 %s" % (v, f))
            self.assertIsInstance(e["class"], list)
            self.assertIsInstance(e["payload"], list)
            self.assertIsInstance(e["producers"], list)
            self.assertIsInstance(e["consumers"], list)

    def test_11_emitted_events_have_producer(self):
        """标为已发射的事件必须有 producers（否则标记自相矛盾）。"""
        d = _dict()
        bad = [v for v, e in d["events"].items()
               if not e["never_emitted"] and not e["producers"]]
        self.assertEqual(bad, [], "已发射却无 producers: %s" % bad[:5])

    def test_12_totals_self_consistent(self):
        d = _dict()
        t = d["totals"]
        self.assertEqual(t["emitted_in_prod"] + t["never_emitted"], t["unique_events"])
        self.assertEqual(len(d["events"]), t["unique_events"])

    def test_13_static_limits_declared(self):
        """★诚信约束：静态分析做不到的事必须写进产物，不能只写在报告里。"""
        d = _dict()
        lim = d["static_limits"]
        self.assertIn("dynamic_emit_sites", lim)
        self.assertGreater(lim["dynamic_emit_sites"], 0,
                           "本项目确实存在动态发射（变量/调用作事件实参）")
        self.assertIn("note", lim)
        self.assertIn("consumers_scope", lim)


class TestEmitDetection(unittest.TestCase):
    """发射面识别（★T0 第三处修正：关键字形态）。"""

    def test_20_keyword_event_type_detected(self):
        """★`event_type=ChatEvent.X` 关键字形态必须被识别。

        回归背景：首版只处理位置参数 ⇒ `chat.user_presence_detected` 明明在
        PulseVisualCortex 里发射，却被判成"零发射"（虚报契约断裂 73 个）。
        """
        d = _dict()
        e = d["events"].get("chat.user_presence_detected")
        self.assertIsNotNone(e)
        self.assertFalse(e["never_emitted"], "关键字发射的事件不得被判成零发射")
        self.assertIn("organs/senses/PulseVisualCortex.py", e["producers"])

    def test_21_positional_emit_detected(self):
        """位置参数形态（器官内 `self._emit(Event.X, {...})`）也必须识别。"""
        d = _dict()
        e = d["events"].get("heart.beat")
        self.assertIsNotNone(e)
        self.assertTrue(e["producers"], "heart.beat 必然在发")
        self.assertTrue(any("PulseHeart" in p for p in e["producers"]))

    def test_22_organ_first_emit_detected(self):
        """`core.emit("器官", Event.X)` —— 事件在第二实参，不能取错位。"""
        d = _dict()
        e = d["events"].get("chat.message")
        self.assertIsNotNone(e)
        self.assertFalse(e["never_emitted"])
        self.assertGreaterEqual(len(e["producers"]), 2,
                                "chat.message 有多个生产方（对话/网页/桥接）")

    def test_23_backup_files_not_scanned(self):
        """★备份文件是平铺的 `.bak_batchNN_X.py`，只靠目录过滤会漏。"""
        d = _dict()
        for v, e in d["events"].items():
            for p in e["producers"] + e["consumers"]:
                self.assertFalse(os.path.basename(p).startswith(".bak"),
                                 "扫描面混入备份文件: %s（事件 %s）" % (p, v))
                self.assertNotIn("docker_build_ctx", p,
                                 "扫描面混入构建副本: %s" % p)


class TestConsumers(unittest.TestCase):
    """订阅面（★T0 第二处修正：双源）。"""

    def test_30_resonance_conditions_are_consumers(self):
        """器官 `get_resonance_conditions()` 的声明必须进 consumers。"""
        d = _dict()
        e = d["events"].get("consent.propose")
        self.assertIsNotNone(e)
        self.assertIn("PulseConsent", e["consumers"],
                      "PulseConsent 在 get_resonance_conditions 里声明订阅 consent.propose")

    def test_31_literal_only_event_registered(self):
        """★直调 `register_condition(event_types=["a.b"])` 的**裸字符串**订阅不能丢。

        `snapshot.auto_save` 在 `main.py:2501` 以字面量订阅，但 const.py 里没有
        对应常量 ⇒ 它按定义不属于 events 表（本表口径 = const.py 常量），
        正确落点是 literal_only 段。旧断言直接查 events 表，必然失败。
        """
        d = _dict()
        self.assertNotIn("snapshot.auto_save", d["events"],
                         "无常量的事件不进 events 表（本表只收 const.py 常量）")
        lo = d.get("literal_only") or {}
        self.assertIn("snapshot.auto_save", lo, "裸字符串订阅必须落 literal_only")
        self.assertTrue(lo["snapshot.auto_save"]["consumers"],
                        "main.py 的 register_condition 订户要记下来")

    def test_34_literal_only_section_shape(self):
        """literal_only 段的自洽性：每条有 producers/consumers/payload，
        且不得与 events 表重名（重名说明常量识别漏了）。"""
        d = _dict()
        lo = d.get("literal_only") or {}
        self.assertTrue(lo, "本项目存在裸字符串事件（至少 snapshot.auto_save）")
        for k, r in lo.items():
            for f in ("producers", "consumers", "consumers_wildcard",
                      "payload", "has_constant", "static_orphan"):
                self.assertIn(f, r, "literal_only[%s] 缺字段 %s" % (k, f))
            self.assertFalse(r["has_constant"], "%s 无常量才该进 literal_only" % k)
            self.assertNotIn(k, d["events"], "%s 不得同时出现在 events 表" % k)

    def test_32_wildcard_separated_from_exact(self):
        """★通配订阅必须与精确订阅分开，否则「契约断裂」指标虚高。"""
        d = _dict()
        # 血管是万能观测者，它订的应是通配而不是精确
        e = d["events"].get("controller.mouse_click")
        self.assertIsNotNone(e)
        self.assertNotIn("PulseBloodVessel", e["consumers"],
                         "血管的通配订阅不得计入精确 consumers")
        self.assertIn("PulseBloodVessel", e["consumers_wildcard"])

    def test_33_emitted_events_have_consumer(self):
        """已发射事件应有订阅方（否则就是真·死信，属另一类缺陷）。"""
        d = _dict()
        no_cons = [v for v, e in d["events"].items()
                   if not e["never_emitted"] and not e["consumers"]
                   and not e["consumers_wildcard"]]
        self.assertEqual(no_cons, [], "已发射且完全无订阅（含通配）: %s" % no_cons[:5])


class TestNeverEmitted(unittest.TestCase):
    """never_emitted 分桶。"""

    def test_40_buckets_partition_complete(self):
        """各桶必须互斥且并集 == never 全集（不漏不重）。"""
        d = _dict()
        b = d["never_emitted_breakdown"]
        names = ("test_only", "deprecated", "orphan_consumer",
                 "wildcard_only", "reserved")
        union = []
        for n in names:
            self.assertIn(n, b)
            union.extend(b[n])
        self.assertEqual(len(union), len(set(union)), "桶之间不得重复")
        self.assertEqual(set(union), set(d["never_emitted_breakdown"]["orphan_consumer"])
                         | set(b["wildcard_only"]) | set(b["deprecated"])
                         | set(b["reserved"]) | set(b["test_only"]))
        never_from_events = {v for v, e in d["events"].items() if e["never_emitted"]}
        self.assertEqual(set(union), never_from_events, "分桶并集必须等于 never 全集")

    def test_41_every_never_has_reason(self):
        d = _dict()
        for v in d["never_emitted_breakdown"]["orphan_consumer"] + \
                d["never_emitted_breakdown"]["wildcard_only"]:
            self.assertTrue(d["events"][v]["never_reason"],
                            "never 事件 %s 缺 never_reason" % v)

    def test_42_orphan_consumer_semantics(self):
        """orphan_consumer = 有精确订阅却零发射（契约断裂）。"""
        d = _dict()
        for v in d["never_emitted_breakdown"]["orphan_consumer"]:
            e = d["events"][v]
            self.assertTrue(e["consumers"], "%s 标为 orphan 但无精确订阅" % v)
            self.assertTrue(e["never_emitted"], "%s 标为 orphan 但已发射" % v)
            self.assertIn("契约断裂", e["never_reason"])

    def test_43_consent_is_a_real_break(self):
        """★实证样本：PulseConsent 声称"承接提议与决策"，但全库没人发这两个事件。"""
        d = _dict()
        for v in ("consent.propose", "consent.decide"):
            e = d["events"][v]
            self.assertTrue(e["never_emitted"], "%s 应零发射" % v)
            self.assertIn("PulseConsent", e["consumers"])
            self.assertEqual(e["never_bucket"], "orphan_consumer")

    def test_44_test_only_is_empty_here(self):
        """★票面要求三类里的「仅测试发」在本项目是空集。

        tests 里的 emit 全是虚构事件名（`ping`/`x`/`a.one`/`demo.*`），
        没有任何**真实**事件只被测试发射 —— 若将来出现，本用例会红，
        提醒把该类真正拆出来。
        """
        d = _dict()
        self.assertEqual(d["never_emitted_breakdown"]["test_only"], [],
                         "出现了仅测试发射的真实事件，需更新分桶说明")


class TestDuplicates(unittest.TestCase):
    """重复值分组。"""

    def test_50_registry_reuse_not_counted_as_collision(self):
        """★`Event.HEART_BEAT = HeartEvent.BEAT` 是**刻意复用**，不是撞车。"""
        d = _dict()
        reuse = d["duplicates"]["reuse_by_registry"]
        coll = d["duplicates"]["colliding"]
        self.assertIn("heart.beat", reuse)
        self.assertNotIn("heart.beat", coll)

    def test_51_real_collision_identified(self):
        """真撞车：两个类**各自独立定义**同一个值。"""
        d = _dict()
        coll = d["duplicates"]["colliding"]
        self.assertIn("inference.result", coll,
                      "InferenceEvent.RESULT 与 InferenceEngineEvent.RESULT 各自定义同值")
        self.assertEqual(len(coll["inference.result"]), 2)
        # 真撞车的成员都不得是注册表 Event.* 的复用
        for v in coll:
            self.assertFalse(any(a.startswith("Event.") and a.count(".") == 1
                                 for a in coll[v]) and len(coll[v]) == 2,
                             "%s 若含 Event. 复用则不应算撞车" % v)


def _mini_project(base):
    """造一个迷你工程：1 个 const.py + 1 个器官（发射 + 订阅）。

    ★IO 契约类用例用它而不是扫全库 —— 全库一次构建要 11 秒，
      两次就是 20+ 秒，会让整片测试超时；而这类用例要验的是
      「只写目标文件 / 幂等」，与工程规模无关。
    """
    os.makedirs(os.path.join(base, "nucleus"), exist_ok=True)
    os.makedirs(os.path.join(base, "organs"), exist_ok=True)
    with io.open(os.path.join(base, "nucleus", "const.py"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(
            "# -*- coding: utf-8 -*-\n"
            "class HeartEvent:\n"
            "    BEAT = 'heart.beat'\n"
            "    ALIVE = 'heart.alive'\n"
            "\n"
            "class ChatEvent:\n"
            "    MESSAGE = 'chat.message'\n"
            "    IDLE = 'chat.idle'\n"
            "\n"
            "class Event:\n"
            "    HEART_BEAT = HeartEvent.BEAT\n"
            "\n"
            "class PulseLayer:\n"
            "    L0 = 'L0'\n"
            "    L1 = 'L1'\n")
    with io.open(os.path.join(base, "organs", "PulseHeart.py"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(
            "from nucleus.const import ChatEvent, HeartEvent\n"
            "\n"
            "class PulseHeart:\n"
            "    organ_name = '心脏'\n"
            "\n"
            "    def beat(self):\n"
            "        self._emit(HeartEvent.BEAT, {'beat_count': 1, 'interval': 60})\n"
            "        self._emit(event_type=HeartEvent.ALIVE, payload={'status': 'ok'})\n"
            "\n"
            "    def get_resonance_conditions(self):\n"
            "        return [{'organ_name': self.organ_name,\n"
            "                 'event_types': [ChatEvent.MESSAGE, ChatEvent.IDLE]}]\n")
    return base


class TestIOContract(unittest.TestCase):
    """IO 契约：只写目标文件、幂等、不改源码。"""

    def setUp(self):
        self.tmp = os.path.join(_ROOT, "tmp", "_m402_k1_iso")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_60_writes_only_target(self):
        """生成器只写目标文件，不碰任何源文件。"""
        proj = _mini_project(os.path.join(self.tmp, "proj"))
        out = os.path.join(self.tmp, "out", "event_dictionary.json")
        const_p = os.path.join(proj, "nucleus", "const.py")
        organ_p = os.path.join(proj, "organs", "PulseHeart.py")
        before = (os.path.getmtime(const_p), os.path.getmtime(organ_p))
        rc = M.main(["--root", proj, "--out", out, "--quiet"])
        after = (os.path.getmtime(const_p), os.path.getmtime(organ_p))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(before, after, "生成器不得修改任何源文件")
        d = json.load(io.open(out, encoding="utf-8"))
        self.assertEqual(d["schema"], M.SCHEMA)
        # 迷你工程的最小语义也要成立
        self.assertIn("heart.beat", d["events"])
        self.assertFalse(d["events"]["heart.beat"]["never_emitted"])
        self.assertTrue(d["events"]["heart.beat"]["producers"])
        self.assertIn("heart.alive", d["events"],
                      "关键字形态的发射（event_type=...）必须被识别")
        self.assertFalse(d["events"]["heart.alive"]["never_emitted"])
        self.assertIn("PulseHeart", d["events"]["chat.message"]["consumers"],
                      "get_resonance_conditions 声明必须进 consumers")
        # 非事件枚举不得进事件表
        self.assertNotIn("L0", d["events"])
        self.assertEqual(d["totals"]["non_event_enum_constants"], 2)

    def test_61_idempotent_except_timestamp(self):
        """连跑两次，除 generated_at 外结果完全一致。"""
        proj = _mini_project(os.path.join(self.tmp, "proj2"))
        a = M.build_dictionary(proj)
        b = M.build_dictionary(proj)
        a.pop("generated_at", None)
        b.pop("generated_at", None)
        self.assertEqual(json.dumps(a, sort_keys=True, ensure_ascii=False),
                         json.dumps(b, sort_keys=True, ensure_ascii=False),
                         "生成器不稳定：同样的源码产出不同字典")

    def test_62_json_serializable_and_utf8(self):
        d = _dict()
        s = json.dumps(d, ensure_ascii=False)
        self.assertIn("契约断裂", s, "中文字段应以原文写入（不转义）")
        json.loads(s)


class TestIsolation(unittest.TestCase):
    """只读隔离：本工具不进热路径。"""

    def test_70_not_imported_by_production(self):
        """★本模块只被 tools/tests 使用，不得被生产代码 import。"""
        hits = []
        for dirpath, dirnames, filenames in os.walk(_ROOT):
            dirnames[:] = [x for x in dirnames
                           if not x.startswith(".") and x not in
                           ("__pycache__", "node_modules", "venv", "code_backups",
                            "docker_build_ctx")]
            for fn in filenames:
                if not fn.endswith(".py") or fn.startswith(".bak"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, _ROOT).replace("\\", "/")
                if rel.startswith(("tools/", "tests/", "tmp/")):
                    continue
                try:
                    t = io.open(fp, encoding="utf-8", errors="replace").read()
                except OSError as exc:
                    silent_exc(exc, where="test_m402_k1_isolation:read:%s" % rel,
                               level="debug")
                    continue
                if "m402_build_event_dictionary" in t:
                    hits.append(rel)
        self.assertEqual(hits, [], "事件字典工具不得进生产热路径: %s" % hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
