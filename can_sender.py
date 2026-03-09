"""CAN UDP sender.

This script sends CAN frames to an ethernet-to-CAN gateway using the same
13-byte frame layout that `can_receiver.py` can parse:

- byte0: DLC (low 4 bits)
- byte1~4: CAN ID (big-endian)
- byte5~12: data area (up to 8 bytes, padded with 0x00)

Features:
- YAML config driven sending
- cyclic frames with custom period (ms)
- optional one-shot CSV/text frame sending
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import yaml

LOG_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
LOGGER = logging.getLogger("ethernet2can.can_sender")
FRAME_SIZE = 13
MAX_DLC = 8
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "send_config.yaml"


class FrameFormatError(ValueError):
    """Raised when an input CAN frame line/config item is invalid."""


def configure_logging(verbose: bool) -> None:
    """Initialize logging with configurable level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def split_frame_line(line: str) -> List[str]:
    """Split one frame line supporting spaces, commas, or mixed separators."""
    return [token for token in line.replace(",", " ").split() if token]


def parse_can_id(token: str) -> int:
    """Parse CAN ID token in decimal or 0x-prefixed hexadecimal format."""
    value = int(token, 16) if token.lower().startswith("0x") else int(token)
    if not 0 <= value <= 0x1FFFFFFF:
        raise FrameFormatError(f"CAN ID out of range: {token}")
    return value


def parse_dlc(token: str) -> int:
    """Parse DLC and validate it is in [0, 8]."""
    value = int(token)
    if not 0 <= value <= MAX_DLC:
        raise FrameFormatError(f"DLC out of range (0-8): {token}")
    return value


def parse_payload(tokens: Iterable[str], dlc: int) -> List[int]:
    """Parse payload bytes and validate length/value range."""
    values = [int(t, 16) for t in tokens]
    if len(values) != dlc:
        raise FrameFormatError(f"payload count({len(values)}) != DLC({dlc})")
    for value in values:
        if not 0 <= value <= 0xFF:
            raise FrameFormatError(f"invalid byte value: {value}")
    return values


def parse_frame_text(frame_text: str) -> Tuple[int, int, List[int]]:
    """Parse one frame string like '203 3 01 02 03'."""
    tokens = split_frame_line(frame_text)
    if len(tokens) < 2:
        raise FrameFormatError("frame must contain at least CAN_ID and DLC")

    can_id = parse_can_id(tokens[0])
    dlc = parse_dlc(tokens[1])
    payload = parse_payload(tokens[2:], dlc)
    return can_id, dlc, payload


def encode_frame_13_bytes(can_id: int, dlc: int, payload: List[int]) -> bytes:
    """Encode CAN frame to fixed 13-byte UDP payload."""
    frame = bytearray(FRAME_SIZE)
    frame[0] = dlc & 0x0F
    frame[1:5] = can_id.to_bytes(4, byteorder="big", signed=False)
    frame[5 : 5 + dlc] = bytes(payload)
    return bytes(frame)


def load_send_config(config_path: Path) -> dict:
    """Load and validate sender config YAML."""
    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {config_path}: {exc}") from exc

    required = ("target_ip", "ports", "default_can_channel", "cyclic_frames")
    for key in required:
        if key not in config:
            raise ValueError(f"missing required config key: {key}")

    if not isinstance(config["ports"], dict):
        raise ValueError("ports must be a map, e.g. {1: 4001, 2: 4002}")

    return config


def channel_to_port(ports: dict, channel: int) -> int:
    """Resolve configured UDP port by CAN channel id."""
    str_key = str(channel)
    if str_key in ports:
        port = ports[str_key]
    elif channel in ports:
        port = ports[channel]
    else:
        raise ValueError(f"channel {channel} not found in ports config")

    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"invalid port for channel {channel}: {port}")
    return port


def send_csv_once(sock: socket.socket, csv_path: Path, target_ip: str, target_port: int) -> int:
    """Send all frames from CSV/text once to one target endpoint."""
    sent = 0
    with csv_path.open("r", encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                can_id, dlc, payload = parse_frame_text(line)
            except (ValueError, FrameFormatError) as exc:
                LOGGER.warning("skip csv line %s: %s | %s", line_no, exc, line)
                continue

            frame = encode_frame_13_bytes(can_id, dlc, payload)
            sock.sendto(frame, (target_ip, target_port))
            LOGGER.info(
                "csv line=%s -> %s:%s can_id=0x%X dlc=%s data=%s",
                line_no,
                target_ip,
                target_port,
                can_id,
                dlc,
                " ".join(f"{x:02X}" for x in payload) or "(empty)",
            )
            sent += 1
    return sent


def cyclic_sender_thread(
    sock: socket.socket,
    stop_event: threading.Event,
    target_ip: str,
    target_port: int,
    frame_text: str,
    period_ms: int,
    name: str,
) -> None:
    """Continuously send one frame with a fixed period until stopped."""
    try:
        can_id, dlc, payload = parse_frame_text(frame_text)
    except (ValueError, FrameFormatError) as exc:
        LOGGER.error("invalid cyclic frame '%s': %s", frame_text, exc)
        return

    if period_ms <= 0:
        LOGGER.error("invalid period_ms=%s for frame '%s'", period_ms, frame_text)
        return

    frame = encode_frame_13_bytes(can_id, dlc, payload)
    period_seconds = period_ms / 1000.0
    next_send = time.monotonic()
    count = 0

    LOGGER.info(
        "start cyclic sender %s -> %s:%s frame='%s' period=%sms",
        name,
        target_ip,
        target_port,
        frame_text,
        period_ms,
    )

    while not stop_event.is_set():
        sock.sendto(frame, (target_ip, target_port))
        count += 1

        if count % 100 == 0:
            LOGGER.info("%s sent %s frames", name, count)

        next_send += period_seconds
        sleep_time = next_send - time.monotonic()
        if sleep_time > 0:
            stop_event.wait(sleep_time)
        else:
            next_send = time.monotonic()

    LOGGER.info("stop cyclic sender %s, total=%s", name, count)


def run_sender(config_path: Path, csv_file: Path | None) -> None:
    """Run sender from YAML config with optional one-shot CSV sending."""
    config = load_send_config(config_path)
    configure_logging(bool(config.get("verbose", False)))

    target_ip = str(config["target_ip"])
    ports = config["ports"]
    default_channel = int(config["default_can_channel"])
    default_port = channel_to_port(ports, default_channel)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    try:
        if csv_file is not None:
            sent = send_csv_once(sock, csv_file, target_ip, default_port)
            LOGGER.info("csv one-shot completed, sent=%s", sent)

        cyclic_frames = config.get("cyclic_frames", [])
        for index, item in enumerate(cyclic_frames, start=1):
            if not isinstance(item, dict):
                LOGGER.warning("skip cyclic_frames[%s], not a map", index)
                continue

            frame_text = str(item.get("frame", "")).strip()
            if not frame_text:
                LOGGER.warning("skip cyclic_frames[%s], empty frame", index)
                continue

            channel = int(item.get("can_channel", default_channel))
            period_ms = int(item.get("period_ms", 10))
            target_port = channel_to_port(ports, channel)

            thread = threading.Thread(
                target=cyclic_sender_thread,
                args=(
                    sock,
                    stop_event,
                    target_ip,
                    target_port,
                    frame_text,
                    period_ms,
                    f"cyclic-{index}",
                ),
                daemon=False,
                name=f"cyclic-{index}",
            )
            thread.start()
            threads.append(thread)

        if not threads:
            LOGGER.info("no cyclic_frames configured, sender exits")
            return

        LOGGER.info("all cyclic senders started, press Ctrl-C to stop")
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        LOGGER.info("received Ctrl-C, stopping cyclic senders")
        stop_event.set()
        for thread in threads:
            thread.join()
    finally:
        sock.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(description="CAN UDP sender with YAML config")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="sender config yaml path",
    )
    parser.add_argument(
        "--csv-file",
        type=Path,
        help="optional CSV/text file for one-shot sending before cyclic send",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    csv_file = args.csv_file.resolve() if args.csv_file else None
    run_sender(args.config.resolve(), csv_file)


if __name__ == "__main__":
    main()
