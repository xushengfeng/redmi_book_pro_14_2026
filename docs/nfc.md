# NTAG5 NFC 标签笔记

> 领域：NFC（I2C 标签），与 MIFS/EC、IoT 模组相互独立。三个领域的索引见
> [README.md](README.md)。

## 1. 硬件

```
ACPI: \_SB.PC00.I2C5.NFC    HID = "NTAG5332"
I2C:  /dev/i2c-1            7-bit address 0x54
IRQ:  GPI3 上的 GPIO 中断
RST:  GPI3 上的 GPIO（输出）
```

- 芯片：NXP **NTAG 5 boost（NTA5332）**
- 用户 EEPROM：2048 B
- 协议：NFC Forum Type 5 / ISO/IEC 15693
- NDEF 容量：由 CC 决定，本机为 1024 B

手机截图里的 `ISO/IEC 14443-3 Type A`、`16 扇区 / 64 块` 是手机 App 的展示字段；实际 I2C 读出的 CC 是：

```
E1 40 80 09
```

即 `E1` magic、version 0x40、size 0x80（0x80 × 8 = 1024 B）、access 0x09。

## 2. I2C 协议

### 2.1 block 寻址

NTAG5 的 I2C 地址不是 byte offset，而是 **block 地址**，每个 block 4 字节：

```
用户区 block 0x000 .. 0x1FF   -> 2048 B
配置区 block 0x1000 ..       -> 从 I2C 地址 0x1000 开始
```

所以：

```
byte offset 0x0000 -> block 0x000
byte offset 0x0004 -> block 0x001
byte offset 0x07F0 -> block 0x1FC
```

这是实现时最容易踩的坑。把 byte offset 直接当成 I2C 地址写，会写到用户区之外并 NAK。

### 2.2 读

```
START, SL_AD+W, BL_AD1, BL_AD0, STOP,
START, SL_AD+R, DATA0 ... DATAN, STOP
```

读长度可以是任意值，芯片会自动按 block 递增。首次空闲后读经常返回 `FF`，先做一次 dummy 读（block 0，4 字节）即可。

### 2.3 写

```
START, SL_AD+W, BL_AD1, BL_AD0, DATA0, DATA1, DATA2, DATA3, STOP
```

一次必须写满一个 block 的 4 字节。写完要等约 10 ms 的 EEPROM 周期，期间读会 NAK。工具里已经加了延时。

### 2.4 不能碰的区域

- block 0：CC。工具在恢复时跳过，避免碰到可能被锁的 CC。
- 最后一个 block（0x1FF）：NFC 侧的 16-bit counter，I2C 不可访问。
- 用户区尾部有一部分是锁定的/OTP；`restore --full` 可能报告这里有差异。默认 `restore` 只恢复 NDEF 区（前 1024 B），不影响“一碰传”。

## 3. NDEF

本机原始用户区前 132 字节：

```
0000: e1 40 80 09 03 7d d4 2a 50 63 6f 6d 2e 78 69 61
0010: 6f 6d 69 2e 6d 69 5f 63 6f 6e 6e 65 63 74 5f 73
0020: 65 72 76 69 63 65 3a 65 78 74 65 72 6e 61 6c 74
0030: 79 70 65 0a 4e 08 01 10 0d 22 01 03 2a 09 4d 49
0040: 2d 4e 46 43 54 41 47 38 0f 4a 34 27 17 00 00 00
0050: 03 00 0e 54 41 47 5f 44 49 53 43 4f 56 45 52 45
0060: 44 65 06 4d 49 52 52 4f 52 01 11 30 38 3a 45 42
0070: 3a 32 31 3a 42 34 3a 34 45 3a 31 30 79 01 01 6a
0080: 02 fa 7f ...
```

结构：

```
E1 40 80 09              CC
03 7D                    NDEF TLV，长度 0x7D = 125
D4 2A 50                 NDEF record header / type length / payload length
"com.xiaomi.mi_connect_service:externaltype"   42 字节 type
0A 4E ...                payload（80 字节）
FE                       终止 TLV（本机在 125 字节之后是 00，也可接受）
```

Record 是 `TNF=4 (EXTERNAL)`，payload 是 protobuf：

```
0A 4E                field 1 (advData), length 78
  08 01              versionMajor = 1
  10 0D              versionMinor = 13
  22 01 03           flags = 03
  2A 09 "MI-NFCTAG"  name
  38 0F              deviceType = 15
  4A 34 ...          appsData (52 字节)
  6A 02 FA 7F        commData
```

`appsData` 里是小米自定义的 TLV：

```
27 17 00 00 00 03 00 0E "TAG_DISCOVERED" 65 06 "MIRROR"
01 11 "08:EB:21:B4:4E:10" 79 01 01
```

- `deviceType=3` 表示 PC（2=TV，3=PC，5=CAR，8=PAD）
- `TAG_DISCOVERED` / `MIRROR` 是动作
- `01 11` 后面是蓝牙 MAC；本机 `wlan0` 是 `08:EB:21:B4:4E:0C`，标签里是 BT 的 `...:10`

`ntag5.py read` 会把这些字段展开打印。

## 4. 工具行为

```bash
python3 ntag5.py info
python3 ntag5.py read
python3 ntag5.py dump -o backup.bin
python3 ntag5.py scratch                 # 0x7e0 写/读/还原自检
python3 ntag5.py write-text 'hello'
python3 ntag5.py write-uri 'https://example.com'
python3 ntag5.py write-raw 'D101...'
python3 ntag5.py restore backup.bin
```

- `write-*` 先读整个用户区，构造新 NDEF，然后只写 block 1 开始到 TLV 结束的 4 字节对齐区域。
- 写入后逐 block 回读校验。
- `restore` 只写和备份不同的 block；默认范围是 4..1024（NDEF 区）。
- 原始备份：`backup-original.bin`。

## 5. 测试记录

- 2026-10-27：`scratch` 写 0x7e0 成功、读回、还原成功。
- 2026-10-27：`write-text 'Hello from Linux'` 成功，`read` 返回 `TNF=1 type=T`。
- 2026-10-27：`write-uri https://example.com/redmi` 成功，payload `04 65 78 61 6d 70 6c 65 ...`。
- 2026-10-27：`restore backup-original.bin` 后 `read` 恢复原始小米记录；`diff` 为 0。
- 手机端 “Type A / MIFARE” 字段与实际芯片不一致，但 NDEF 内容完全匹配，说明读到的是这张 NTAG5。

## 6. 参考

- NXP NTA5332 datasheet（I2C READ/WRITE MEMORY、CC、NDEF TLV）
- 小米 NFC 标签协议解析：<https://www.xfy9326.top/posts/mi_nfc_protocol/>
- XiControl 项目对 NFC 的记录（Windows 未把 NTAG5 暴露为标准 NFC 设备）
