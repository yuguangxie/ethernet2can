"""Configurable CAN-over-UDP sender.

The sender encodes each CAN frame into fixed 13-byte payloads:
- byte0: DLC (low 4 bits)
- byte1~4: CAN ID (big-endian)
- byte5~12: data (0~8 bytes, right-padded with 0x00)

It supports multiple remote endpoints simultaneously, and each endpoint can send:
- one-shot frames
- cyclic frames
- or both
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import yaml

LOG_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
LOGGER = logging.getLogger("ethernet2can.can_sender")
FRAME_SIZE = 13
MAX_DLC = 8
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "send_config.yaml"
VALID_SEND_MODES = {"oneshot", "cyclic", "both"}


class FrameFormatError(ValueError):
    """Raised when a frame text has invalid CAN/DLC/payload format."""


@dataclass(frozen=True)
class CyclicFrameTask:
    """One cyclic frame sending task bound to one endpoint."""

    endpoint_name: str
    ip: str
    port: int
    frame_text: str
    period_ms: int


def configure_logging(verbose: bool) -> None:
    """Initialize logging level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def split_frame_line(line: str) -> List[str]:
    """Split one frame line by spaces/commas (mixed separator friendly)."""
    return [token for token in line.replace(",", " ").split() if token]


def parse_can_id(token: str) -> int:
    """Parse CAN ID in decimal or 0x-prefixed hex format."""
    value = int(token, 16) if token.lower().startswith("0x") else int(token)
    if not 0 <= value <= 0x1FFFFFFF:
        raise FrameFormatError(f"CAN ID out of range: {token}")
    return value


def parse_dlc(token: str) -> int:
    """Parse DLC and ensure 0 <= DLC <= 8."""
    value = int(token)
    if not 0 <= value <= MAX_DLC:
        raise FrameFormatError(f"DLC out of range (0-8): {token}")
    return value


def parse_payload(tokens: Iterable[str], dlc: int) -> List[int]:
    """Parse payload bytes in hex and verify count matches DLC."""
    values = [int(t, 16) for t in tokens]
    if len(values) != dlc:
        raise FrameFormatError(f"payload count({len(values)}) != DLC({dlc})")
    for value in values:
        if not 0 <= value <= 0xFF:
            raise FrameFormatError(f"invalid byte value: {value}")
    return values


def parse_frame_text(frame_text: str) -> tuple[int, int, List[int]]:
    """Parse one frame string like '203 3 01 02 03'."""
    tokens = split_frame_line(frame_text)
    if len(tokens) < 2:
        raise FrameFormatError("frame must contain at least CAN_ID and DLC")

    can_id = parse_can_id(tokens[0])
    dlc = parse_dlc(tokens[1])
    payload = parse_payload(tokens[2:], dlc)
    return can_id, dlc, payload


def encode_frame_13_bytes(can_id: int, dlc: int, payload: List[int]) -> bytes:
    """Encode one CAN frame into fixed 13-byte payload."""
    frame = bytearray(FRAME_SIZE)
    frame[0] = dlc & 0x0F
    frame[1:5] = can_id.to_bytes(4, byteorder="big", signed=False)
    frame[5 : 5 + dlc] = bytes(payload)
    return bytes(frame)


def parse_and_encode(frame_text: str) -> bytes:
    """Parse frame text then encode to 13-byte payload."""
    can_id, dlc, payload = parse_frame_text(frame_text)
    return encode_frame_13_bytes(can_id, dlc, payload)


def _validate_endpoint_config(index: int, endpoint: dict) -> None:
    """Validate one endpoint config entry."""
    if not isinstance(endpoint, dict):
        raise ValueError(f"endpoints[{index}] must be a map")

    for key in ("name", "ip", "port"):
        if key not in endpoint:
            raise ValueError(f"endpoints[{index}] missing required key: {key}")

    if not isinstance(endpoint["name"], str) or not endpoint["name"].strip():
        raise ValueError(f"endpoints[{index}].name must be non-empty string")

    if not isinstance(endpoint["ip"], str) or not endpoint["ip"].strip():
        raise ValueError(f"endpoints[{index}].ip must be non-empty string")

    if not isinstance(endpoint["port"], int) or not 1 <= endpoint["port"] <= 65535:
        raise ValueError(f"endpoints[{index}].port invalid: {endpoint['port']}")

    send_mode = endpoint.get("send_mode", "both")
    if send_mode not in VALID_SEND_MODES:
        raise ValueError(
            f"endpoints[{index}].send_mode must be one of {sorted(VALID_SEND_MODES)}"
        )

    if "oneshot_frames" in endpoint and not isinstance(endpoint["oneshot_frames"], list):
        raise ValueError(f"endpoints[{index}].oneshot_frames must be list")
    if "cyclic_frames" in endpoint and not isinstance(endpoint["cyclic_frames"], list):
        raise ValueError(f"endpoints[{index}].cyclic_frames must be list")


def load_send_config(config_path: Path) -> dict:
    """Load sender YAML and validate schema."""
    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {config_path}: {exc}") from exc

    if "endpoints" not in config:
        raise ValueError("config missing required key: endpoints")
    if not isinstance(config["endpoints"], list) or not config["endpoints"]:
        raise ValueError("endpoints must be a non-empty list")

    names = set()
    for index, endpoint in enumerate(config["endpoints"], start=1):
        _validate_endpoint_config(index, endpoint)
        name = endpoint["name"].strip()
        if name in names:
            raise ValueError(f"duplicate endpoint name: {name}")
        names.add(name)

    if "verbose" in config and not isinstance(config["verbose"], bool):
        raise ValueError("verbose must be bool")

    return config


def send_oneshot_frames(
    sock: socket.socket,
    endpoint_name: str,
    ip: str,
    port: int,
    frames: List[str],
    dry_run: bool,
) -> int:
    """Send one-shot frame list to one endpoint exactly once."""
    sent = 0
    for line_no, frame_text in enumerate(frames, start=1):
        frame_text = str(frame_text).strip()
        if not frame_text:
            continue

        try:
            payload = parse_and_encode(frame_text)
        except (ValueError, FrameFormatError) as exc:
            LOGGER.warning("[%s] skip oneshot frame %s: %s | %s", endpoint_name, line_no, exc, frame_text)
            continue

        if dry_run:
            LOGGER.info(
                "[DRY-RUN][%s] oneshot -> %s:%s frame=%s bytes=%s",
                endpoint_name,
                ip,
                port,
                frame_text,
                payload.hex(" ").upper(),
            )
        else:
            sock.sendto(payload, (ip, port))
            LOGGER.info("[%s] oneshot -> %s:%s frame=%s", endpoint_name, ip, port, frame_text)
        sent += 1
    return sent


def build_cyclic_tasks(config: dict) -> List[CyclicFrameTask]:
    """Create validated cyclic tasks from endpoint config entries."""
    tasks: List[CyclicFrameTask] = []
    for endpoint in config["endpoints"]:
        endpoint_name = endpoint["name"].strip()
        send_mode = endpoint.get("send_mode", "both")
        if send_mode not in ("cyclic", "both"):
            continue

        for i, item in enumerate(endpoint.get("cyclic_frames", []), start=1):
            if not isinstance(item, dict):
                LOGGER.warning("[%s] cyclic_frames[%s] is not map, skipped", endpoint_name, i)
                continue

            frame_text = str(item.get("frame", "")).strip()
            period_ms = item.get("period_ms", 10)
            if not frame_text:
                LOGGER.warning("[%s] cyclic_frames[%s] empty frame, skipped", endpoint_name, i)
                continue
            if not isinstance(period_ms, int) or period_ms <= 0:
                LOGGER.warning("[%s] cyclic_frames[%s] invalid period_ms=%s", endpoint_name, i, period_ms)
                continue

            tasks.append(
                CyclicFrameTask(
                    endpoint_name=endpoint_name,
                    ip=endpoint["ip"].strip(),
                    port=endpoint["port"],
                    frame_text=frame_text,
                    period_ms=period_ms,
                )
            )
    return tasks


def cyclic_sender_thread(
    sock: socket.socket,
    stop_event: threading.Event,
    task: CyclicFrameTask,
    dry_run: bool,
) -> None:
    """Run one cyclic task until stop signal."""
    try:
        payload = parse_and_encode(task.frame_text)
    except (ValueError, FrameFormatError) as exc:
        LOGGER.error("[%s] invalid cyclic frame: %s | %s", task.endpoint_name, exc, task.frame_text)
        return

    period_seconds = task.period_ms / 1000.0
    next_send_time = time.monotonic()
    count = 0

    LOGGER.info(
        "[%s] cyclic start -> %s:%s frame=%s period=%sms",
        task.endpoint_name,
        task.ip,
        task.port,
        task.frame_text,
        task.period_ms,
    )

    while not stop_event.is_set():
        if dry_run:
            if count == 0:
                LOGGER.info(
                    "[DRY-RUN][%s] cyclic sample -> %s:%s bytes=%s",
                    task.endpoint_name,
                    task.ip,
                    task.port,
                    payload.hex(" ").upper(),
                )
        else:
            sock.sendto(payload, (task.ip, task.port))

        count += 1
        if count % 200 == 0:
            LOGGER.info("[%s] cyclic sent=%s", task.endpoint_name, count)

        next_send_time += period_seconds
        sleep_time = next_send_time - time.monotonic()
        if sleep_time > 0:
            stop_event.wait(sleep_time)
        else:
            next_send_time = time.monotonic()

    LOGGER.info("[%s] cyclic stop total=%s", task.endpoint_name, count)


def run_sender(config_path: Path, dry_run: bool) -> None:
    """Run one-shot sends and cyclic send workers from config."""
    config = load_send_config(config_path)
    configure_logging(bool(config.get("verbose", False)))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    try:
        for endpoint in config["endpoints"]:
            send_mode = endpoint.get("send_mode", "both")
            if send_mode in ("oneshot", "both"):
                sent = send_oneshot_frames(
                    sock=sock,
                    endpoint_name=endpoint["name"].strip(),
                    ip=endpoint["ip"].strip(),
                    port=endpoint["port"],
                    frames=endpoint.get("oneshot_frames", []),
                    dry_run=dry_run,
                )
                LOGGER.info("[%s] oneshot completed sent=%s", endpoint["name"], sent)

        cyclic_tasks = build_cyclic_tasks(config)
        for idx, task in enumerate(cyclic_tasks, start=1):
            thread = threading.Thread(
                target=cyclic_sender_thread,
                args=(sock, stop_event, task, dry_run),
                daemon=False,
                name=f"cyclic-{idx}",
            )
            thread.start()
            threads.append(thread)

        if not threads:
            LOGGER.info("no cyclic tasks configured, sender exits")
            return

        LOGGER.info("cyclic senders started (%s threads), press Ctrl-C to stop", len(threads))
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
    """Build command-line argument parser."""
    parser = argparse.ArgumentParser(description="CAN UDP sender (multi-endpoint, oneshot+cyclic)")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="sender yaml config path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse/encode/log only; do not send UDP packets",
    )
    return parser


def main() -> None:
    """Program entry."""
    parser = build_arg_parser()
    args = parser.parse_args()
    run_sender(args.config.resolve(), args.dry_run)


if __name__ == "__main__":
    main()
