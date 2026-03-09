"""Simple UDP test sender for local receiver debugging."""

import logging
import socket
import time


TARGET_IP = "127.0.0.1"
SEND_INTERVAL_SECONDS = 0.1
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOGGER = logging.getLogger("ethernet2can.sender")
TEST_FRAMES = (
    (8001, bytes((0x04, 0x00, 0x00, 0x01, 0xA2, 0x01, 0x23, 0x45, 0x67))),
    (8002, bytes((0x08, 0x00, 0x00, 0x01, 0xA3, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0))),
    (8001, bytes((0x02, 0x00, 0x00, 0x07, 0xFF, 0xAB, 0xCD))),
)


def configure_logging() -> None:
    """Initialize logging output for test sender."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def describe_frame(frame: bytes):
    """Decode frame fields for readable log output."""
    dlc = frame[0] & 0x0F
    can_id = int.from_bytes(frame[1:5], byteorder="big")
    payload = " ".join(f"{byte:02X}" for byte in frame[5:])
    return dlc, can_id, payload


def main() -> None:
    """Send predefined test frames to local UDP ports."""
    configure_logging()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        for index, (port, frame) in enumerate(TEST_FRAMES, start=1):
            dlc, can_id, payload = describe_frame(frame)
            sock.sendto(frame, (TARGET_IP, port))
            LOGGER.info(
                "[%s] sent to %s:%s dlc=%s can_id=0x%s data=%s",
                index,
                TARGET_IP,
                port,
                dlc,
                f"{can_id:X}",
                payload or "(empty)",
            )
            time.sleep(SEND_INTERVAL_SECONDS)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
