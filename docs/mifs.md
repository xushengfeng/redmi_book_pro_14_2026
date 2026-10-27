# MIFS / EC 控制

领域：**host 侧的 Bitland MIFS + EC**，不是 IoT 模组。

- ACPI：`\_SB.PC00.WMID`（`PNP0C14`，`_UID = "MIFS"`）
- 方法：`WMAA`
- 传输：`acpi_call` 调 `WMAA`；风扇 tach 直接读 EC 共享内存窗口
- 工具：`mifs.py`（性能/充电/麦克风/风扇/温度）、`mifs_iot.py`（IoT 模组电源）

> 性能模式、充电阈值、麦克风、风扇、温度都不经过 `IOTD0000`。关掉 IoT 模组后仍然可用。

## WMAA 输入缓冲

```
offset 0: 00
offset 1: FA=GET / FB=SET
offset 2: 00
offset 3: function
offset 4: arg
offset 5: 00
offset 6: arg2
offset 7: 00
offset 8..31: 00
```

返回 32 字节：

```
OUT[0..1] = 状态，0x8000 = 支持，0xE000 = 不支持
OUT[2..3] = 功能组回显
OUT[4..5] = FRD0
OUT[6..9] = FRD1（值，little-endian）
```

## 功能表

| 功能 | 命令 | 实测 |
| --- | --- | --- |
| 性能模式 | `GET/SET 0x08`，值 2/3/4/9/10 | ✅ `9 -> 2 -> 9` |
| 充电阈值 | `GET/SET 0x10/0x02`，码 0/1/4/5/6/7/8 | ✅ `100 -> 80 -> 100` |
| 麦克风静音 | `GET/SET 0x0A/0x05` | ✅ `unmuted -> muted -> unmuted` |
| IoT 模组电源 | `GET/SET 0x0C/0x03` | ✅ `0 -> 1` |
| IoT 模组存在 | `GET 0x0C/0x02` | ✅ 1 |
| IoT mailbox/绑定 | `GET 0x0C/0x06` | 绑定后仍 0，不能作为判据 |
| 风扇（MIFS） | `GET 0x0D` | ❌ v2 返回 `0xE000` |
| CPU 温度（MIFS） | `GET 0x16` | ❌ v2 返回 `0xE000` |

## 风扇

MIFS v2 固件没有 WMI 风扇函数，`0x0D` 返回 `0xE000`。风扇转速从 EC 共享内存窗口读：

```
EC window base: 0xFE0B0300
CPU tach: +0x69, u16 little-endian
GPU tach: +0x6B, u16 little-endian
0 = stopped/idle
```

工具：

```bash
sudo python3 mifs.py fan
```

实测：

```
fan CPU: 2560 RPM
fan GPU: 2820 RPM
```

`/sys/class/hwmon/hwmon4`（`bitland_mifs`）也有 fan1/2/3，但当前 v2 固件下全是 0；
等上游 MIFS v2 补丁进入内核后会由驱动填充。

### 风扇控制

**不能直接控制转速。** v2 固件没有暴露 PWM/风扇控制接口：

- `0x14`（MAX_FAN_SWITCH）、`0x15`（MAX_FAN_SPEED）都返回 `0xE000`
- hwmon 只有 `fan*_input`，没有 `pwm1`
- `bitland-mifs-wmi` 的 `fan_boost` sysfs 写的是 `0x14`，在这台机器上同样被拒绝
- `fancontrol`/`pwmconfig` 需要 `pwm1`，无法使用
- 直接写 EC 风扇寄存器没有公开/安全的寄存器定义，不建议

能做的只有间接影响：通过性能模式 `0x08` 切换 quiet/balanced/speed/extreme，让 EC 使用
不同的风扇曲线。风扇本身仍由 EC 固件闭环控制。

## 温度

MIFS `0x16` 在 v2 上不支持，温度用标准 hwmon：

```bash
python3 mifs.py temp
```

实测：

```
acpitz: 27.8 C
nvme Composite: 28.9 C
coretemp Package id 0: 34.0 C
iwlwifi_1: 28.0 C
bitland_mifs: 0.0 C   # v2 下未填充
```

## 上游 Linux 支持

| 功能 | 上游状态 |
| --- | --- |
| 电池容量/状态 | ✅ 标准 ACPI battery |
| 性能模式 | ⚠️ `bitland-mifs-wmi` 有 `platform_profile`，但这台 MIFS v2 上失败；`/sys/class/platform_profile` 不存在 |
| 风扇/温度 | ⚠️ `bitland_mifs` hwmon 存在但 v2 下为 0；v2 补丁在 LKML，未进入当前内核/master |
| 充电阈值 | ❌ 驱动没有实现；`BAT0` 没有 `charge_control_end_threshold` |
| 麦克风静音 | ❌ 没有上游接口 |
| IoT 模组电源 | ❌ 没有上游接口；用 `mifs_iot.py` |

MIFS v2 的补丁系列（EC fan tach、lid、QFAN 映射等）还在邮件列表；当前内核 7.2.11
的 `bitland-mifs-wmi` 只覆盖 v1 语义，所以本项目用原始 WMAA + EC window 绕过。

## 安全

- `mifs.py` 的 SET 会真的改 EC/固件状态；改完可用 `get` 读回。
- 性能模式/充电阈值随电源状态可能被 EC 重置；需要时重新设置。
- 风扇/温度读取是只读的；风扇 tach 读 `/dev/mem`，需要 root。
- `acpi_call` 是实验性接口；确认 `/proc/acpi/call` 存在再运行。
