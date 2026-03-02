import os
import socket
import binascii
import time
import yaml
import threading
import sys

# Global lock for thread-safe file writing
file_lock = threading.Lock()

def parse_can_id(data):
    """
    Parse CAN message ID from data bytes 2-5 (indices 1-4, big-endian).
    """
    if len(data) < 5:
        raise ValueError("Data too short to parse CAN ID, expected at least 5 bytes")
    can_id = int.from_bytes(data[1:5], byteorder='big')  # Bytes 2-5 (indices 1-4)
    return can_id

def save_to_file(data, file_path, start_time, bus_number):
    """
    Save parsed CAN data to the specified file in ASC format with specified bus number.
    """
    if start_time is None:
        raise ValueError("start_time not set")
    if bus_number not in (1, 2):
        raise ValueError(f"Invalid bus_number: {bus_number}. Must be 1 or 2.")
    if len(data) < 5:  # Minimum length for header + ID
        raise ValueError("Data too short for CAN frame")

    # Extract data length from the first byte (bit3-bit0)
    data_length = data[0] & 0x0F
    if data_length > 8 or data_length < 0:  # Typical CAN DLC range is 0-8
        raise ValueError(f"Invalid data length: {data_length}. Expected 0-8.")

    can_id = parse_can_id(data)
    can_id_str = hex(can_id)[2:].upper() + 'X' if can_id > 2047 else hex(can_id)[2:].upper()

    # Calculate the end index based on data length, starting after ID (byte 5)
    end_index = 5 + data_length
    if len(data) < end_index:
        raise ValueError(f"Data too short for specified length {data_length}, got {len(data) - 5} bytes")

    # Extract and format the data (starting after the 4-byte ID)
    can_data_hex = binascii.hexlify(data[5:end_index]).decode('utf-8').upper()
    data_str = ' '.join(can_data_hex[i:i+2] for i in range(0, len(can_data_hex), 2))

    with file_lock:  # Ensure thread-safe file access
        try:
            with open(file_path, "a") as file:
                # Write header if file is empty
                if os.path.getsize(file_path) == 0:
                    current_time = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime())
                    file.write(f"date {current_time}\n")
                    file.write("base hex  timestamps absolute\n")

                # Calculate relative timestamp
                absolute_time = time.time() - start_time
                formatted_time = '{:0.6f}'.format(absolute_time)

                # Write the message line with dynamic data length
                file.write(f"{formatted_time} {bus_number} {can_id_str} Tx d {data_length} {data_str}\n")
        except IOError as e:
            print(f"Error writing to file {file_path}: {e}")

def receiver_thread(local_ip, port, file_path, bus_number):
    """
    Thread function to receive UDP packets on a specific port and save to file.
    """
    print(f"Starting receiver on {local_ip}:{port}, saving to {file_path} with bus {bus_number}")

    try:
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((local_ip, port))
        print(f"Bound to {local_ip}:{port}")

        # Wait for first packet to set start_time
        start_time = None
        while start_time is None:
            try:
                data, addr = sock.recvfrom(1024)
                start_time = time.time()
                print(f"First packet received on port {port} from {addr}")
                # Save the first packet
                save_to_file(data, file_path, start_time, bus_number)
            except socket.error as e:
                print(f"Error receiving first packet on port {port}: {e}")
                time.sleep(1)  # Retry after delay

        count = 1  # Start from 1 since first packet is saved
        print(f"Logging on port {port}. Press Ctrl-C to stop.")

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                save_to_file(data, file_path, start_time, bus_number)
                count += 1
                if count % 1000 == 0:  # Print every 1000 messages to avoid spam
                    print(f"Port {port}: Received {count} messages so far.")
            except ValueError as ve:
                print(f"Port {port}: Skipping invalid packet from {addr}: {ve}")
            except socket.error as se:
                print(f"Port {port}: Socket error: {se}")
                time.sleep(1)  # Retry after delay

    except socket.error as be:
        print(f"Failed to bind on {local_ip}:{port}: {be}")
    except Exception as e:
        print(f"Unexpected error in receiver on port {port}: {e}")
    finally:
        sock.close()
        print(f"Receiver on port {port} stopped.")

def main():
    # Load configuration
    try:
        with open("config.yaml", "r") as config_file:
            config = yaml.safe_load(config_file)
    except FileNotFoundError:
        print("config.yaml not found. Please create it with the required format.")
        sys.exit(1)
    except yaml.YAMLError as ye:
        print(f"Error parsing config.yaml: {ye}")
        sys.exit(1)

    local_ip = config.get('local_ip')
    ports_config = config.get('ports')

    if not local_ip:
        print("Missing 'local_ip' in config.")
        sys.exit(1)
    if not isinstance(ports_config, list) or not ports_config:
        print("Missing or invalid 'ports' list in config.")
        sys.exit(1)

    # Generate unique file name based on current system time
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"can_data_{timestamp}.asc"

    # Create csv directory if it doesn't exist
    os.makedirs("csv", exist_ok=True)
    file_path = os.path.join("csv", file_name)

    print(f"New log file created: {file_path}")

    # Start a thread for each port
    threads = []
    for pconf in ports_config:
        port = pconf.get('port')
        bus_number = pconf.get('bus_number')
        if not isinstance(port, int):
            print(f"Invalid port number: {port}")
            continue
        if bus_number not in (1, 2):
            print(f"Invalid bus_number for port {port}: {bus_number}. Must be 1 or 2.")
            continue
        thread = threading.Thread(target=receiver_thread, args=(local_ip, port, file_path, bus_number), daemon=True)
        thread.start()
        threads.append(thread)

    # Wait for threads
    print("All receivers started. Press Ctrl-C to stop.")
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)

if __name__ == "__main__":
    main()