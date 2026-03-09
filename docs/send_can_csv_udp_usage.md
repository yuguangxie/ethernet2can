# can_sender.py 使用说明

## 1. 功能概览

`can_sender.py` 是一个独立的 CAN UDP 发送程序，支持：

1. 从 `send_config.yaml` 读取发送参数（目标 IP、端口映射、默认通道、日志开关等）。
2. 按配置中的 `cyclic_frames` 周期发送报文（例如 10ms 周期持续发送）。
3. 可选读取 CSV/文本文件做一次性发送（`--csv-file`）。

发送报文编码为固定 **13 字节**，且可被仓库内 `can_receiver.py` 直接解析。

## 2. 13 字节编码格式

每帧格式如下：

- 第 1 字节：DLC（低 4 bit）
- 第 2~5 字节：CAN ID（4 字节，大端）
- 第 6~13 字节：CAN 数据（最多 8 字节，不足补 `00`）

即：`1 + 4 + 8 = 13` 字节。

示例帧：

```text
203 3 01 02 03
```

编码后：

```text
03 00 00 00 CB 01 02 03 00 00 00 00 00
```

## 3. 配置文件 send_config.yaml

默认配置文件名：`send_config.yaml`（与 `can_sender.py` 同目录）。

示例：

```yaml
target_ip: "192.168.1.10"
ports:
  "1": 4001
  "2": 4002
default_can_channel: 1
verbose: false

cyclic_frames:
  - frame: "203 3 01 02 03"
    period_ms: 10
    can_channel: 1
  - frame: "418 8 11 22 33 44 55 66 77 88"
    period_ms: 100
    can_channel: 2
```

字段说明：

- `target_ip`：目标以太网转 CAN 设备 IP。
- `ports`：CAN 通道到 UDP 端口映射。你当前设备是：
  - CAN1 -> `4001`
  - CAN2 -> `4002`
- `default_can_channel`：默认发送通道（用于 `--csv-file` 一次性发送）。
- `verbose`：是否开启调试日志。
- `cyclic_frames`：周期发送列表。
  - `frame`：报文字符串，格式 `CAN_ID DLC DATA...`
  - `period_ms`：发送周期（毫秒）
  - `can_channel`：该条报文发送到哪个 CAN 通道（可选）

## 4. 输入报文格式（frame / csv）

每行或每条 `frame` 使用同一格式：

```text
CAN_ID DLC BYTE0 BYTE1 ...
```

例如：

```text
201 6 0F 00 32 00 00 00
```

说明：

- CAN_ID 支持十进制（如 `201`）和十六进制（如 `0x201`）。
- DLC 范围 `0~8`。
- 数据字节数量必须与 DLC 一致。
- 数据字节按十六进制解析（`00`~`FF`）。
- 支持空格、逗号或混合分隔。

## 5. 运行示例

### 5.1 按 send_config.yaml 周期发送

```bash
python can_sender.py
```

### 5.2 指定配置文件

```bash
python can_sender.py --config send_config.yaml
```

### 5.3 先 CSV 一次性发送，再进入周期发送

```bash
python can_sender.py --config send_config.yaml --csv-file examples/can_frames_sample.csv
```

### 5.4 停止发送

运行中按 `Ctrl-C` 结束周期发送线程。

## 6. CSV 示例

`examples/can_frames_sample.csv`：

```text
# 每行: CAN_ID DLC DATA...
201 6 0F 00 32 00 00 00
418 8 11 22 33 44 55 66 77 88
0x7FF 2 AB CD
```

## 7. 常见问题

1. **设备收不到报文**：检查 `target_ip`、端口映射、防火墙与网线连接。
2. **报文未按预期发送**：检查 `frame` 格式和 `period_ms` 是否正确。
3. **某条配置被跳过**：查看日志，通常是 DLC 与数据字节数不匹配。
