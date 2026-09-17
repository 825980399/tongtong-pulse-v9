#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_rom_sys.h"
#include "nvs_flash.h"

static const char *TAG = "LU9685_SMOOTH";

// ================= 硬件配置 =================
#define I2C_SDA_PIN        7
#define I2C_SCL_PIN        8

#define BOARD0_ADDR  0x00  // 左腿驱动板
#define BOARD1_ADDR  0x02  // 右腿驱动板

#define CMD_RESET     0xFB
#define CMD_BATCH     0xFD

#define UART_PORT      UART_NUM_0
#define UART_BUF_SIZE  256

// I2C互斥锁 解决多任务总线冲突
SemaphoreHandle_t i2c_mutex;
// ===========================================

// ================= 站姿数据（基准，永远不被步态修改） =================
// 0号板：左腿
uint8_t pose_board0[20] = {
    90,   // ch0  未使用
    90,   // ch1  左腿-髋侧摆（角度增大=向外）
    90,   // ch2  未使用
    90,   // ch3  未使用
    90,   // ch4  左腿-髋前后（角度增大=向前）
    90,   // ch5  未使用
    90,   // ch6  未使用
    90,   // ch7  未使用
    90,   // ch8  左腿-膝关节（角度增大=向前）
    90,   // ch9  未使用
    90,   // ch10 未使用
    90,   // ch11 未使用
    90,   // ch12 左腿-踝侧摆（角度增大=向外）
    90,   // ch13 未使用
    90,   // ch14 未使用
    255,  // ch15 关闭
    255,  // ch16 关闭
    255,  // ch17 关闭
    255,  // ch18 关闭
    255   // ch19 关闭
};

// 1号板：右腿
uint8_t pose_board1[20] = {
    90,   // ch0  未使用
    75,   // ch1  右腿-髋侧摆（角度增大=向内）
    90,   // ch2  未使用
    90,   // ch3  未使用
    65,   // ch4  右腿-髋前后（角度增大=向后）
    90,   // ch5  未使用
    90,   // ch6  未使用
    90,   // ch7  未使用
    90,   // ch8  右腿-膝关节（角度增大=向后）
    90,   // ch9  未使用
    90,   // ch10 未使用
    90,   // ch11 未使用
    92,   // ch12 右腿-踝侧摆（角度增大=向内）
    90,   // ch13 未使用
    90,   // ch14 未使用
    255,  // ch15 关闭
    255,  // ch16 关闭
    255,  // ch17 关闭
    255,  // ch18 关闭
    255   // ch19 关闭
};

// 实时角度缓存（步态专用，只改这个，绝不碰基准站姿）
uint8_t current_board0[20], current_board1[20];

// 步态核心参数
int32_t step_length = 5;       // 步幅：髋前后摆动角度
int32_t step_height = 8;       // 抬腿高度：膝关节弯曲角度
int32_t shift_amount = 4;      // 重心侧移量：髋侧摆偏移角度
int32_t dead_zone = 2;         // 角度死区：抑制齿隙抖动
int32_t speed_level = 2;       // 速度等级：1~10，1最慢，10最快
int32_t step_inc = 1;          // 单步最大角度增量，越小越平滑

// 内部计算参数
int step_delay = 40;

TaskHandle_t walk_task_handle = NULL;
volatile bool walk_running = false;
// ===========================================

// ================= 软件I2C底层（全部加互斥锁） =================
static void i2c_delay(void)
{
    esp_rom_delay_us(10);
}

static void i2c_start(void)
{
    gpio_set_level(I2C_SDA_PIN, 1);
    gpio_set_level(I2C_SCL_PIN, 1);
    i2c_delay();
    gpio_set_level(I2C_SDA_PIN, 0);
    i2c_delay();
    gpio_set_level(I2C_SCL_PIN, 0);
    i2c_delay();
}

static void i2c_stop(void)
{
    gpio_set_level(I2C_SDA_PIN, 0);
    i2c_delay();
    gpio_set_level(I2C_SCL_PIN, 1);
    i2c_delay();
    gpio_set_level(I2C_SDA_PIN, 1);
    i2c_delay();
}

static uint8_t i2c_send_byte(uint8_t data)
{
    for(uint8_t i = 0; i < 8; i++)
    {
        gpio_set_level(I2C_SDA_PIN, (data & 0x80) ? 1 : 0);
        data <<= 1;
        i2c_delay();
        gpio_set_level(I2C_SCL_PIN, 1);
        i2c_delay();
        gpio_set_level(I2C_SCL_PIN, 0);
        i2c_delay();
    }

    gpio_set_level(I2C_SDA_PIN, 1);
    i2c_delay();
    gpio_set_level(I2C_SCL_PIN, 1);
    i2c_delay();
    uint8_t ack = gpio_get_level(I2C_SDA_PIN);
    gpio_set_level(I2C_SCL_PIN, 0);
    i2c_delay();

    return ack;
}

// ================= 驱动函数 加互斥锁保护 =================
uint8_t lu9685_reset(uint8_t dev_addr)
{
    xSemaphoreTake(i2c_mutex, portMAX_DELAY);
    i2c_start();
    if(i2c_send_byte(dev_addr) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    if(i2c_send_byte(CMD_RESET) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    if(i2c_send_byte(0xFB) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    i2c_stop();
    xSemaphoreGive(i2c_mutex);
    return 0;
}

uint8_t lu9685_set_angle(uint8_t dev_addr, uint8_t ch, uint8_t angle)
{
    xSemaphoreTake(i2c_mutex, portMAX_DELAY);
    i2c_start();
    if(i2c_send_byte(dev_addr) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    if(i2c_send_byte(ch) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    if(i2c_send_byte(angle) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    i2c_stop();
    xSemaphoreGive(i2c_mutex);
    return 0;
}

uint8_t lu9685_set_all(uint8_t dev_addr, uint8_t *angles)
{
    xSemaphoreTake(i2c_mutex, portMAX_DELAY);
    i2c_start();
    if(i2c_send_byte(dev_addr) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    if(i2c_send_byte(CMD_BATCH) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    for(int i = 0; i < 20; i++) {
        if(i2c_send_byte(angles[i]) != 0) { i2c_stop(); xSemaphoreGive(i2c_mutex); return 1; }
    }
    i2c_stop();
    xSemaphoreGive(i2c_mutex);
    return 0;
}

// ================= 速度等级计算 =================
void update_speed_from_level(void)
{
    if(speed_level < 1) speed_level = 1;
    if(speed_level > 10) speed_level = 10;
    step_delay = 60 - (speed_level - 1) * 5;
}

// ================= NVS掉电存储 =================
void save_all_to_nvs(void)
{
    nvs_handle_t nvs_handle;
    nvs_open("robot_pose", NVS_READWRITE, &nvs_handle);
    
    nvs_set_blob(nvs_handle, "board0", pose_board0, sizeof(pose_board0));
    nvs_set_blob(nvs_handle, "board1", pose_board1, sizeof(pose_board1));
    
    nvs_set_i32(nvs_handle, "step_len", step_length);
    nvs_set_i32(nvs_handle, "step_h", step_height);
    nvs_set_i32(nvs_handle, "shift", shift_amount);
    nvs_set_i32(nvs_handle, "deadzone", dead_zone);
    nvs_set_i32(nvs_handle, "speed", speed_level);
    nvs_set_i32(nvs_handle, "step_inc", step_inc);
    
    nvs_commit(nvs_handle);
    nvs_close(nvs_handle);
    ESP_LOGI(TAG, "✅ 站姿+步态+平滑参数已全部保存，掉电不丢失");
}

void load_all_from_nvs(void)
{
    nvs_handle_t nvs_handle;
    size_t size = 20;
    esp_err_t err = nvs_open("robot_pose", NVS_READONLY, &nvs_handle);
    if(err == ESP_OK) {
        nvs_get_blob(nvs_handle, "board0", pose_board0, &size);
        size = 20;
        nvs_get_blob(nvs_handle, "board1", pose_board1, &size);
        
        nvs_get_i32(nvs_handle, "step_len", &step_length);
        nvs_get_i32(nvs_handle, "step_h", &step_height);
        nvs_get_i32(nvs_handle, "shift", &shift_amount);
        nvs_get_i32(nvs_handle, "deadzone", &dead_zone);
        nvs_get_i32(nvs_handle, "speed", &speed_level);
        nvs_get_i32(nvs_handle, "step_inc", &step_inc);
        
        nvs_close(nvs_handle);
        update_speed_from_level();
        ESP_LOGI(TAG, "已加载Flash保存的全部参数");
    } else {
        update_speed_from_level();
        ESP_LOGI(TAG, "无保存记录，使用默认平滑参数");
    }
    memcpy(current_board0, pose_board0, 20);
    memcpy(current_board1, pose_board1, 20);
}

void clear_nvs_data(void)
{
    nvs_handle_t nvs_handle;
    nvs_open("robot_pose", NVS_READWRITE, &nvs_handle);
    nvs_erase_all(nvs_handle);
    nvs_commit(nvs_handle);
    nvs_close(nvs_handle);
    ESP_LOGI(TAG, "✅ 已清除所有保存参数，重启后使用默认值");
}

void go_home_pose(void)
{
    lu9685_set_all(BOARD0_ADDR, pose_board0);
    lu9685_set_all(BOARD1_ADDR, pose_board1);
    memcpy(current_board0, pose_board0, 20);
    memcpy(current_board1, pose_board1, 20);
    ESP_LOGI(TAG, "已回到初始站姿");
}

// ================= 步态生成函数修复：每个case全部变量清零、髋侧摆符号修正 =================
void build_gait_frame(int phase, uint8_t *out0, uint8_t *out1)
{
    memcpy(out0, pose_board0, 20);
    memcpy(out1, pose_board1, 20);

    int hip_side_l = 0, hip_side_r = 0;
    int hip_fwd_l = 0, hip_fwd_r = 0;
    int knee_l = 0, knee_r = 0;
    // 强制脚踝无任何动态补偿，彻底消除差值
    int ankle_roll_l = 0, ankle_roll_r = 0;

    switch(phase) {
        case 0:
            break;
        case 1: // 重心左移，髋侧摆反向修正
            hip_side_l = shift_amount;
            hip_side_r = -shift_amount;
            break;
        case 2: // 抬右腿
            hip_side_l = shift_amount;
            hip_side_r = -shift_amount;
            hip_fwd_r = -step_length;
            knee_r = step_height;
            break;
        case 3: // 右腿落地
            hip_fwd_r = -step_length;
            break;
        case 4: // 重心右移
            hip_side_l = -shift_amount;
            hip_side_r = shift_amount;
            break;
        case 5: // 抬左腿
            hip_side_l = -shift_amount;
            hip_side_r = shift_amount;
            hip_fwd_l = step_length;
            knee_l = -step_height;
            break;
        case 6: // 左腿落地
            hip_fwd_l = step_length;
            break;
    }

    // 仅更新上三层关节
    out0[1]  += hip_side_l;
    out0[4]  += hip_fwd_l;
    out0[8]  += knee_l;
    // 脚踝直接赋值基准，不加任何偏移
    out0[12] = pose_board0[12];

    out1[1]  += hip_side_r;
    out1[4]  += hip_fwd_r;
    out1[8]  += knee_r;
    // 脚踝直接赋值基准，不加任何偏移
    out1[12] = pose_board1[12];

    // 限幅保护
    for(int i=0; i<20; i++)
    {
        if(out0[i] > 160) out0[i] = 160;
        if(out1[i] > 160) out1[i] = 160;
        if(out0[i] < 20) out0[i] = 20;
        if(out1[i] < 20) out1[i] = 20;
    }
}
// ================= 平滑插值修复：乘法前置，避免整数除法归零 =================
void smooth_goto_frame(int target_phase)
{
    uint8_t target0[20], target1[20];
    uint8_t last_send0[20], last_send1[20];

    memcpy(last_send0, current_board0, 20);
    memcpy(last_send1, current_board1, 20);

    build_gait_frame(target_phase, target0, target1);

    int max_diff = 0;
    for(int i = 0; i < 20; i++) {
        int d0 = abs(target0[i] - current_board0[i]);
        int d1 = abs(target1[i] - current_board1[i]);
        if(d0 > max_diff) max_diff = d0;
        if(d1 > max_diff) max_diff = d1;
    }

    int total_steps = 1;
    if(step_inc > 0 && max_diff > 0) {
        total_steps = max_diff / step_inc;
        if(max_diff % step_inc != 0) total_steps += 1;
    }
    if(total_steps < 1) total_steps = 1;
    if(total_steps > 200) total_steps = 200;

    for(int step = 1; step <= total_steps; step++) {
        if(!walk_running) return;
        
        uint8_t cur0[20], cur1[20];
        bool need_send = false;

        for(int i = 0; i < 20; i++) {
            // 修复：先乘后除，不会出现前序步数不动、最后一步猛跳
            int delta0 = target0[i] - current_board0[i];
            cur0[i] = current_board0[i] + (delta0 * step) / total_steps;

            int delta1 = target1[i] - current_board1[i];
            cur1[i] = current_board1[i] + (delta1 * step) / total_steps;

            if(abs(cur0[i] - last_send0[i]) >= dead_zone) {
                last_send0[i] = cur0[i];
                need_send = true;
            }
            if(abs(cur1[i] - last_send1[i]) >= dead_zone) {
                last_send1[i] = cur1[i];
                need_send = true;
            }
        }

        if(need_send) {
            lu9685_set_all(BOARD0_ADDR, last_send0);
            lu9685_set_all(BOARD1_ADDR, last_send1);
        }
        vTaskDelay(pdMS_TO_TICKS(step_delay));
    }

    lu9685_set_all(BOARD0_ADDR, target0);
    lu9685_set_all(BOARD1_ADDR, target1);
    memcpy(current_board0, target0, 20);
    memcpy(current_board1, target1, 20);
}

// 走路主任务
// 走路主任务（修复起步突变）
void walk_task(void *arg)
{
    ESP_LOGI(TAG, "步态任务已启动");
    while(1) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        ESP_LOGI(TAG, "开始行走，过渡中立姿态");
        smooth_goto_frame(0);
        vTaskDelay(pdMS_TO_TICKS(100));
        int phase_seq[] = {1,2,3,4,5,6,0};
        int seq_len = sizeof(phase_seq)/sizeof(int);
        while(walk_running) {
            for(int i = 0; i < seq_len; i++) {
                if(!walk_running) break;
                smooth_goto_frame(phase_seq[i]);
            }
        }
        smooth_goto_frame(0);
        go_home_pose();
        ESP_LOGI(TAG, "已停止行走，回到初始站姿");
    }
}
// ================= 串口指令任务 =================
static void uart_cmd_task(void *arg)
{
    uint8_t buf[UART_BUF_SIZE];
    char cmd[32], param1[8], param2[8], param3[8], param4[8];

    ESP_LOGI(TAG, "=== 串口控制台已启动 ===");
    ESP_LOGI(TAG, "基础指令: scan / reset / set / off / home / save / clear_nvs");
    ESP_LOGI(TAG, "步态指令: walk start / walk stop");
    ESP_LOGI(TAG, "调参指令: set_step 步幅 抬腿 侧移");
    ESP_LOGI(TAG, "速度指令: set_speed 等级(1~10)");
    ESP_LOGI(TAG, "死区指令: set_deadzone 度数(0~5)");
    ESP_LOGI(TAG, "平滑指令: set_stepinc 单步度数(1~5)");

    while(1)
    {
        int len = uart_read_bytes(UART_PORT, buf, UART_BUF_SIZE - 1, pdMS_TO_TICKS(20));
        if(len <= 0) continue;

        buf[len] = '\0';
        for(int i = 0; i < len; i++) {
            if(buf[i] == '\r' || buf[i] == '\n') buf[i] = '\0';
        }

        int cnt = sscanf((char*)buf, "%s %s %s %s %s", cmd, param1, param2, param3, param4);
        if(cnt < 1) continue;

        if(strcmp(cmd, "scan") == 0)
        {
            ESP_LOGI(TAG, "--- 设备扫描 ---");
            ESP_LOGI(TAG, "0号板(左腿): %s", lu9685_reset(BOARD0_ADDR)==0 ? "✅ 在线" : "❌ 离线");
            ESP_LOGI(TAG, "1号板(右腿): %s", lu9685_reset(BOARD1_ADDR)==0 ? "✅ 在线" : "❌ 离线");
        }
        else if(strcmp(cmd, "reset") == 0 && cnt >= 2)
        {
            int board = atoi(param1);
            uint8_t addr = (board == 0) ? BOARD0_ADDR : BOARD1_ADDR;
            ESP_LOGI(TAG, "%d号板复位: %s", board, lu9685_reset(addr)==0 ? "成功" : "失败");
        }
        else if(strcmp(cmd, "set") == 0 && cnt >= 4)
        {
            int board = atoi(param1);
            int ch = atoi(param2);
            int angle = atoi(param3);
            uint8_t addr = (board == 0) ? BOARD0_ADDR : BOARD1_ADDR;

            if(ch < 0 || ch > 19 || angle < 0 || angle > 180) {
                ESP_LOGE(TAG, "参数错误：通道0~19，角度0~180");
                continue;
            }
            
            if(board == 0) pose_board0[ch] = angle;
            else pose_board1[ch] = angle;

            lu9685_set_angle(addr, ch, angle);
            memcpy(current_board0, pose_board0, 20);
            memcpy(current_board1, pose_board1, 20);
            ESP_LOGI(TAG, "%d号板 通道%d → %d° : 成功", board, ch, angle);
        }
        else if(strcmp(cmd, "off") == 0 && cnt >= 3)
        {
            int board = atoi(param1);
            int ch = atoi(param2);
            uint8_t addr = (board == 0) ? BOARD0_ADDR : BOARD1_ADDR;

            if(board == 0) pose_board0[ch] = 255;
            else pose_board1[ch] = 255;

            lu9685_set_angle(addr, ch, 255);
            ESP_LOGI(TAG, "%d号板 通道%d 关闭: 成功", board, ch);
        }
        else if(strcmp(cmd, "home") == 0)
        {
            if(walk_running) {
                walk_running = false;
                xTaskNotifyGive(walk_task_handle);
            }
            go_home_pose();
        }
        else if(strcmp(cmd, "save") == 0)
        {
            save_all_to_nvs();
        }
        else if(strcmp(cmd, "clear_nvs") == 0)
        {
            clear_nvs_data();
        }
        else if(strcmp(cmd, "walk") == 0 && cnt >= 2)
        {
            if(strcmp(param1, "start") == 0) {
                if(walk_running) ESP_LOGW(TAG, "已经在行走中");
                else {
                    walk_running = true;
                    xTaskNotifyGive(walk_task_handle);
                }
            }
            else if(strcmp(param1, "stop") == 0) {
                walk_running = false;
                xTaskNotifyGive(walk_task_handle);
            }
        }
        else if(strcmp(cmd, "set_step") == 0 && cnt >= 4)
        {
            step_length = atoi(param1);
            step_height = atoi(param2);
            shift_amount = atoi(param3);
            ESP_LOGI(TAG, "步态参数已更新: 步幅=%d 抬腿=%d 侧移=%d",
                    (int)step_length, (int)step_height, (int)shift_amount);
        }
        else if(strcmp(cmd, "set_speed") == 0 && cnt >= 2)
        {
            speed_level = atoi(param1);
            update_speed_from_level();
            ESP_LOGI(TAG, "速度等级已设为%d级：单步间隔%dms",
                    (int)speed_level, step_delay);
        }
        else if(strcmp(cmd, "set_deadzone") == 0 && cnt >= 2)
        {
            dead_zone = atoi(param1);
            if(dead_zone < 0) dead_zone = 0;
            if(dead_zone > 5) dead_zone = 5;
            ESP_LOGI(TAG, "角度死区已设为%d度", (int)dead_zone);
        }
        else if(strcmp(cmd, "set_stepinc") == 0 && cnt >= 2)
        {
            step_inc = atoi(param1);
            if(step_inc < 1) step_inc = 1;
            if(step_inc > 10) step_inc = 10;
            ESP_LOGI(TAG, "单步角度增量已设为%d度，数值越小动作越平滑", (int)step_inc);
        }
        else
        {
            ESP_LOGW(TAG, "未知指令，输入 help 查看用法");
        }
    }
}

// ================= 主函数 =================
void app_main(void)
{
    ESP_LOGI(TAG, "===== 超细平滑双足步态系统（修复I2C冲突+步态逻辑） =====");
    ESP_LOGI(TAG, "SDA=GPIO%d  SCL=GPIO%d", I2C_SDA_PIN, I2C_SCL_PIN);

    // 创建I2C互斥锁，解决多任务总线争抢
    i2c_mutex = xSemaphoreCreateMutex();

    // IO初始化
    gpio_config_t io_cfg = {
        .pin_bit_mask = (1ULL << I2C_SDA_PIN) | (1ULL << I2C_SCL_PIN),
        .mode = GPIO_MODE_INPUT_OUTPUT_OD,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_cfg);
    gpio_set_level(I2C_SDA_PIN, 1);
    gpio_set_level(I2C_SCL_PIN, 1);

    // NVS初始化
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    load_all_from_nvs();

    // 上电复位舵机至基准站姿
    ESP_LOGI(TAG, "--- 上电初始化站姿 ---");
    if(lu9685_reset(BOARD0_ADDR) == 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
        lu9685_set_all(BOARD0_ADDR, pose_board0);
        ESP_LOGI(TAG, "0号板(左腿)初始化完成");
    } else ESP_LOGE(TAG, "0号板无响应");

    vTaskDelay(pdMS_TO_TICKS(20));

    if(lu9685_reset(BOARD1_ADDR) == 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
        lu9685_set_all(BOARD1_ADDR, pose_board1);
        ESP_LOGI(TAG, "1号板(右腿)初始化完成");
    } else ESP_LOGE(TAG, "1号板无响应");

    // 创建步态任务
    xTaskCreate(walk_task, "walk_task", 4096, NULL, 4, &walk_task_handle);

    // 串口初始化
    uart_driver_install(UART_PORT, UART_BUF_SIZE * 2, 0, 0, NULL, 0);
    xTaskCreate(uart_cmd_task, "uart_cmd", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "系统就绪：I2C互斥锁已启用，步态循环无累积偏移");
}