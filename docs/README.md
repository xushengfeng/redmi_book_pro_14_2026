# 文档索引

这个项目分成三个独立领域，不要把它们的控制路径混在一起：

| 领域 | 硬件/接口 | 工具 | 文档 |
| --- | --- | --- | --- |
| NFC | NXP NTAG5（I2C `/dev/i2c-1`，0x54） | `ntag5.py` | [nfc.md](nfc.md) |
| MIFS | Bitland MIFS/EC（ACPI `\_SB.PC00.WMID`） | `mifs.py`、`mifs_iot.py` | [mifs.md](mifs.md) |
| IoT | 小米 IoT 模组（ACPI `IOTD0000` + WMI/mailbox） | `iot_status.py`、`iot_event.py`、`iot_agent.py` | [iot.md](iot.md) |

边界：

- **NFC** 只是触控板下面的 NTAG5 标签，读写走 I2C；不经过网络，也不经过 EC。
- **MIFS** 是 host 固件/EC 的控制面：性能模式、充电阈值、麦克风、风扇、温度、IoT 模组电源。
- **IoT** 是独立 always-on 网络模组：远程唤醒、关机/睡眠事件、状态 ack、把 EC 温度/电池转发到米家。
- MIFS 可以给 IoT 模组上下电；IoT 模组可以读 EC 遥测，但它不控制性能/充电阈值。
- `docs/iot.md` 是 IoT 的研究日志，里面也会提到 MIFS/WMAA 的交叉点；MIFS 的独立说明以
  [mifs.md](mifs.md) 为准。

实现状态和安装步骤以根目录 [README.md](../README.md) 为准。
