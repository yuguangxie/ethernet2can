import argparse
import logging
import socket
import time
from pathlib import Path
from typing import Iterable, List, Tuple

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOGGER = logging.getLogger("ethernet2can.csv_sender")
DEFAULT_TARGET_IP = "192.168.1.10"
DEFAULT_PORTS = {1: 4001, 2: 4002}
MAX_DLC = 8
FRAME_SIZE = 13


class CsvFrameError(ValueError):
    pass


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def split_csv_line(line: str) -> List[str]:
    # 支持逗号或空白分隔，也支持混合场景
    return [token for token in line.replace(",", " ").split() if token]


def parse_can_id(token: str) -> int:
    value = int(token, 16) if token.lower().startswith("0x") else int(token)
    if not 0 <= value <= 0x1FFFFFFF:
        raise CsvFrameError(f"CAN ID 超出范围: {token}")
    return value


def parse_dlc(token: str) -> int:
    value = int(token)
    if not 0 <= value <= MAX_DLC:
        raise CsvFrameError(f"DLC 超出范围(0-8): {token}")
    return value


def parse_data(tokens: Iterable[str], dlc: int) -> List[int]:
    values = [int(t, 16) for t in tokens]
    if len(values) != dlc:
        raise CsvFrameError(f"数据字节数量({len(values)})与 DLC({dlc})不一致")
    for value in values:
        if not 0 <= value <= 0xFF:
            raise CsvFrameError(f"存在非法字节值: {value}")
    return values


def encode_frame_13_bytes(can_id: int, dlc: int, payload: List[int]) -> bytes:
    """编码为固定 13 字节: [DLC(低4位)] + [CAN ID 4字节大端] + [DATA 8字节不足补0]"""
    frame = bytearray(FRAME_SIZE)
    frame[0] = dlc & 0x0F
    frame[1:5] = can_id.to_bytes(4, byteorder="big", signed=False)
    frame[5 : 5 + dlc] = bytes(payload)
    return bytes(frame)


def parse_csv_frame(line: str, line_no: int) -> Tuple[int, int, List[int]]:
    tokens = split_csv_line(line)
    if not tokens:
        raise CsvFrameError("空行")
    if tokens[0].startswith("#"):
        raise CsvFrameError("注释行")
    if len(tokens) < 2:
        raise CsvFrameError("至少需要 CAN ID 和 DLC")

    can_id = parse_can_id(tokens[0])
    dlc = parse_dlc(tokens[1])
    payload = parse_data(tokens[2:], dlc)
    return can_id, dlc, payload


def send_frames(
    csv_path: Path,
    target_ip: str,
    target_port: int,
    interval_seconds: float,
    dry_run: bool,
) -> int:
    sent = 0
    sock = None if dry_run else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        with csv_path.open("r", encoding="utf-8") as file:
            for line_no, raw_line in enumerate(file, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                try:
                    can_id, dlc, payload = parse_csv_frame(line, line_no)
                except (ValueError, CsvFrameError) as exc:
                    LOGGER.warning("第 %s 行已跳过: %s | 原始内容: %s", line_no, exc, line)
                    continue

                frame = encode_frame_13_bytes(can_id, dlc, payload)
                payload_hex = " ".join(f"{byte:02X}" for byte in payload) if payload else "(empty)"

                if dry_run:
                    LOGGER.info(
                        "[DRY-RUN] line=%s -> %s:%s can_id=0x%X dlc=%s frame=%s",
                        line_no,
                        target_ip,
                        target_port,
                        can_id,
                        dlc,
                        frame.hex(" ").upper(),
                    )
                else:
                    sock.sendto(frame, (target_ip, target_port))
                    LOGGER.info(
                        "line=%s -> %s:%s can_id=0x%X dlc=%s data=%s",
                        line_no,
                        target_ip,
                        target_port,
                        can_id,
                        dlc,
                        payload_hex,
                    )
                    if interval_seconds > 0:
                        time.sleep(interval_seconds)

                sent += 1

        return sent
    finally:
        if sock is not None:
            sock.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从 CSV 文本读取 CAN 帧并编码为固定 13 字节 UDP 报文发送到以太网转 CAN 设备。"
        )
    )
    parser.add_argument("csv_file", type=Path, help="CSV/文本文件路径，每行一帧")
    parser.add_argument("--target-ip", default=DEFAULT_TARGET_IP, help="目标设备 IP")
    parser.add_argument(
        "--can-channel",
        type=int,
        choices=(1, 2),
        default=1,
        help="目标 CAN 通道: 1=>端口4001, 2=>端口4002",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        help="手动覆盖目标 UDP 端口；不填则按 --can-channel 自动选择",
    )
    parser.add_argument("--interval", type=float, default=0.01, help="帧间隔秒")
    parser.add_argument("--dry-run", action="store_true", help="仅解析和编码，不实际发送")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)

    csv_path = args.csv_file.resolve()
    if not csv_path.exists():
        parser.error(f"文件不存在: {csv_path}")

    target_port = args.target_port if args.target_port else DEFAULT_PORTS[args.can_channel]
    LOGGER.info(
        "开始发送: file=%s target=%s:%s channel=%s dry_run=%s",
        csv_path,
        args.target_ip,
        target_port,
        args.can_channel,
        args.dry_run,
    )

    sent = send_frames(
        csv_path=csv_path,
        target_ip=args.target_ip,
        target_port=target_port,
        interval_seconds=args.interval,
        dry_run=args.dry_run,
    )
    LOGGER.info("完成，成功处理并发送 %s 帧", sent)


if __name__ == "__main__":
    main()
