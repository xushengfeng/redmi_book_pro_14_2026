# Redmi Book Pro 14 2026（TM2426）Linux NFC / MIFS / IoT

Linux 下这台机器的 NFC 标签与小米 IoT 模组的实现、研究记录和工具集。

## 状态

| 模块           | 实际硬件                                                 | Linux 状态                                                                        |
| -------------- | -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| NFC            | NXP NTAG 5 boost（NTA5332）I2C 标签                      | ✅ 读写 NDEF、vCard、Wi-Fi、raw、备份恢复                                         |
| IoT / 远程唤醒 | 独立小米 always-on 2.4 GHz Wi-Fi 模组（ACPI `IOTD0000`） | ✅ S5 唤醒、远程关机、状态 ack、温度/电量遥测；配网/绑定/固件升级仍走米家/Windows |
| MIFS           | Intel PTL + Bitland EC                                   | ✅ 性能模式、充电阈值、麦克风静音、风扇/温度、模组电源                            |

> 重要：笔记本的 NFC 是**标签**，不是读卡器。它能让手机“碰一碰”读到 Linux 写入的 NDEF，但**不能**读门禁卡、银行卡、公交卡，也不能克隆 UID。

## 三个领域

| 领域 | 硬件/接口                    | 主要工具                                        | 说明                                |
| ---- | ---------------------------- | ----------------------------------------------- | ----------------------------------- |
| NFC  | NTAG5，I2C `/dev/i2c-1` 0x54 | `ntag5.py`                                      | 标签读写；不是读卡器                |
| MIFS | ACPI `\_SB.PC00.WMID` + EC   | `mifs.py`、`mifs_iot.py`                        | 性能/充电/麦克风/风扇/温度/IoT 电源 |
| IoT  | `IOTD0000` + WMI/mailbox     | `iot_status.py`、`iot_event.py`、`iot_agent.py` | 远程唤醒/关机/ack/遥测              |

边界：

- NFC 完全不经过 EC/网络，只走 I2C。
- MIFS 是 host 固件/EC；关掉 IoT 模组后性能/充电/麦克风/风扇/温度仍然可用。
- IoT 模组负责远程唤醒、关机/睡眠事件和把 EC 遥测转发米家，不控制性能/充电阈值。
- 详细分领域文档：[docs/README.md](docs/README.md)、[docs/nfc.md](docs/nfc.md)、
  [docs/mifs.md](docs/mifs.md)、[docs/iot.md](docs/iot.md)。

## 硬件识别

### NFC

```
\_SB.PC00.I2C5.NFC   HID = NTAG5332
I2C: /dev/i2c-1      7-bit address 0x54
```

- NXP NTAG 5 boost（NTA5332）
- 2048 B 用户 EEPROM，NFC Forum Type 5（ISO/IEC 15693）
- 本机 CC：`E1 40 80 09`，NDEF 容量 1024 B
- 原始 NDEF 是小米 `com.xiaomi.mi_connect_service:externaltype`，含 BT MAC 和
  `TAG_DISCOVERED / MIRROR`

### MIFS

```
\_SB.PC00.WMID   HID = PNP0C14, _UID = "MIFS"
WMAA: GET/SET 0x08 perf, 0x10/0x02 charge, 0x0A/0x05 mic, 0x0C/0x03 IoT power
风扇 tach: EC window 0xFE0B0300 + 0x69/0x6B
温度: 标准 hwmon（coretemp/acpitz/nvme/iwlwifi）
```

详见 [docs/mifs.md](docs/mifs.md)。

### IoT

```
\_SB.PC00.IOTD   HID = IOTD0000
```

- 模组：`xiaomi.laptop.p52` 一类独立米家设备，自带 2.4 GHz Wi-Fi、DID、固件
- 官方 Windows：`IoTDriver.sys` + `IoTService.exe`（`IoTSvc`）
- Linux 侧通过：
  - `/dev/mem` 映射的 mailbox（`0xFE0B0AB8` / `0xFE0B0E00` / `0xFE0B0F00`）
  - MIFS `WMAA`（ACPI，经 `acpi_call`）
  - `redmi-wmi` 的 raw keymap（WMI `HID_EVENT20`）
- 当前模组固件：`1.0.3_0010`（米家提示可升级到 `1.0.3_0030`，Linux 只读，不升级）

## 快速开始

依赖：

- Python 3（全部工具只用标准库）
- `i2c-dev` + `i2c` 组（NFC）
- `acpi_call`（MIFS）
- 内核头文件、`curl`、`patch`、`make`（重建 `redmi-wmi`）
- systemd

```bash
# NFC 权限
sudo usermod -aG i2c "$USER"     # 之后重新登录

# MIFS（Arch）
sudo pacman -S acpi_call         # 或 acpi_call-dkms + linux-headers
sudo modprobe acpi_call

# raw event：编译并安装带 IoT keymap 的 redmi-wmi
sudo ./build-redmi-wmi.sh
sudo rmmod redmi_wmi && sudo modprobe redmi_wmi   # 或重启

# 开机/恢复 ack
sudo ./install-iot-ready.sh

# 关机/睡眠/唤醒 agent（会执行 systemctl poweroff/suspend）
sudo ./install-iot-agent.sh
```

## NFC

### 使用

```bash
python3 ntag5.py info                 # CC、容量、NDEF 摘要
python3 ntag5.py read                 # 解析 NDEF；小米记录会展开
python3 ntag5.py dump -o backup.bin   # 备份 2048 B 用户区
python3 ntag5.py scratch              # 0x7e0 写/读/还原自检

python3 ntag5.py write-text 'hello from Linux'
python3 ntag5.py write-uri  'https://example.com'
python3 ntag5.py write-vcard --name 'Redmi Book' --phone '+8613800000000'
python3 ntag5.py write-wifi --ssid MyWiFi --password secret123
python3 ntag5.py write-raw  'D101...'
python3 ntag5.py watch --interval 1
python3 ntag5.py restore backup.bin
```

- `write-*` 只重写 block 1 开始到 TLV 结束的区域，不碰 CC（block 0）和计数器块。
- NTAG5 EEPROM 写周期约 10 ms，工具已加延时。
- 手机端可能把标签显示成只读；I2C 侧写入是另一条通道，已实测可写。
- 这台机器的原始备份是 `backup-original.bin`（**本地文件，已被 `.gitignore` 忽略**；
  重新克隆后请先用 `ntag5.py dump` 生成自己的备份），SHA-256：

```
14194c6356021c00e76b2fe956307417bcf47f41e1cd263fd017c9c61f8965ba
```

### 能做 / 不能做

- ✅ 文本、URL、vCard、Wi-Fi NDEF、MIME/外部类型、多条记录
- ✅ 解析/改写小米 `MI-NFCTAG` payload（deviceType、动作、BT MAC）
- ❌ 读卡、克隆 UID、模拟任意卡、NFC 点对点

## IoT

### 已实现的 Linux 链路

```
米家 App
  → 小米云
  → IoT 模组（2.4 GHz Wi-Fi）
  → EC / WMI
  → Linux agent
       ├─ iot-agent.service   shutdown/sleep/wake
       ├─ iot-ready.service   开机 WIN_READY ack
       └─ iot-resume.service  恢复 WIN_READY ack
```

IoT 模组本身不控制性能模式/充电阈值；这些是 host 侧 MIFS/EC 的功能。模组只读 EC
遥测并负责远程唤醒/状态上报。

事件在 MIFS `HID_EVENT20` 里表现为 32-bit payload：

| payload（十进制） | 字节       | 含义     |
| ----------------- | ---------- | -------- |
| `76289`           | `01 2A 01` | shutdown |
| `141825`          | `01 2A 02` | sleep    |
| `207361`          | `01 2A 03` | wake     |

`redmi-wmi` 已打 patch，把这三个 payload 映射成 `KEY_MACRO1/2/3`：

- `patches/redmi-wmi-iot-keys.patch`
- `build-redmi-wmi.sh`：下载/打 patch/编译/安装到
  `/lib/modules/$(uname -r)/updates/redmi-wmi.ko`
- `ensure-redmi-event.sh`：确保 WMI event 设备绑定到 `redmi-wmi`
- `iot_agent.py --source input` 直接读 `Redmibook WMI keys`，不依赖 journal/dynamic debug

### 状态 ack 与固件版本

mailbox：

```
0xFE0B0AB8   8 B      门铃/控制
0xFE0B0E00   0x100 B  命令/事件
0xFE0B0F00   0x80 B   状态/ack
0xFE0B0F08            版本/返回字符串
```

状态值：

| 值     | 含义             |
| ------ | ---------------- |
| `0x10` | `IOT_WIN_READY`  |
| `0x0E` | `IOT_SHUTING`    |
| `0x0F` | `IOT_SUSPENDING` |
| `0x0A` | `IOT_FW_VERSION` |

```bash
sudo python3 iot_status.py 0x10          # WIN_READY
sudo python3 iot_status.py 0x0e          # SHUTING
sudo python3 iot_status.py 0x0f          # SUSPENDING
sudo python3 iot_status.py --firmware    # 1.0.3_0010
```

`iot_status.py --firmware` 走官方 `GetFwVersion` 同一套序列，读
`0xFE0B0F08` 的长度前缀字符串。

### 模组电源

```bash
sudo python3 mifs_iot.py status
sudo python3 mifs_iot.py on --yes
sudo python3 mifs_iot.py off --yes
```

MIFS `0x0C/0x03` 控制 `RMTS`；`0x0C/0x02` 是存在标志；`0x0C/0x06` 在绑定后仍为 0，
不能作为绑定判据。

### MIFS 其他功能

> 性能模式、充电阈值、麦克风静音属于 **MIFS/EC**（ACPI `\_SB.PC00.WMID`），
> 不是 IoT 模组（`IOTD0000`）。关掉 IoT 模组后这些功能仍然可用；IoT 模组只负责
> 远程唤醒、状态 ack 和把 EC 温度/电池数据转发到米家。


```bash
sudo python3 mifs.py perf get
sudo python3 mifs.py perf set 2        # 2=quiet 3=balanced 4=speed 9/10=extreme
sudo python3 mifs.py charge get
sudo python3 mifs.py charge set 80     # 40/50/60/70/80/100
sudo python3 mifs.py mic get
sudo python3 mifs.py mic mute
sudo python3 mifs.py mic unmute
sudo python3 mifs.py fan        # CPU/GPU RPM，从 EC window 读
python3 mifs.py temp            # 标准 hwmon 温度
```

风扇：v2 固件 `0x0D` 返回 `0xE000`，改读 EC window `+0x69`/`+0x6B`（u16 LE）。
温度：v2 固件 `0x16` 也不支持，使用标准 hwmon `coretemp`/`acpitz`/`nvme`/`iwlwifi`。

**风扇只能读，不能直接控制**：v2 下 `0x14/0x15` 也返回 `0xE000`，hwmon 没有 `pwm1`，
`fan_boost` 是 v1 接口，在这台机器上会被固件拒绝。只能通过性能模式间接影响风扇曲线；
不建议直接写 EC 寄存器。

原始值优先；不同 MIFS 版本的 label 可能不同。实测已恢复原值：
`perf 9`、`charge 100%`、`mic unmuted`。

### 支持矩阵

| 能力                | Linux                     | 说明                                          |
| ------------------- | ------------------------- | --------------------------------------------- |
| S5 远程唤醒         | ✅ 实测                   | 模组硬件触发 EC/PMIC                          |
| 远程关机            | ✅ 实测                   | 米家关机 → WMI payload → `systemctl poweroff` |
| 开机/恢复 ack       | ✅ 实测                   | `iot-ready` / `iot-resume`                    |
| 温度/电量/充电状态  | ✅ 实测                   | 模组直读 EC，不依赖 OS 服务                   |
| 模组电源/存在状态   | ✅ 实测                   | `mifs_iot.py`                                 |
| 模组固件版本        | ✅ 实测                   | `iot_status.py --firmware`                    |
| 睡眠                | ⚠️ 代码已实现，未真机验证 | `--allow-sleep` 已启用                        |
| 性能/充电/麦克风    | ✅ 实测                   | `mifs.py`                                     |
| PC 端改模组 Wi-Fi   | ❌                        | 用米家/Windows PC Manager                     |
| 绑定/解绑、固件升级 | ❌                        | 米家/云端/Windows                             |
| 本地 miIO 控制      | ❌                        | token 为 `ff`×16，只在云端/flash              |
| 远程桌面/文件管理   | ❌                        | 需要 Windows 远程控制应用                     |
| NFC 读卡/克隆       | ❌                        | 这是标签，不是读卡器                          |

### 限制说明

- **Wi-Fi 配置**：模组的 SSID/密码存在模组 flash；官方 Linux 没有 `IoTService`
  的 `IPC_WriteWiFiItem`。改 Wi-Fi 请用米家 App 或 Windows PC Manager。笔记本自己的
  Wi-Fi 当然可以用 NetworkManager 改。
- **热点依赖**：模组现在连手机 2.4 GHz 热点；热点必须一直覆盖笔记本，否则模组离线，
  远程关机/唤醒失效。稳定使用建议放一个常开的 2.4 GHz AP。
- **固件升级**：Linux 只读版本，不刷固件；`1.0.3_0030` 请走米家/Windows。

## 实测记录

- S5 唤醒：模组断电重启后由米家“开机”成功启动
- 远程关机：`systemctl poweroff` 触发，日志：

```
shutdown event
  ack -> 0x0e
  running: systemctl poweroff
```

- 开机 ack：`iot-ready.service` 在启动后发送 `0x10`，米家“正在唤醒”恢复正常
- raw key：`EV_KEY KEY_MACRO1 value=1`
- 固件版本：`1.0.3_0010`
- MIFS：`perf 9 → 2 → 9`，`charge 100 → 80 → 100`，`mic 1 → 0 → 1`
- 风扇：`CPU 2560 RPM`、`GPU 2820 RPM`
- 温度：`coretemp Package 34.0 C`、`acpitz 27.8 C`、`nvme 28.9 C`、`iwlwifi 28.0 C`

## 项目结构

```
ntag5.py                 NTAG5 I2C / NDEF 工具
backup-original.bin      原始 2048 B 用户区备份（本地/忽略，设备相关）
miio_discover.py         局域网 miIO hello 发现
mifs.py                  性能/充电/麦克风
mifs_iot.py              IoT 模组电源/状态
iot_mailbox.py           /dev/mem 底层 mailbox
iot_status.py            WIN_READY/SHUTING/SUSPENDING/固件版本
iot_event.py             事件帧解析（input/journal/mailbox/file）
iot_agent.py             事件策略/执行
iot_ack.py               iot_status.py 兼容入口
input_events.py          raw input 调试
ensure-redmi-event.sh    绑定 redmi-wmi WMI event 设备
build-redmi-wmi.sh       重建带 IoT raw key 的 redmi-wmi
patches/                 redmi-wmi patch
install-iot-ready.sh     安装开机/恢复 ack 服务
install-iot-agent.sh     安装关机/睡眠/唤醒 agent
systemd/                 service 模板
tests/                   单元测试
docs/README.md           三个领域的索引和边界
docs/nfc.md              NTAG5 协议/内存布局
docs/mifs.md             MIFS/EC：性能/充电/麦克风/风扇/温度
docs/iot.md              IoT 模组/事件/官方栈研究记录
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

当前 18 项通过。

## 回滚

```bash
# 暂停自动关机/睡眠
sudo systemctl disable --now iot-agent.service

# 暂停开机/恢复 ack
sudo systemctl disable --now iot-ready.service iot-resume.service

# 回退 patched redmi-wmi
sudo rm /lib/modules/$(uname -r)/updates/redmi-wmi.ko
sudo depmod -a
sudo rmmod redmi_wmi && sudo modprobe redmi_wmi

# NFC：恢复原始小米标签
python3 ntag5.py restore backup-original.bin
```

## 风险

- 这台机器的 `xe` 显卡驱动在 7.2 内核上有已知崩溃（`__xe_unpin_fb_vma` /
  soft lockup）；NFC/I2C 无关，但会卡死整机。
- `iot-agent.service` 会执行 `systemctl poweroff`/`suspend`；不想自动执行就
  disable/stop。
- `acpi_call` 和 `/dev/mem` 都是底层接口；MIFS 写 EC 前先确认状态，NFC 写前先备份。
- `redmi-wmi` patch 在内核升级后需要重新运行 `sudo ./build-redmi-wmi.sh`。

## License

除非单独说明，本项目使用 MIT（见 `LICENSE`）。

`patches/` 里的 `redmi-wmi-iot-keys.patch` 修改的是 GPL-2.0 的 Linux 内核驱动，
因此该 patch 按 GPL-2.0 提供；详见 `patches/README.md`。

## 免责声明

本项目是个人互操作性研究，与小米/Redmi 没有隶属关系，也不代表官方支持。仓库不包含
小米驱动、固件、签名、云 token、账号信息或 Wi-Fi 密码；只包含本机逆向得到的协议事实和
自写代码。请自行遵守当地法律和小米服务条款，风险自负。

`patches/` 里的 patch 针对 Linux in-tree 的 GPL-2.0 `redmi-wmi` 驱动。


## 参考

- NXP NTA5332 datasheet
- Linux in-tree `redmi-wmi`、`bitland-mifs-wmi`
- XiControl 项目：<https://github.com/Oksion/XiControl>
  - WMI/MIFS 协议、IoT 远程唤醒、官方驱动分析
- 小米 NFC 标签协议解析：<https://www.xfy9326.top/posts/mi_nfc_protocol/>

## 友链

[linux.do](https://linux.do)
