# send_can_csv_udp.py 使用说明

## 1. 功能说明

`send_can_csv_udp.py` 用于读取 CSV/文本中的 CAN 帧定义，将每帧编码为**固定 13 字节数据流**并通过 UDP 发送到以太网转 CAN 设备。

编码格式与 `can_receiver.py` 的解析逻辑兼容：

- 第 1 字节：`DLC`（低 4 bit）
- 第 2~5 字节：`CAN ID`（4 字节，大端）
- 第 6~13 字节：`CAN DATA`（最多 8 字节，不足补 `00`）

> 固定 13 字节 = 1 + 4 + 8。

## 2. 设备与端口映射

远端设备 IP：`192.168.1.10`

- CAN1 -> `192.168.1.10:4001`
- CAN2 -> `192.168.1.10:4002`

脚本默认使用 CAN1（4001），可通过参数切换。

## 3. 输入数据文件格式

每行代表一帧，格式：

```text
CAN_ID DLC BYTE0 BYTE1 ...
```

示例：

```text
201 6 0F 00 32 00 00 00
```

解释：

- `201`：CAN ID（十进制；也支持 `0x201`）
- `6`：DLC
- 后续 6 个字节：十六进制数据

注意：

1. 数据字节数必须与 DLC 一致。
2. 每个数据字节范围 `00` 到 `FF`。
3. 支持空行和 `#` 注释行。
4. 分隔符可用空格、逗号，或混合。

## 4. 快速开始

### 4.1 安装依赖

本脚本只使用 Python 标准库，无新增依赖。若你也要运行接收端，可先安装：

```bash
pip install -r requirements.txt
```

### 4.2 先检查编码（不发包）

```bash
python send_can_csv_udp.py examples/can_frames_sample.csv --dry-run --can-channel 1
```

### 4.3 发到 CAN1（4001）

```bash
python send_can_csv_udp.py examples/can_frames_sample.csv --target-ip 192.168.1.10 --can-channel 1
```

### 4.4 发到 CAN2（4002）

```bash
python send_can_csv_udp.py examples/can_frames_sample.csv --target-ip 192.168.1.10 --can-channel 2
```

### 4.5 手动指定端口（覆盖通道映射）

```bash
python send_can_csv_udp.py examples/can_frames_sample.csv --target-ip 192.168.1.10 --target-port 4002
```

## 5. 常用参数

- `csv_file`：输入文件路径（必填）
- `--target-ip`：目标 IP，默认 `192.168.1.10`
- `--can-channel`：CAN 通道，`1` 或 `2`，默认 `1`
- `--target-port`：手动端口（优先级高于 `--can-channel`）
- `--interval`：帧间隔秒，默认 `0.01`
- `--dry-run`：仅解析与编码，不发送 UDP
- `--verbose`：输出更详细日志

## 6. 编码示例（13字节）

以：

```text
201 6 0F 00 32 00 00 00
```

为例：

- CAN ID `201(dec)` = `0xC9`
- DLC = `6`
- Data = `0F 00 32 00 00 00`

编码后 13 字节：

```text
06 00 00 00 C9 0F 00 32 00 00 00 00 00
```

可被 `can_receiver.py` 正确解析为：

- DLC = 6
- CAN ID = 0xC9
- Data = `0F 00 32 00 00 00`

## 7. 故障排查

1. **收不到数据**：确认网络可达、IP/端口正确、防火墙放行 UDP。
2. **行被跳过**：检查该行 DLC 与数据字节个数是否一致。
3. **解析结果异常**：先执行 `--dry-run`，检查日志中的 `frame=` 十六进制编码。
