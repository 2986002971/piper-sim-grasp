#!/usr/bin/env python3
"""Forward one UDP endpoint through an authenticated TLS/TCP connection.

This is a small experiment for networks that expose a TCP port but block UDP.
It is intended for one WebRTC peer and one fixed local UDP target.
"""

from __future__ import annotations

import argparse
import hmac
import json
import select
import socket
import ssl
import struct
import threading
import time
import zlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAGIC = b"PIPERUOT1"
MAX_PACKET = 65507


def endpoint(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def recv_exact(sock: ssl.SSLSocket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        try:
            part = sock.recv(count - len(data))
        except (TimeoutError, socket.timeout):
            continue
        if not part:
            raise ConnectionError("TCP tunnel closed")
        data.extend(part)
    return bytes(data)


def send_frame(sock: ssl.SSLSocket, lock: threading.Lock, payload: bytes) -> None:
    if len(payload) > MAX_PACKET:
        return
    frame = struct.pack("!H", len(payload)) + payload
    with lock:
        sock.sendall(frame)


def stun_transaction_id(payload: bytes) -> bytes | None:
    """Return the RFC 5389 transaction ID for a complete STUN message."""
    if len(payload) < 20 or payload[0] & 0xC0 or payload[4:8] != b"\x21\x12\xa4\x42":
        return None
    message_length = struct.unpack("!H", payload[2:4])[0]
    if message_length + 20 > len(payload):
        return None
    return payload[8:20]


def stun_summary(payload: bytes) -> str | None:
    """Describe a STUN packet without exposing ICE credentials."""
    transaction_id = stun_transaction_id(payload)
    if transaction_id is None:
        return None
    message_type = struct.unpack("!H", payload[:2])[0]
    message_class = ((message_type >> 4) & 1) | ((message_type >> 7) & 2)
    class_name = ("request", "indication", "success", "error")[message_class]
    error_code = None
    xor_mapped_address = None
    offset = 20
    end = min(len(payload), 20 + struct.unpack("!H", payload[2:4])[0])
    while offset + 4 <= end:
        attribute, size = struct.unpack("!HH", payload[offset:offset + 4])
        value_start = offset + 4
        if attribute == 0x0009 and size >= 4:
            error_code = ((payload[value_start + 2] & 0x07) * 100
                          + payload[value_start + 3])
        elif attribute == 0x0020 and size >= 8:
            family = payload[value_start + 1]
            port = struct.unpack("!H", payload[value_start + 2:value_start + 4])[0] ^ 0x2112
            if family == 1 and size >= 8:
                address = bytes(a ^ b for a, b in zip(payload[value_start + 4:value_start + 8], payload[4:8]))
                xor_mapped_address = f"{socket.inet_ntop(socket.AF_INET, address)}:{port}"
            elif family == 2 and size >= 20:
                mask = payload[4:8] + transaction_id
                address = bytes(a ^ b for a, b in zip(payload[value_start + 4:value_start + 20], mask))
                xor_mapped_address = f"[{socket.inet_ntop(socket.AF_INET6, address)}]:{port}"
        offset = value_start + size + (size % 4)
    suffix = f" error={error_code}" if error_code is not None else ""
    if xor_mapped_address is not None:
        suffix += f" xor-mapped={xor_mapped_address}"
    return f"{class_name} type=0x{message_type:04x} tx={transaction_id.hex()}{suffix}"


def rewrite_stun_xor_mapped(payload: bytes, address: tuple[str, int],
                            ice_password: str | None) -> bytes:
    """Restore the browser-side mapped address and re-sign an ICE STUN success."""
    transaction_id = stun_transaction_id(payload)
    if transaction_id is None or ice_password is None:
        return payload
    message_type = struct.unpack("!H", payload[:2])[0]
    message_class = ((message_type >> 4) & 1) | ((message_type >> 7) & 2)
    if message_class != 2:
        return payload

    data = bytearray(payload)
    message_end = 20 + struct.unpack("!H", data[2:4])[0]
    xor_value_start = None
    integrity_offset = None
    integrity_value_start = None
    fingerprint_offset = None
    offset = 20
    while offset + 4 <= message_end:
        attribute, size = struct.unpack("!HH", data[offset:offset + 4])
        value_start = offset + 4
        if attribute == 0x0020 and size >= 8:
            xor_value_start = value_start
        elif attribute == 0x0008 and size == 20:
            integrity_offset = offset
            integrity_value_start = value_start
        elif attribute == 0x8028 and size == 4:
            fingerprint_offset = offset
        offset = value_start + size + (size % 4)
    if xor_value_start is None or integrity_offset is None or integrity_value_start is None:
        return payload

    host, port = address
    try:
        ip_bytes = socket.inet_aton(host)
    except OSError:
        return payload
    data[xor_value_start:xor_value_start + 8] = (
        b"\x00\x01"
        + struct.pack("!H", port ^ 0x2112)
        + bytes(a ^ b for a, b in zip(ip_bytes, data[4:8]))
    )

    integrity_input = bytearray(data[:integrity_offset])
    integrity_input[2:4] = struct.pack("!H", integrity_offset + 24 - 20)
    digest = hmac.new(ice_password.encode("ascii"), integrity_input, "sha1").digest()
    data[integrity_value_start:integrity_value_start + 20] = digest

    if fingerprint_offset is not None:
        fingerprint_value_start = fingerprint_offset + 4
        fingerprint = zlib.crc32(data[:fingerprint_offset]) ^ 0x5354554E
        data[fingerprint_value_start:fingerprint_value_start + 4] = struct.pack("!I", fingerprint)
    return bytes(data)


def ice_password_cors_headers(origin: str | None) -> list[tuple[str, str]]:
    if origin in {"http://127.0.0.1:5173", "http://127.0.0.1:5174"}:
        return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin")]
    return []


def start_ice_password_server(password: list[str | None], lock: threading.Lock) -> None:

    class Handler(BaseHTTPRequestHandler):
        def allow_origin(self) -> None:
            for key, value in ice_password_cors_headers(self.headers.get("Origin")):
                self.send_header(key, value)

        def do_OPTIONS(self) -> None:
            if self.path != "/ice-password":
                self.send_error(404)
                return
            self.send_response(204)
            self.allow_origin()
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/ice-password":
                self.send_error(404)
                return
            length = min(int(self.headers.get("Content-Length", "0")), 256)
            value = self.rfile.read(length).decode("ascii", errors="ignore").strip()
            if not 16 <= len(value) <= 256:
                self.send_error(400)
                return
            with lock:
                password[0] = value
            self.send_response(204)
            self.allow_origin()
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 16009), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def report_stats(label: str, counters: dict[str, int], stop: threading.Event) -> None:
    while not stop.wait(2.0):
        snapshot = ", ".join(f"{key}={value}" for key, value in counters.items())
        print(f"{label} UDP relay: {snapshot}", flush=True)


def tcp_to_udp(sock: ssl.SSLSocket, udp: socket.socket, target: tuple[str, int],
               stop: threading.Event, counters: dict[str, int] | None = None) -> None:
    try:
        while not stop.is_set():
            size = struct.unpack("!H", recv_exact(sock, 2))[0]
            if size:
                payload = recv_exact(sock, size)
                udp.sendto(payload, target)
                if counters is not None:
                    counters["tcp_to_isaac_packets"] += 1
                    counters["tcp_to_isaac_bytes"] += len(payload)
    except (ConnectionError, OSError, ssl.SSLError):
        stop.set()


def udp_to_tcp(udp: socket.socket, sock: ssl.SSLSocket,
               lock: threading.Lock, stop: threading.Event,
               counters: dict[str, int] | None = None) -> None:
    try:
        while not stop.is_set():
            readable, _, _ = select.select([udp], [], [], 0.5)
            if readable:
                payload, _ = udp.recvfrom(MAX_PACKET)
                if counters is not None:
                    counters["isaac_to_tcp_packets"] += 1
                    counters["isaac_to_tcp_bytes"] += len(payload)
                send_frame(sock, lock, payload)
    except (ConnectionError, OSError, ssl.SSLError):
        stop.set()


def run_relay(sock: ssl.SSLSocket, target: tuple[str, int], local_addr: str) -> None:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(endpoint(local_addr))
    stop = threading.Event()
    lock = threading.Lock()
    counters = {"tcp_to_isaac_packets": 0, "tcp_to_isaac_bytes": 0,
                "isaac_to_tcp_packets": 0, "isaac_to_tcp_bytes": 0}
    readers = [
        threading.Thread(target=tcp_to_udp, args=(sock, udp, target, stop, counters), daemon=True),
        threading.Thread(target=udp_to_tcp, args=(udp, sock, lock, stop, counters), daemon=True),
        threading.Thread(target=report_stats, args=("server", counters, stop), daemon=True),
    ]
    for thread in readers:
        thread.start()
    while not stop.wait(0.5):
        if not any(thread.is_alive() for thread in readers):
            break
    stop.set()
    udp.close()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def run_client_relay(sock: ssl.SSLSocket, local_addr: str,
                     ice_password: list[str | None], password_lock: threading.Lock) -> None:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(endpoint(local_addr))
    stop = threading.Event()
    lock = threading.Lock()
    peer_lock = threading.Lock()
    peer: list[tuple[str, int] | None] = [None]
    transaction_peers: dict[bytes, tuple[tuple[str, int], float]] = {}
    counters = {"browser_to_tcp_packets": 0, "browser_to_tcp_bytes": 0,
                "tcp_to_browser_packets": 0, "tcp_to_browser_bytes": 0,
                "stun_transactions": 0, "stun_responses_matched": 0,
                "stun_responses_unmatched": 0, "stun_debug_lines": 0}

    def browser_to_tcp() -> None:
        try:
            while not stop.is_set():
                readable, _, _ = select.select([udp], [], [], 0.5)
                if readable:
                    payload, address = udp.recvfrom(MAX_PACKET)
                    transaction_id = stun_transaction_id(payload)
                    summary = stun_summary(payload)
                    with peer_lock:
                        peer[0] = address
                        if transaction_id is not None:
                            transaction_peers[transaction_id] = (address, time.monotonic())
                            counters["stun_transactions"] += 1
                        cutoff = time.monotonic() - 60.0
                        for expired in [key for key, (_, timestamp) in transaction_peers.items()
                                        if timestamp < cutoff]:
                            del transaction_peers[expired]
                    if summary is not None and counters["stun_debug_lines"] < 120:
                        print(f"browser UDP {address}: {summary}", flush=True)
                        counters["stun_debug_lines"] += 1
                    send_frame(sock, lock, payload)
                    counters["browser_to_tcp_packets"] += 1
                    counters["browser_to_tcp_bytes"] += len(payload)
        except (ConnectionError, OSError, ssl.SSLError):
            stop.set()

    def tcp_to_browser() -> None:
        try:
            while not stop.is_set():
                size = struct.unpack("!H", recv_exact(sock, 2))[0]
                payload = recv_exact(sock, size) if size else b""
                summary = stun_summary(payload)
                with peer_lock:
                    transaction_id = stun_transaction_id(payload)
                    # Isaac may retransmit the same STUN response. Keep the
                    # transaction mapping until expiry so every copy is rewritten.
                    transaction_peer = transaction_peers.get(transaction_id) if transaction_id is not None else None
                    if transaction_peer is not None:
                        address = transaction_peer[0]
                        counters["stun_responses_matched"] += 1
                    else:
                        address = peer[0]
                        if transaction_id is not None:
                            counters["stun_responses_unmatched"] += 1
                if transaction_peer is not None:
                    with password_lock:
                        key = ice_password[0]
                    rewritten = rewrite_stun_xor_mapped(payload, address, key)
                    if rewritten != payload:
                        payload = rewritten
                        summary = stun_summary(payload)
                        print(f"rewrote STUN mapped address for {address}: {summary}", flush=True)
                if summary is not None and counters["stun_debug_lines"] < 120:
                    print(f"Isaac UDP -> browser {address}: {summary}", flush=True)
                    counters["stun_debug_lines"] += 1
                if address is not None and payload:
                    udp.sendto(payload, address)
                    counters["tcp_to_browser_packets"] += 1
                    counters["tcp_to_browser_bytes"] += len(payload)
        except (ConnectionError, OSError, ssl.SSLError):
            stop.set()

    readers = [
        threading.Thread(target=browser_to_tcp, daemon=True),
        threading.Thread(target=tcp_to_browser, daemon=True),
        threading.Thread(target=report_stats, args=("client", counters, stop), daemon=True),
    ]
    for thread in readers:
        thread.start()
    while not stop.wait(0.5):
        if not any(thread.is_alive() for thread in readers):
            break
    stop.set()
    udp.close()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def load_token(path: str) -> bytes:
    token = Path(path).read_bytes().strip()
    if len(token) < 32:
        raise ValueError("token file must contain at least 32 bytes of random data")
    return token


def server(args: argparse.Namespace) -> None:
    token = load_token(args.token_file)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    listener = socket.create_server(endpoint(args.bind), reuse_port=False)
    print(f"TLS UDP tunnel listening on {args.bind}, target={args.target}", flush=True)
    while True:
        raw, address = listener.accept()
        try:
            with context.wrap_socket(raw, server_side=True) as conn:
                conn.settimeout(1.0)
                magic = recv_exact(conn, len(MAGIC))
                size = struct.unpack("!H", recv_exact(conn, 2))[0]
                supplied = recv_exact(conn, size)
                if magic != MAGIC or not hmac.compare_digest(supplied, token):
                    conn.sendall(b"NO")
                    print(f"rejected tunnel authentication from {address[0]}", flush=True)
                    continue
                conn.sendall(b"OK")
                conn.settimeout(None)
                print(f"authenticated tunnel from {address[0]}", flush=True)
                run_relay(conn, endpoint(args.target), args.udp_bind)
        except (ConnectionError, OSError, ssl.SSLError) as exc:
            print(f"tunnel ended: {exc}", flush=True)


def client(args: argparse.Namespace) -> None:
    token = load_token(args.token_file)
    context = ssl.create_default_context(cafile=args.ca_cert)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    ice_password: list[str | None] = [None]
    password_lock = threading.Lock()
    start_ice_password_server(ice_password, password_lock)
    while True:
        try:
            raw = socket.create_connection(endpoint(args.remote), timeout=15)
            with context.wrap_socket(raw, server_hostname=args.server_name) as conn:
                conn.settimeout(1.0)
                conn.sendall(MAGIC + struct.pack("!H", len(token)) + token)
                if recv_exact(conn, 2) != b"OK":
                    raise PermissionError("server rejected tunnel credentials")
                # The authentication timeout must not abort media writes during
                # a short TCP backpressure pause and force a new UDP endpoint.
                conn.settimeout(None)
                print(f"authenticated tunnel to {args.remote}; UDP {args.udp_bind}", flush=True)
                run_client_relay(conn, args.udp_bind, ice_password, password_lock)
        except PermissionError:
            raise
        except (ConnectionError, OSError, ssl.SSLError) as exc:
            print(f"tunnel reconnect after: {exc}", flush=True)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    srv = sub.add_parser("server")
    srv.add_argument("--bind", default="127.0.0.1:6008")
    srv.add_argument("--target", default="127.0.0.1:47998")
    srv.add_argument("--udp-bind", default="127.0.0.1:0")
    srv.add_argument("--cert", required=True)
    srv.add_argument("--key", required=True)
    srv.add_argument("--token-file", required=True)
    srv.set_defaults(run=server)
    cli = sub.add_parser("client")
    cli.add_argument("--remote", required=True)
    cli.add_argument("--udp-bind", default="127.0.0.1:47998")
    cli.add_argument("--server-name", default="piper-udp-tunnel")
    cli.add_argument("--ca-cert", required=True)
    cli.add_argument("--token-file", required=True)
    cli.set_defaults(run=client)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
