# can_sender.py 使用说明（多目标端口版）

## 1. 核心能力

`can_sender.py` 支持以下操作人员常见场景：

1. **多目标并发发送**：每个端口目标都可绑定独立 `ip + port`。
2. **同一配置内管理一次性与周期性报文**：不再依赖额外 CSV 才能发一次性数据。
3. **按端口选择发送模式**：`oneshot` / `cyclic` / `both`。
4. **统一 13 字节编码**：与 `can_receiver.py` 解析协议兼容。

## 2. 报文编码格式（13字节）

- 第1字节：DLC（低4位）
- 第2~5字节：CAN ID（4字节，大端）
- 第6~13字节：CAN数据（最多8字节，不足补0）

示例：`203 3 01 02 03` 编码后为：

```text
03 00 00 00 CB 01 02 03 00 00 00 00 00
```

## 3. send_config.yaml 结构

```yaml
verbose: false

endpoints:
  - name: dev_10_can1
    ip: "192.168.1.10"
    port: 8001
    send_mode: both
    oneshot_frames:
      - "201 6 0F 00 32 00 00 00"
    cyclic_frames:
      - frame: "203 3 01 02 03"
        period_ms: 10

  - name: dev_11_can1
    ip: "192.168.1.11"
    port: 8001
    send_mode: cyclic
    cyclic_frames:
      - frame: "302 4 01 00 00 01"
        period_ms: 20
```

字段说明：

- `verbose`：日志是否详细。
- `endpoints`：端口目标列表。
  - `name`：端口任务名（必须唯一）。
  - `ip`：远端设备 IP。
  - `port`：远端设备端口。
  - `send_mode`：
    - `oneshot`：仅发送 `oneshot_frames` 一次。
    - `cyclic`：仅发送 `cyclic_frames` 周期任务。
    - `both`：先发送一次性报文，再进入周期发送。
  - `oneshot_frames`：一次性报文列表。
  - `cyclic_frames`：周期报文列表（`frame + period_ms`）。

## 4. 运行方式

```bash
python can_sender.py
```

指定配置文件：

```bash
python can_sender.py --config send_config.yaml
```

仅检查配置和编码，不发 UDP：

```bash
python can_sender.py --config send_config.yaml --dry-run
```

## 5. 报文行格式

每条 CAN 报文都使用如下字符串格式：

```text
CAN_ID DLC DATA0 DATA1 ...
```

例如：

```text
201 6 0F 00 32 00 00 00
```

规则：

- `CAN_ID` 支持十进制（如 `201`）或 `0x` 十六进制（如 `0x201`）。
- `DLC` 必须在 `0~8`。
- 数据字节数量必须与 `DLC` 一致。
- 每个字节使用十六进制（`00~FF`）。
