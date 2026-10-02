# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   三壳·config_body：硬件躯体纯配置壳（228行），无独立语义；③弃用标注（原定②删除或③，选③）。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""config_body —— 躯体全局硬件配置 config_body.py

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

# ===================== 一、TCP网络通信基础配置 =====================
ESP32_HOST = "192.168.4.1"
ESP32_TCP_PORT = 8080

# 视觉追踪参数
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
DEAD_ZONE_X = 30
DEAD_ZONE_Y = 20
PAN_PER_PIXEL = 0.1
TILT_PER_PIXEL = 0.1
MAX_PAN_ANGLE = 90
MAX_TILT_ANGLE = 30
TRACK_INTERVAL = 0.033

# ===================== 二、Vosk语音模型路径 =====================
VOSK_MODEL_PATH = "models/vosk-model-small-cn-0.22"

# ===================== 三、舵机驱动板I2C地址（仅两块） =====================
LU9685_ADDR_MAP = {
    "main_body": 0x40,    # 硬件通道0~15 → 全局0~15 头/手臂/躯干
    "leg_full": 0x41      # 硬件通道0~15 → 全局16~31 双腿髋膝+脚踝+备用
}

# ===================== 四、全局舵机通道映射（0~23 完整24轴行走人形，仅两块驱动板） =====================
SERVO_CHANNEL_MAP = {
    # ========== LU9685 0x40 全局0~15 头部+双臂+躯干 16轴 ==========
    # 头部云台3轴
    "head_pan": 0,
    "head_tilt": 1,
    "head_roll": 2,

    # 右臂5轴
    "right_shoulder_yaw": 3,
    "right_shoulder_pitch": 4,
    "right_elbow": 5,
    "right_wrist": 6,
    "right_hand_wave": 7,

    # 左臂5轴
    "left_shoulder_yaw": 8,
    "left_shoulder_pitch": 9,
    "left_elbow": 10,
    "left_wrist": 11,
    "left_hand_wave": 12,

    # 躯干腰部3轴
    "body_yaw": 13,
    "body_forward": 14,
    "body_sway": 15,

    # ========== LU9685 0x41 全局16~23 完整双腿（髋+膝+双脚踝 8轴） ==========
    # 左腿基础2轴
    "left_hip": 16,
    "left_knee": 17,
    # 右腿基础2轴
    "right_hip": 18,
    "right_knee": 19,
    # 左脚脚踝2轴（行走平衡核心）
    "left_ankle_pitch": 20,
    "left_ankle_roll": 21,
    # 右脚脚踝2轴（行走平衡核心）
    "right_ankle_pitch": 22,
    "right_ankle_roll": 23
}

# 通道数字反向映射
CHANNEL_TO_NAME = {ch: name for name, ch in SERVO_CHANNEL_MAP.items()}

# ===================== 五、全部24轴机械角度限位 =====================
SERVO_ANGLE_LIMIT = {
    # 头部
    "head_pan": {"min": 0, "max": 180, "default": 90},
    "head_tilt": {"min": 40, "max": 140, "default": 90},
    "head_roll": {"min": 60, "max": 120, "default": 90},

    # 右臂
    "right_shoulder_yaw": {"min": 20, "max": 160, "default": 90},
    "right_shoulder_pitch": {"min": 30, "max": 150, "default": 30},
    "right_elbow": {"min": 20, "max": 160, "default": 90},
    "right_wrist": {"min": 0, "max": 180, "default": 90},
    "right_hand_wave": {"min": 30, "max": 150, "default": 30},

    # 左臂
    "left_shoulder_yaw": {"min": 20, "max": 160, "default": 90},
    "left_shoulder_pitch": {"min": 30, "max": 150, "default": 30},
    "left_elbow": {"min": 20, "max": 160, "default": 90},
    "left_wrist": {"min": 0, "max": 180, "default": 90},
    "left_hand_wave": {"min": 30, "max": 150, "default": 30},

    # 躯干
    "body_yaw": {"min": 30, "max": 150, "default": 90},
    "body_forward": {"min": 40, "max": 140, "default": 90},
    "body_sway": {"min": 50, "max": 130, "default": 90},

    # 髋、膝
    "left_hip": {"min": 20, "max": 160, "default": 90},
    "left_knee": {"min": 30, "max": 150, "default": 90},
    "right_hip": {"min": 20, "max": 160, "default": 90},
    "right_knee": {"min": 30, "max": 150, "default": 90},

    # 脚踝行走轴
    "left_ankle_pitch": {"min": 30, "max": 150, "default": 90},
    "left_ankle_roll": {"min": 40, "max": 140, "default": 90},
    "right_ankle_pitch": {"min": 30, "max": 150, "default": 90},
    "right_ankle_roll": {"min": 40, "max": 140, "default": 90}
}

# ===================== 六、语音动作映射（原地静态 + 完整行走动作） =====================
VOICE_ACTION_SERVO = {
    # 原地静态动作（仅0~19基础轴，现阶段可直接调试）
    "挥手": {
        "ch": SERVO_CHANNEL_MAP["right_hand_wave"],
        "start_angle": 30,
        "end_angle": 150,
        "delay": 0.3,
        "loop_times": 2
    },
    "点头": {
        "ch": SERVO_CHANNEL_MAP["head_tilt"],
        "start_angle": 90,
        "end_angle": 40,
        "delay": 0.25,
        "loop_times": 2
    },
    "摇头": {
        "ch": SERVO_CHANNEL_MAP["head_pan"],
        "start_angle": 90,
        "end_angle": 30,
        "delay": 0.3,
        "loop_times": 2
    },
    "抬右手": {
        "ch": SERVO_CHANNEL_MAP["right_shoulder_pitch"],
        "start_angle": 30,
        "end_angle": 140,
        "delay": 0.4,
        "loop_times": 1
    },
    "放下手": {
        "ch": SERVO_CHANNEL_MAP["right_shoulder_pitch"],
        "start_angle": 140,
        "end_angle": 30,
        "delay": 0.4,
        "loop_times": 1
    },
    "弯腰鞠躬": {
        "ch": SERVO_CHANNEL_MAP["body_forward"],
        "start_angle": 90,
        "end_angle": 130,
        "delay": 0.5,
        "loop_times": 1
    },
    "站直": {
        "ch": SERVO_CHANNEL_MAP["body_forward"],
        "start_angle": 130,
        "end_angle": 90,
        "delay": 0.4,
        "loop_times": 1
    },
    "下蹲": {
        "multi_ch": [
            {"ch": SERVO_CHANNEL_MAP["left_knee"], "angle": 130},
            {"ch": SERVO_CHANNEL_MAP["right_knee"], "angle": 130},
            {"ch": SERVO_CHANNEL_MAP["body_forward"], "angle": 110}
        ],
        "delay": 0.6
    },
    "起身": {
        "multi_ch": [
            {"ch": SERVO_CHANNEL_MAP["left_knee"], "angle": 90},
            {"ch": SERVO_CHANNEL_MAP["right_knee"], "angle": 90},
            {"ch": SERVO_CHANNEL_MAP["body_forward"], "angle": 90}
        ],
        "delay": 0.5
    },

    # 完整行走动态动作（包含4路脚踝，两块驱动板即可运行）
    "原地踏步": {
        "multi_ch": [
            {"ch": SERVO_CHANNEL_MAP["left_hip"], "angle": 120},
            {"ch": SERVO_CHANNEL_MAP["left_knee"], "angle": 110},
            {"ch": SERVO_CHANNEL_MAP["left_ankle_pitch"], "angle": 110},
            {"ch": SERVO_CHANNEL_MAP["right_hip"], "angle": 90},
            {"ch": SERVO_CHANNEL_MAP["right_knee"], "angle": 90},
            {"ch": SERVO_CHANNEL_MAP["right_ankle_pitch"], "angle": 90}
        ],
        "delay": 0.4,
        "loop_times": 4
    },
    "向前走一步": {
        "multi_ch": [
            {"ch": SERVO_CHANNEL_MAP["body_sway"], "angle": 110},
            {"ch": SERVO_CHANNEL_MAP["left_hip"], "angle": 130},
            {"ch": SERVO_CHANNEL_MAP["left_knee"], "angle": 120},
            {"ch": SERVO_CHANNEL_MAP["left_ankle_pitch"], "angle": 120},
            {"ch": SERVO_CHANNEL_MAP["right_ankle_roll"], "angle": 100}
        ],
        "delay": 0.7
    },
    "后退一步": {
        "multi_ch": [
            {"ch": SERVO_CHANNEL_MAP["body_sway"], "angle": 70},
            {"ch": SERVO_CHANNEL_MAP["right_hip"], "angle": 130},
            {"ch": SERVO_CHANNEL_MAP["right_knee"], "angle": 120},
            {"ch": SERVO_CHANNEL_MAP["right_ankle_pitch"], "angle": 120},
            {"ch": SERVO_CHANNEL_MAP["left_ankle_roll"], "angle": 80}
        ],
        "delay": 0.7
    }
}

# ===================== 七、整机功能开关 =====================
# 脚踝轴启用开关：True = 加载全部24轴，支持行走；False = 仅0~19基础轴，忽略脚踝指令
ENABLE_ANKLE_AXIS = True
ENABLE_FACE_TRACK = True
ENABLE_VOICE_ACTION = True
ENABLE_LOAD_DOWNGRADE = True
# 舵机动作平滑参数
SERVO_SMOOTH_STEP = 3
SERVO_ACTION_MIN_INTERVAL = 0.08