# CAN UDP 接收器

## 概述

这个项目用于监听多个 UDP 端口，接收封装后的 CAN 报文，并将结果写入 ASC 日志文件，便于后续用 CANalyzer、Wireshark 等工具分析。

当前仓库已经整理为一个可直接分发的最小结构：

- [can_receiver.py](/d:/workspace/0-yunle/4_program/ethernet2can/can_receiver.py): UDP 接收与 ASC 写入
- [send_test_frames.py](/d:/workspace/0-yunle/4_program/ethernet2can/send_test_frames.py): 本地自测发送脚本
- [config.yaml](/d:/workspace/0-yunle/4_program/ethernet2can/config.yaml): 示例配置
- [requirements.txt](/d:/workspace/0-yunle/4_program/ethernet2can/requirements.txt): Python 依赖
- [.gitignore](/d:/workspace/0-yunle/4_program/ethernet2can/.gitignore): 忽略缓存与运行产物

## 环境要求

- Python 3.8+
- `pip`

安装依赖：

```bash
pip install -r requirements.txt
```

## 配置

默认读取脚本同目录下的 [config.yaml](/d:/workspace/0-yunle/4_program/ethernet2can/config.yaml)。

示例：

```yaml
local_ip: "0.0.0.0"
ports:
  - port: 8001
    bus_number: 1
  - port: 8002
    bus_number: 2
```

字段说明：

- `local_ip`: 要绑定的本地地址。`0.0.0.0` 表示监听所有网卡。
- `ports`: 监听项列表。
- `port`: UDP 端口号，范围 `1-65535`。
- `bus_number`: ASC 输出中的总线号，要求为正整数。

## 报文格式

脚本假定每个 UDP 报文的格式如下：

- 第 1 字节：DLC，取低 4 bit，范围 `0-8`
- 第 2-5 字节：CAN ID，大端序
- 第 6 字节开始：CAN 数据区

示例：

```text
\x04\x00\x00\x01\xA2\x01\x23\x45\x67
```

表示：

- DLC = `4`
- CAN ID = `0x1A2`
- 数据 = `01 23 45 67`

如果你的设备协议不是这个格式，需要修改 [can_receiver.py](/d:/workspace/0-yunle/4_program/ethernet2can/can_receiver.py) 中的 `parse_can_id` 和 `parse_can_frame`。

## 运行接收端

```bash
python can_receiver.py
```

启动后会：

- 校验配置文件
- 创建 `csv/` 目录
- 生成形如 `csv/can_data_YYYY-MM-DD_HH-MM-SS.asc` 的输出文件
- 为每个端口启动一个接收线程

当前使用标准 `logging` 输出日志，示例：

```text
2026-03-03 10:00:00 INFO [MainThread] new log file created: D:\workspace\0-yunle\4_program\ethernet2can\csv\can_data_2026-03-03_10-00-00.asc
2026-03-03 10:00:00 INFO [udp-8001] starting receiver on 0.0.0.0:8001, writing to D:\workspace\0-yunle\4_program\ethernet2can\csv\can_data_2026-03-03_10-00-00.asc with bus 1
2026-03-03 10:00:00 INFO [udp-8001] bound receiver to 0.0.0.0:8001
2026-03-03 10:00:05 WARNING [udp-8001] port 8001 skipping invalid packet from ('127.0.0.1', 50000): data too short for CAN frame
```

按 `Ctrl-C` 后，主线程会通知所有接收线程停止，并等待它们退出。

## 运行自测发送端

```bash
python send_test_frames.py
```

该脚本会向本机 `127.0.0.1` 的 `8001` 和 `8002` 端口发送 3 帧测试数据，并输出发送日志。

推荐联调顺序：

1. 保持 [config.yaml](/d:/workspace/0-yunle/4_program/ethernet2can/config.yaml#L1) 中的 `local_ip` 为 `0.0.0.0`
2. 启动 [can_receiver.py](/d:/workspace/0-yunle/4_program/ethernet2can/can_receiver.py)
3. 运行 [send_test_frames.py](/d:/workspace/0-yunle/4_program/ethernet2can/send_test_frames.py)
4. 检查 `csv/` 目录中的 ASC 输出是否新增对应报文

## 输出格式

输出为 ASC 文本文件，头部类似：

```text
date Tue Mar 03 10:00:00 2026
base hex  timestamps absolute
```

报文行类似：

```text
0.123456 1 1A2 Tx d 4 01 23 45 67
0.456789 2 1A3 Tx d 8 12 34 56 78 9A BC DE F0
```

字段说明：

- 相对时间戳
- 总线号
- CAN ID
- 方向，当前固定写为 `Tx`
- 帧类型，当前固定写为 `d`
- 数据长度
- 数据字节

## 已整理的发布项

- 补充了 [requirements.txt](/d:/workspace/0-yunle/4_program/ethernet2can/requirements.txt)，安装方式更明确。
- 补充了 [.gitignore](/d:/workspace/0-yunle/4_program/ethernet2can/.gitignore)，避免提交缓存和运行输出。
- `print` 已替换为标准 `logging`，日志级别和线程来源更清晰。
- 保持单文件脚本结构，避免在当前体量下引入过重的打包配置。

## 故障排除

- 无法绑定端口：检查端口是否被占用，或 `local_ip` 是否属于本机网卡。
- 收不到数据：确认发送端目标地址、端口以及报文格式正确。
- 日志中频繁出现 `skipping invalid packet`：说明收到的数据不满足当前协议假设。
- `exe` 启动后立即退出：优先检查 `config.yaml` 是否与 `exe` 位于同一目录，以及配置中的 `local_ip` 是否属于本机。

## 许可证

MIT License，见 [LICENSE](/d:/workspace/0-yunle/4_program/ethernet2can/LICENSE)。
