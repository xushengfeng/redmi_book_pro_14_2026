# IoT 模组（IOTD0000）研究笔记

> 领域：IoT 模组（网络、远程唤醒、WMI 事件、mailbox）。性能/充电/麦克风/风扇/温度属于
> MIFS/EC，独立说明见 [mifs.md](mifs.md)。

> 当前实现以 `README.md` 为准。本文是研究日志，早期章节里的“不可行/未实现”是当时的
> 阶段结论；5.x 已实现 raw event、MIFS 性能/充电/麦克风、固件版本读取和真实关机/唤醒。

## 1. ACPI / 固件

```
\_SB.PC00.IOTD
    _HID = "IOTD0000"
    _STA = 0x0F
```

它没有 `_CRS`，所以 Linux 只创建一个 `platform` 设备，不会出现 USB/PCI 子设备。真正的通信在 EC 和 MIFS WMI 后面。

MIFS 设备：

```
\_SB.PC00.WMID
    _HID = PNP0C14
    _UID = "MIFS"
    方法 WMAA (Arg0, Arg1, Arg2)
```

### 1.1 WMAA 输入缓冲

Windows WMI 调用 `MiCommonInterface.MiInterface` 时，`InData` 是 32 字节：

```
offset 0: 00
offset 1: FA = GET / FB = SET
offset 2: 00
offset 3: function
offset 4: arg
offset 5: 00
offset 6: arg2
offset 7: 00
offset 8..31: 00
```

对应 AML 里的两个 16 位字段和一个 32 位字段：

```
FUN1 = little-endian word at offset 0   -> 0xFA00 / 0xFB00
FUN2 = little-endian word at offset 2   -> 0x0C00（功能组）
FUN3 = little-endian word at offset 4   -> 子功能
FUN4 = little-endian dword at offset 6  -> 值
```

`WMAA` 的第二个参数必须是 `1`（方法编号）。`Arg2` 是上面这个 32 字节 buffer。

### 1.2 和 IoT 有关的子功能（SSDT25.dsl，0x0C00 组）

| FUN3 | 方向 | AML 行为 | 含义 |
| --- | --- | --- | --- |
| `0x02` | GET/SET | 读写 EC `LOTS` | 模组“存在/在线”标志；只读探测用 |
| `0x03` | GET/SET | 读写 EC `RMTS`；变化时置 `APPC=1` | **模组电源**（0=关，1=开） |
| `0x04` | GET/SET | 读写 EC `PL1F`，并写 `OD08`、Notify IETM | 平台功耗限制相关，不属于 IoT |
| `0x05` | GET/SET | 读写 EC `EPOF`，并写 `OD09`、Notify IETM | 平台功耗限制相关，不属于 IoT |
| `0x06` | GET | 通过 `PMBC=0x22` mailbox 读 `PMBD` | 绑定/状态类信息（XiControl 记录为“绑定/remote-wake 激活”） |

GET 返回的 32 字节里：

```
OUT[0..1] = SGER 状态（0x8000 = 支持；0xE000 = 不支持）
OUT[2..3] = FUTR 功能组回显
OUT[4..5] = FRD0
OUT[6..9] = FRD1（值）
```

`0x0C/0x03` 的 SET 是真正会动硬件的：它改 EC 的 `RMTS`，官方 `IoTService.exe` 就是用这条路径开关模组。

## 2. 官方 Windows 栈（公开逆向资料）

来自 XiControl 项目的文档（`docs/11-iot-remote-wake.md`）：

- 驱动：`IoTDriver.sys`（KMDF），服务：`IoTService.exe`（`IoTSvc`，自启动）
- 驱动 ↔ 模组：RAM mailbox（`RamDevice::Read/Write`，cmd → ack → ret）
- 服务 ↔ 应用：命名管道 `\\.\pipe\LOCAL\IoTService_IPC_Broker`，JSON 消息
- 模型：`xiaomi.laptop.p52`（TM2424）
- 模组有自己的 2.4 GHz Wi-Fi，关机时仍在线，经小米云收 miIO 指令后唤醒主机
- 本地 miIO：UDP `54321`，magic `21 31`，32 字节 hello；模组返回 DID，但 token 固定为 `ff`×16
- 真正的唤醒令牌是 128-bit miIO token，只从中国区小米云或模组 flash 获得
- 配网：30 分钟窗口，`IPC_WriteWiFiItem(ssid, pass, connectNow=1)`
- 绑定：`IPC_GetBindStatus` / `IPC_SetDeviceStatus`

关键点：**本地可以把模组拉进 Wi-Fi，但无法拿到 token；远程唤醒只能走中国区小米云 + 米家绑定。** 这不是普通 WoL。

## 3. Linux 驱动现状

本机同时加载了：

```
redmi-wmi          -> WMI event GUID 46C93E13-...
bitland-mifs-wmi   -> WMI method GUID B60BFB48-...
```

`bitland-mifs-wmi` 暴露 `platform_profile`、`hwmon`、`gpu_mode`、`kb_mode`、`fan_boost` 等，但没有 raw MIFS 接口，也没有 IoT 属性。

`platform_profile` 在本机报：

```
platform_profile: Failed to get profile for handler bitland-mifs-wmi
```

这是 MIFS v2 固件的已知兼容问题（性能模式返回的是 EC `QFAN` 原始码 2/3/4/9/10，而不是 v1 的 0..3）。

## 4. 如果想做实验：raw MIFS

### 4.1 用 acpi_call（临时、用户态）

先加载 `acpi_call`（Arch 的 `extra/acpi_call` 或 `acpi_call-dkms`），然后：

```bash
# 只读：GET 0x0C/0x02（LOTS）
echo '\_SB.PC00.WMID.WMAA 0 1 b00FA000C0200000000000000000000000000000000000000000000000000000000' > /proc/acpi/call
cat /proc/acpi/call

# 只读：GET 0x0C/0x03（RMTS，模组电源）
echo '\_SB.PC00.WMID.WMAA 0 1 b00FA000C0300000000000000000000000000000000000000000000000000000000' > /proc/acpi/call
cat /proc/acpi/call

# 只读：GET 0x0C/0x06（mailbox）
echo '\_SB.PC00.WMID.WMAA 0 1 b00FA000C0600000000000000000000000000000000000000000000000000000000' > /proc/acpi/call
cat /proc/acpi/call
```

注意：

- `acpi_call` 的 buffer 参数是 `b` + 十六进制；上面每个 buffer 是 32 字节（64 个 hex 字符）。
- 每次 `cat` 之后 `acpi_call` 会把结果重置成 `not called`，所以一次调用只读一次。
- `0x0C/0x03` 的 **SET** 才是写电源：

```bash
# 关模组：SET 0x0C/0x03 = 0
echo '\_SB.PC00.WMID.WMAA 0 1 b00FB000C0300000000000000000000000000000000000000000000000000000000' > /proc/acpi/call
cat /proc/acpi/call

# 开模组：SET 0x0C/0x03 = 1
echo '\_SB.PC00.WMID.WMAA 0 1 b00FB000C0301000000000000000000000000000000000000000000000000000000' > /proc/acpi/call
cat /proc/acpi/call
```

**不要在没有备份/心理准备的情况下直接跑 SET。** 本机 EC 驱动因为 BIOS 的 `ECON` 缺失没有正常绑定，`WMAA` 里的 ECRD/ECWT 可能返回错误；官方 `IoTService` 不在 Linux 上，关掉后没有东西会自动把它打开。

### 4.2 更干净的长期方案

给 `bitland-mifs-wmi` 增加一个 `iot_power` sysfs 属性，内部用驱动已有的 `wmidev_invoke_method()` 发 `0xFB/0x0C/0x03`。这样不需要 `acpi_call`，也能复用驱动的 WMI 句柄和错误处理。

驱动里已有：

```c
static int bitland_mifs_call(struct bitland_mifs_wmi_data *data,
                             struct bitland_mifs_input *in,
                             struct bitland_mifs_output *out)
{
    ...
    ret = wmidev_invoke_method(data->wdev, 0, 1,
                               &in_buf, &out_buf, sizeof(*output));
    ...
}
```

新增属性只需要构造：

```c
struct bitland_mifs_input in = {
    .operation = WMI_METHOD_SET,   /* 0xfb */
    .function  = 0x0c,
    .arg       = 0x03,
    .arg2      = value,            /* 0/1 */
};
```

然后在 probe 里 `device_create_file()`。这是实验性 patch，不建议在没确认 EC 状态前默认启用。

## 5. 本机实测（2026-10-27）

加载 `acpi_call` 后，用 `mifs_iot.py` 或 `acpi_get.py` 调用 `WMAA`：

```
0x0C/02 present/LOTS = 1  [ok]
0x0C/03 power/RMTS   = 0  [ok]   # 通电前
0x0C/06 mailbox      = 0  [ok]
```

执行 `SET 0x0C/0x03 = 1` 后：

```
0x0C/03 power/RMTS   = 1  [ok]
0x0C/02 present/LOTS = 1  [ok]
0x0C/06 mailbox      = 0  [ok]
```

说明：

- 模组确实存在，且可以被 MIFS 单独通电。
- 通电后没有出现新的 Wi-Fi SSID，也没有看到新的 BLE 广播；模组没有配置过 Wi-Fi，
  基本可以确定它在等小米私有的 Wi-Fi 配网广播/小米电脑管家的触发。
- `0x0C/06` 仍是 0，符合“未绑定/未配网”的状态。
- **米家 App 能扫描到这台笔记本**，说明模组在未配网时仍会做可发现广播；
  但现场没有 2.4 GHz Wi-Fi，无法完成配网，所以只验证到“发现”这一步。
- 测试结束后执行 `mifs_iot.py off --yes`，`0x0C/03` 已回到 0，模组断电；
  `acpi_call` 也已卸载。

### 5.1 米家绑定成功（2026-10-27 晚）

现场没有 2.4 GHz 路由器，用手机开 2.4 GHz 热点后：

1. 米家 → 添加设备 → 扫描到这台笔记本；
2. 绑定并选择手机热点作为 Wi-Fi；
3. 绑定成功，米家设备页可以直接看到：
   - CPU 温度
   - 电池电量
   - 充电状态

这说明模组通过 EC 直接读取主机状态，**不依赖 Linux 上的任何服务**；即使系统里没有
`IoTService`，米家仍能看到这些数据。

同时观察到：`0x0C/0x06` 仍然是 0，所以这个 MIFS 标志不是“已绑定”的可靠指示；
它可能需要 Windows 的 `IoTService` 去更新，或者表示的是另一个状态。判断是否绑定，
以米家设备页/模组是否在线为准。

### 5.2 米家“关机”没有反应（2026-10-27）

在米家设备页按“关机”后 App 一直转圈，主机没有任何反应。检查结果：

- `journalctl -k` 最近 20 分钟没有 ACPI/EC/WMI/电源键事件；
- `journalctl` 没有 logind 关机、session 结束等记录；
- `0x0C/0x03` 仍是 1（模组在线），`0x0C/0x06` 仍是 0（没有主机侧 mailbox 消息）。

结论：这条“关机”指令走的是 Windows 侧的 `IoTService` / 远程控制代理，不是模组直接拉
EC 电源键。Linux 上没有这个代理，所以 App 只能等到超时；模组本身仍然能通过 EC 读温度、
电池，所以状态页是正常的。

这也意味着：

- **状态遥测**：模组独立完成，Linux 不需要做任何事。
- **优雅关机/重启**：需要主机侧有代理；Linux 目前没有。要做的话，需要逆向
  `IoTDriver.sys` 的 RAM mailbox，自己写一个守护进程，收到命令后执行
  `systemctl poweroff` 并回 ack。
- **从 S5 唤醒**：可能是模组直接触发 EC/PMIC 的硬件路径，不依赖 OS 代理，值得单独测试。

### 5.3 S5 远程唤醒成功（2026-10-27）

真机测试：

- 16:53:49 正常关机（S5）
- 16:54:23 新的一次启动，BIOS/内核从头初始化

也就是说，**模组在 S5 下仍然在线，并且成功把笔记本从完全关机状态唤醒**。这条路径不依赖
Linux 或 Windows 里的代理，是模组 → EC/PMIC 的硬件动作。

米家 App 在唤醒后仍显示“正在唤醒笔记本”，这是因为它还在等电脑端代理回 ack/上报在线；
Linux 没有这个代理，所以状态文案会滞后或超时。**硬件已经醒了，App 文案不等于失败。**
可以下拉刷新/重进设备页，或等它超时后看温度/电池是否恢复。

另一个现实限制：模组连的是手机 2.4 GHz 热点。笔记本放在哪里，那个热点就必须覆盖到哪里；
手机带走后模组会离线，远程唤醒也就失效。要稳定使用，需要在笔记本所在位置放一个常开的
2.4 GHz 路由器/AP。

“关机/重启”仍然需要主机侧代理；目前只有“从 S5 唤醒”这一条是 Linux 下可用的。

`mifs_iot.py` 的用法：

```bash
sudo modprobe acpi_call
sudo python3 mifs_iot.py status
sudo python3 mifs_iot.py on --yes
sudo python3 mifs_iot.py off --yes
sudo rmmod acpi_call
```

### 5.4 主机状态 ack 逆向（2026-10-27）

官方 `IoTService.exe`（25.0.0.9）里找到了状态上报协议。它通过驱动把数据写到
`/dev/mem` 可映射的物理地址：

```
0xFE0B0AB8  8 B     门铃/控制
0xFE0B0E00  0x100 B 命令/事件数据
0xFE0B0F00  0x80 B  状态/ack 邮箱
```

`ReportLaptopStatus(status)` 的写序列（从 `IoTService.exe` 反汇编）：

```
0xFE0B0F01 <- status, 0x01, 0x01, <4 字节值>
0xFE0B0F00 <- 0x55
0xFE0B0F00 <- 0x55, status, 0x01, 0x02
0xFE0B0F00 <- 0x55, status, 0x01, 0x02, 0x55, status, 0x01, 0x03
```

状态值：

| 值 | 含义 |
| --- | --- |
| `0x10` | `IOT_WIN_READY`（系统已启动/恢复） |
| `0x0F` | `IOT_SHUTING`（正在关机） |
| `0x0E` | `IOT_SUSPENDING`（正在睡眠） |

`iot_ack.py` 用 `/dev/mem` 复现了这套写序列。实测发 `0x10` 后：

```
before: f00=00 00 00 00 ...
detail: f00=00 10 01 01 ...
55:     f00=55 10 01 02 ...   ← 模块把子命令从 01 改成 02
0102:   f00=55 10 01 03 01 ... ← 模块继续推进
both:   f00=11 10 01 03 ...   ← 模块回 ack 0x11
2 秒后: f00=00 10 01 03 ...   ← 模块清掉 ack
```

说明模块确实在处理这个 mailbox，`0x11` 是它的应答。米家 App 的“正在唤醒”是否因此
清除，还需要在手机上确认；如果不清，可能还缺 7 字节块里的那个 4 字节值（服务里是运行时
从模组读出的值，可能是 DID/时间戳），或者还需要在 WMI 事件里回复。

**关机/睡眠**：官方服务是收到模组的 `[shutdown]`/`[sleep]` 事件后执行
`InitiateSystemShutdown`/`SetSuspendState`，然后上报 `IOT_SHUTING`/`IOT_SUSPENDING`。
Linux 要做同样的事，需要：

1. 定期读 `0xFE0B0E00`（或实现 WMI 事件）判断模组是否发来 shutdown/sleep；
2. 执行 `systemctl poweroff` / `systemctl suspend`；
3. 调 `iot_ack.py 0x0f` / `0x0e` 上报状态。

这部分还没有实现成守护进程。

### 5.5 自动上报 WIN_READY（成功）

实测 `iot_ack.py 0x10` 后，米家不再卡在“正在唤醒笔记本”，状态恢复正常。说明：

- `IOT_WIN_READY = 0x10` 就是 App 等的 ack；
- 7 字节状态块里的 4 字节值用 0 也能被模组接受（至少这块固件是这样）；
- 模组会先回 `0x11`，随后自动清掉门铃字节。

已提供安装脚本：

```bash
sudo ./install-iot-ready.sh
```

它会安装两个 systemd 单元：

- `iot-ready.service`：开机后发一次 `0x10`；
- `iot-resume.service`：从 suspend 恢复后发一次 `0x10`。

查看状态：

```bash
systemctl status iot-ready.service
journalctl -u iot-ready.service -b
```

`iot_ack.py` 也可以手动发其他状态：

```bash
sudo python3 iot_ack.py 0x10   # WIN_READY
sudo python3 iot_ack.py 0x0e   # SUSPENDING
sudo python3 iot_ack.py 0x0f   # SHUTING
```

关机/睡眠的“自动上报 + 真正执行关机/睡眠”还需要一个守护进程；目前没有默认安装，避免在
没有确认模组事件格式前误触发关机。

### 5.6 Linux 侧代码分层与事件源（2026-10-27）

代码按四层拆开，关机策略不再和 ack/解析混在一起：

| 文件 | 职责 |
| --- | --- |
| `iot_mailbox.py` | 只做 `/dev/mem` 映射、读/写三个区域、快照 |
| `iot_status.py` | 只做 `WIN_READY/SHUTING/SUSPENDING` 的 ack 序列 |
| `iot_event.py` | 只做事件帧解析：`[01, EventFunc, EventValue]` |
| `iot_agent.py` | 只做策略/执行：dry-run 默认，真实关机需 `--execute --allow-shutdown` |

事件映射（来自 `IoTService.exe` 的 IoT 驱动事件解析）：

```
01 2A 01 -> shutdown
01 2A 02 -> sleep
01 2A 03 -> wake
01 29 xx -> status
01 2B xx -> state
```

注意：这是**事件帧内容**，不是传输层。模组重启（`RMTS` 0→1）过程中，
`0xFE0B0E00` / `0xFE0B0F00` 没有出现这个帧。说明 shutdown/sleep 请求很可能走
`IoTDriver.sys` 的 WMI 事件，而不是 RAM mailbox。要抓它，需要：

1. 从 `IoTDriver.sys` 里找到 WMI event GUID / MOF 结构；
2. 在 Linux 侧做一个对应的 WMI/事件监听，或让模组在线时按“关机”并轮询 mailbox
   确认是否真的不经过 mailbox。

单测：`python3 -m unittest discover -s tests -v`（14 项通过）。

`iot_agent.py` 默认 dry-run：

```bash
# 只观察/打印，不做任何动作
sudo python3 iot_agent.py --source mailbox --dump

# 模拟事件
python3 iot_agent.py --simulate shutdown

# 真正允许关机/睡眠（确认后再用）
sudo python3 iot_agent.py --execute --allow-shutdown --allow-sleep
```

### 5.7 抓到 shutdown WMI payload（2026-10-27）

开着 `redmi-wmi` 的 dynamic debug 后，在米家按“关机”，内核日志出现：

```
redmi-wmi 46C93E13-...: Unknown WMI event with payload 76289
```

`76289 = 0x00012A01`，小端字节就是：

```
01 2A 01   -> shutdown
```

对应关系：

| 字节 | 十进制 payload | 含义 |
| --- | --- | --- |
| `01 2A 01` | `76289` | shutdown |
| `01 2A 02` | `141825` | sleep |
| `01 2A 03` | `207361` | wake |

所以事件**不走** `0xFE0B0E00` mailbox，而是走 MIFS `HID_EVENT20` 的 WMI payload。
`redmi-wmi` 把它当成未知按键，默认只 `dev_dbg` 一下；打开 dynamic debug 才看得到。

代码里新增了 `JournalEventSource`，把这条 debug 日志桥接成正式事件：

```bash
echo 'module redmi_wmi +p' > /sys/kernel/debug/dynamic_debug/control
sudo python3 iot_agent.py --source journal --dry-run
```

实测用历史日志回放：

```
$ python3 iot_agent.py --source journal --since '-30 min' --once
shutdown event
  shutdown not allowed (need --allow-shutdown)
```

说明“日志 → payload → Event(shutdown) → 策略”这条链已经通了。真正关机仍然要显式：

```bash
sudo python3 iot_agent.py --source journal --execute --allow-shutdown
```

这会把 `IOT_SHUTING` ack 发给模组，然后执行 `systemctl poweroff`。

**长期方案**：给 `redmi-wmi` 加一个 raw event 接口（misc/input 自定义 key 或 debugfs），
就不需要依赖 `dev_dbg` 文本了。`journal` 源是当前可用的桥接；`mailbox` 源保留，
但它看不到 shutdown/sleep 事件。

### 5.8 真实关机/唤醒闭环（已通过）

2026-10-27 18:33 左右做了真实测试：

```
18:33:39  上一次 boot 结束（systemctl poweroff）
18:34:13  新 boot
18:34:15  iot-ready.service 发送 IOT_WIN_READY
```

agent 日志：

```
shutdown event
  ack -> 0x0e
  running: systemctl poweroff
```

结论：

- 米家“关机” → `redmi-wmi` payload `76289` → agent → `IOT_SHUTING` → `systemctl poweroff`：**通过**
- S5 后米家“开机” → 硬件唤醒：**通过**
- 开机后 `iot-ready.service` 自动 ack：**通过**
- `iot-agent.service` 已启用（`--execute --allow-shutdown --allow-sleep`），
  `iot-ready.service` / `iot-resume.service` 已启用

睡眠（`01 2A 02`）的解析/动作已实现，但没有单独做真机睡眠测试。

### 5.9 raw event / MIFS / 固件版本（2026-10-27）

#### raw event

给 `redmi-wmi` 增加了 3 个 keymap：

```
0x00012A01 -> KEY_MACRO1   shutdown
0x00012A02 -> KEY_MACRO2   sleep
0x00012A03 -> KEY_MACRO3   wake
```

patch 在 `patches/redmi-wmi-iot-keys.patch`，`build-redmi-wmi.sh` 可重建并安装到
`/lib/modules/$(uname -r)/updates/`。`ensure-redmi-event.sh` 确保 WMI event 设备绑定到
`redmi-wmi` 而不是 `bitland-mifs-wmi`。

验证：

```
EV_KEY KEY_MACRO1 value=1
```

`iot-agent.service` 已改为 `--source input`，不再依赖 `journalctl`/dynamic debug。
journal 源仍保留作为 fallback。

#### MIFS 三个功能

`mifs.py` 用 WMAA 原始命令：

```bash
sudo python3 mifs.py perf get          # 当前 9 (extreme)
sudo python3 mifs.py perf set 2        # quiet，已验证；再设 9 恢复
sudo python3 mifs.py charge get        # 100%
sudo python3 mifs.py charge set 80     # 已验证；再设 100 恢复
sudo python3 mifs.py mic get           # raw=1 unmuted
sudo python3 mifs.py mic mute          # raw=0
sudo python3 mifs.py mic unmute        # raw=1
```

实测：

```
perf 9 (extreme) -> set 2 -> 2 (quiet) -> set 9 -> 9 (extreme)
charge code=0 limit=100% -> set 80 -> code=4 limit=80% -> set 100 -> code=0
mic raw=1 unmuted -> mute -> raw=0 muted -> unmute -> raw=1
```

注意：性能模式的 label 在不同 MIFS 版本上有差异（2/3/4/9/10）；原始值可靠。

#### 固件版本

`iot_status.py --firmware` 走与 Windows `IoTService::GetFwVersion` 相同的 mailbox 序列
（status `0x0A`，读取 `0xFE0B0F08` 的长度前缀字符串）：

```bash
sudo python3 iot_status.py --firmware
# 1.0.3_0010
```

当前模组版本是 **1.0.3_0010**，米家提示可升级 **1.0.3_0030**。Linux 目前只能**读**版本，
不能升级；升级走米家/Windows PC Manager/云端。

### 5.10 软复位/刷新

`IoTService.exe` 的 IPC 类型 `0x6A`（`ResetDevice`）使用与状态上报相同的 mailbox
序列，status 为：

```
0x03 = IOT_RESET_DEVICE
```

`iot_status.py --reset` 复现这条序列：

```bash
sudo python3 iot_status.py --reset
```

它会软重启模组固件（不切 EC 电源），用于 Wi-Fi 断线后迟迟不重连的情况；代价是模组会
短暂离线。当前网络已在线时不要随手发。

官方 IPC 和底层 status 的对应关系（十进制 IPC 类型）：

| IPC | 功能 | mailbox status | 状态 |
| --- | --- | --- | --- |
| `110` (`0x6E`) | `ReadWiFiStatus` | `0x07` | ✅ `--wifi-status` |
| `113` (`0x71`) | `GetModel` | `0x0B` | ✅ `--model` |
| `114` (`0x72`) | `ConnectWiFi` | `0x0C` | ⚠️ `--reconnect`，断网场景待测 |
| `106` (`0x6A`) | `ResetDevice` | `0x03` | ✅ `--reset` |

`ReadWiFiStatus` 的回复：

```
header: 55 07 01 03 05 07 01 03
                          ^^ status code (5 = ConnectedToCloud)
reply:  24 01 08 3e 16 "root 的 Xiaomi 17 Max" ...
                       ^^ SSID length
```

实测：

```
$ sudo python3 iot_status.py --wifi-status
wifi status: 5 (ConnectedToCloud) ssid='root 的 Xiaomi 17 Max'
$ sudo python3 iot_status.py --model
xiaomi.laptop.n57sr
```

`--reconnect` 发 `0x0C`；当前在线时发送不会掉线，但还没有在“真的断网”场景下验证它能否
加速重连。如果不行，退回到 `--reset` 或 `mifs_iot.py off/on`。

## 6. 结论

- Linux 没有现成可用的 IoT 远程唤醒驱动。
- 只读状态（`0x0C/0x02`、`0x0C/0x03`、`0x0C/0x06`）理论上可通过 raw MIFS 做，但本机 EC 状态不正常，需要先确认。
- 电源开关（`0x0C/0x03`）是唯一有实际意义的本地控制；配网、绑定、远程唤醒仍然绕不开小米云/OEM 服务。
- 最实用的方案：Windows 下完成一次配网/绑定，之后 Linux 侧只负责“被唤醒后自己上线”。

## 7. 参考

- XiControl 项目：<https://github.com/Oksion/XiControl>
  - `docs/01-wmi-protocol.md`
  - `docs/11-iot-remote-wake.md`
- 本机 DSDT：`\_SB.PC00.WMID`、`\_SB.PC00.IOTD`
- Linux `drivers/platform/x86/bitland-mifs-wmi.c`
- Linux `drivers/platform/x86/redmi-wmi.c`
