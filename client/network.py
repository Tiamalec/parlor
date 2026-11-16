"""Threaded TCP transport used by the Tkinter chat client.

The wire format is a four-byte unsigned big-endian payload length followed by a
UTF-8 JSON object.  This module deliberately has no Tkinter dependency: network
events are delivered through a thread-safe queue and consumed by the GUI thread.
"""

from __future__ import annotations

import json
import queue
import socket
import struct
import threading
import time
import uuid
from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = 1
MAX_FRAME_SIZE = 4 * 1024 * 1024
HEARTBEAT_INTERVAL = 20.0
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 10.0)


class ProtocolError(ValueError):
    """Raised when a received frame violates the client protocol."""


def make_envelope(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    if not event_type:
        raise ValueError("event_type must not be empty")
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "type": event_type,
        "timestamp": int(time.time() * 1000),
        "payload": dict(payload or {}),
    }


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """Encode one protocol envelope into a length-prefixed frame."""
    body = json.dumps(
        dict(message), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(body) > MAX_FRAME_SIZE:
        raise ProtocolError(f"frame exceeds {MAX_FRAME_SIZE} bytes")
    return struct.pack(">I", len(body)) + body


class FrameDecoder:
    """Incrementally decode arbitrarily split or coalesced TCP frames."""

    def __init__(self, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self.max_frame_size = max_frame_size
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while len(self._buffer) >= 4:
            length = struct.unpack(">I", self._buffer[:4])[0]
            if length == 0:
                raise ProtocolError("empty frame is not allowed")
            if length > self.max_frame_size:
                raise ProtocolError(
                    f"received frame exceeds {self.max_frame_size} bytes"
                )
            if len(self._buffer) < 4 + length:
                break
            body = bytes(self._buffer[4 : 4 + length])
            del self._buffer[: 4 + length]
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(f"invalid JSON frame: {exc}") from exc
            if not isinstance(value, dict):
                raise ProtocolError("JSON frame must contain an object")
            messages.append(value)
        return messages


class NetworkClient:
    """Own a reconnecting socket in a background thread.

    Local transport events use names beginning with ``network.`` and have the
    same envelope shape as server events.  The GUI must call :meth:`start` once
    and :meth:`close` while shutting down.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._outgoing: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._socket_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="chat-network",
            daemon=True,
        )
        self._thread.start()

    def send(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> str:
        """Queue a server request and return its request ID.

        Requests are accepted only while connected so stale chat messages are
        not replayed before authentication after a reconnect.
        """
        if not self.connected:
            raise ConnectionError("not connected to chat server")
        envelope = make_envelope(event_type, payload, request_id=request_id)
        self._outgoing.put(envelope)
        return str(envelope["request_id"])

    def close(self) -> None:
        self._stop.set()
        self._connected.clear()
        with self._socket_lock:
            sock = self._socket
            self._socket = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        self.events.put(make_envelope(event_type, payload))

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                sock.settimeout(0.25)
                with self._socket_lock:
                    self._socket = sock
                self._connected.set()
                attempt = 0
                self._emit(
                    "network.connected", {"host": self.host, "port": self.port}
                )
                self._connected_loop(sock)
            except (OSError, ProtocolError) as exc:
                if not self._stop.is_set():
                    self._emit("network.error", {"message": str(exc)})
            finally:
                was_connected = self._connected.is_set()
                self._connected.clear()
                with self._socket_lock:
                    sock = self._socket
                    self._socket = None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                if was_connected and not self._stop.is_set():
                    self._emit("network.disconnected", {})
            if self._stop.is_set():
                break
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            self._emit("network.reconnecting", {"delay": delay, "attempt": attempt})
            self._stop.wait(delay)

    def _connected_loop(self, sock: socket.socket) -> None:
        decoder = FrameDecoder()
        last_heartbeat = time.monotonic()
        while not self._stop.is_set():
            self._flush_outgoing(sock)
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                sock.sendall(encode_frame(make_envelope("heartbeat.ping")))
                last_heartbeat = now
            try:
                chunk = sock.recv(64 * 1024)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("server closed the connection")
            for message in decoder.feed(chunk):
                self.events.put(message)

    def _flush_outgoing(self, sock: socket.socket) -> None:
        while True:
            try:
                message = self._outgoing.get_nowait()
            except queue.Empty:
                return
            try:
                sock.sendall(encode_frame(message))
            except Exception:
                # The caller saw a connected state when queuing the request.  If
                # delivery fails now, surface the request ID instead of replaying
                # it after reconnect, which could duplicate a mutation.
                self._emit(
                    "network.send_failed",
                    {
                        "request_id": message.get("request_id"),
                        "type": message.get("type"),
                    },
                )
                raise
