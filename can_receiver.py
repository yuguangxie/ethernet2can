from pathlib import Path
import binascii
import logging
import socket
import threading
import time
import sys

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
OUTPUT_DIR = BASE_DIR / "csv"
RECV_BUFFER_SIZE = 1024
SOCKET_TIMEOUT_SECONDS = 1.0
PROGRESS_INTERVAL = 1000
LOG_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
LOGGER = logging.getLogger("ethernet2can.receiver")

file_lock = threading.Lock()


def configure_logging():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def parse_can_id(data):
    if len(data) < 5:
        raise ValueError("data too short to parse CAN ID, expected at least 5 bytes")
    return int.from_bytes(data[1:5], byteorder="big")


def parse_can_frame(data):
    if len(data) < 5:
        raise ValueError("data too short for CAN frame")

    data_length = data[0] & 0x0F
    if not 0 <= data_length <= 8:
        raise ValueError(f"invalid data length: {data_length}, expected 0-8")

    end_index = 5 + data_length
    if len(data) < end_index:
        raise ValueError(
            f"data too short for specified length {data_length}, got {len(data) - 5} bytes"
        )

    can_id = parse_can_id(data)
    can_id_str = f"{can_id:X}X" if can_id > 0x7FF else f"{can_id:X}"
    payload_hex = binascii.hexlify(data[5:end_index]).decode("ascii").upper()
    payload = " ".join(payload_hex[i : i + 2] for i in range(0, len(payload_hex), 2))
    return can_id_str, data_length, payload


def save_to_file(data, file_path, start_time, bus_number):
    if start_time is None:
        raise ValueError("start_time not set")
    if not isinstance(bus_number, int) or bus_number <= 0:
        raise ValueError(f"invalid bus_number: {bus_number}, expected a positive integer")

    can_id_str, data_length, data_str = parse_can_frame(data)
    relative_time = time.time() - start_time

    with file_lock:
        with file_path.open("a", encoding="ascii", newline="\n") as file:
            if file_path.stat().st_size == 0:
                current_time = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(start_time))
                file.write(f"date {current_time}\n")
                file.write("base hex  timestamps absolute\n")

            line = f"{relative_time:0.6f} {bus_number} {can_id_str} Tx d {data_length}"
            if data_str:
                line = f"{line} {data_str}"
            file.write(f"{line}\n")


def receiver_thread(local_ip, port, file_path, bus_number, start_time, stop_event):
    sock = None
    count = 0
    first_packet_logged = False

    LOGGER.info(
        "starting receiver on %s:%s, writing to %s with bus %s",
        local_ip,
        port,
        file_path,
        bus_number,
    )

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        sock.bind((local_ip, port))
        LOGGER.info("bound receiver to %s:%s", local_ip, port)

        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(RECV_BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError as exc:
                if stop_event.is_set():
                    break
                LOGGER.error("port %s socket error: %s", port, exc)
                time.sleep(1)
                continue

            if not first_packet_logged:
                LOGGER.info("first packet received on port %s from %s", port, addr)
                first_packet_logged = True

            try:
                save_to_file(data, file_path, start_time, bus_number)
            except ValueError as exc:
                LOGGER.warning("port %s skipping invalid packet from %s: %s", port, addr, exc)
                continue
            except OSError as exc:
                LOGGER.error("port %s failed to write packet: %s", port, exc)
                continue

            count += 1
            if count % PROGRESS_INTERVAL == 0:
                LOGGER.info("port %s received %s messages so far", port, count)

    except OSError as exc:
        LOGGER.error("failed to bind on %s:%s: %s", local_ip, port, exc)
    finally:
        if sock is not None:
            sock.close()
        LOGGER.info("receiver on port %s stopped", port)


def load_config():
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
    except FileNotFoundError:
        LOGGER.error("%s not found. Please create it with the required format.", CONFIG_PATH.name)
        sys.exit(1)
    except yaml.YAMLError as exc:
        LOGGER.error("error parsing %s: %s", CONFIG_PATH.name, exc)
        sys.exit(1)

    local_ip = config.get("local_ip")
    ports_config = config.get("ports")

    if not isinstance(local_ip, str) or not local_ip.strip():
        LOGGER.error("missing or invalid 'local_ip' in config")
        sys.exit(1)
    if not isinstance(ports_config, list) or not ports_config:
        LOGGER.error("missing or invalid 'ports' list in config")
        sys.exit(1)

    validated_ports = []
    seen_ports = set()
    for entry in ports_config:
        if not isinstance(entry, dict):
            LOGGER.error("invalid port entry: %r", entry)
            sys.exit(1)

        port = entry.get("port")
        bus_number = entry.get("bus_number")

        if not isinstance(port, int) or not 1 <= port <= 65535:
            LOGGER.error("invalid port number: %s", port)
            sys.exit(1)
        if port in seen_ports:
            LOGGER.error("duplicate port in config: %s", port)
            sys.exit(1)
        if not isinstance(bus_number, int) or bus_number <= 0:
            LOGGER.error(
                "invalid bus_number for port %s: %s. Must be a positive integer.",
                port,
                bus_number,
            )
            sys.exit(1)

        seen_ports.add(port)
        validated_ports.append((port, bus_number))

    return local_ip.strip(), validated_ports


def main():
    configure_logging()
    local_ip, ports_config = load_config()

    OUTPUT_DIR.mkdir(exist_ok=True)
    start_time = time.time()
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(start_time))
    file_path = OUTPUT_DIR / f"can_data_{timestamp}.asc"
    file_path.touch(exist_ok=False)
    LOGGER.info("new log file created: %s", file_path)

    stop_event = threading.Event()
    threads = []

    for port, bus_number in ports_config:
        thread = threading.Thread(
            target=receiver_thread,
            args=(local_ip, port, file_path, bus_number, start_time, stop_event),
            daemon=False,
            name=f"udp-{port}",
        )
        thread.start()
        threads.append(thread)

    LOGGER.info("all receivers started. Press Ctrl-C to stop.")

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        LOGGER.info("shutdown signal received, stopping receivers")
        stop_event.set()
        for thread in threads:
            thread.join()


if __name__ == "__main__":
    main()
