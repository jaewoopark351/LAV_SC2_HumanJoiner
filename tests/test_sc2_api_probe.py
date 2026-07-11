from __future__ import annotations

import socket
import threading
import unittest

from sc2_api_probe import SC2_API_PING_FIELD_KEY, ping_sc2_api_once


class SC2ApiProbeTest(unittest.TestCase):
    def test_ping_sc2_api_once_accepts_websocket_binary_ping_response(self) -> None:
        ready = threading.Event()
        port_box: list[int] = []

        def server() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                sock.listen(1)
                port_box.append(sock.getsockname()[1])
                ready.set()
                conn, _ = sock.accept()
                with conn:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        data += conn.recv(4096)
                    conn.sendall(
                        b"HTTP/1.1 101 Switching Protocols\r\n"
                        b"Upgrade: websocket\r\n"
                        b"Connection: Upgrade\r\n"
                        b"\r\n"
                    )
                    header = conn.recv(2)
                    self.assertEqual(0x82, header[0])
                    length = header[1] & 0x7F
                    mask = conn.recv(4)
                    payload = conn.recv(length)
                    unmasked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
                    self.assertIn(SC2_API_PING_FIELD_KEY, unmasked)
                    conn.sendall(b"\x82\x03" + SC2_API_PING_FIELD_KEY + b"\x00")

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=2.0))

        result = ping_sc2_api_once("127.0.0.1", port_box[0], timeout_sec=2.0)

        self.assertTrue(result.ok, result.error)


if __name__ == "__main__":
    unittest.main()
