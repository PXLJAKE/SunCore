# SPDX-License-Identifier: Apache-2.0
"""A tiny in-process MQTT 3.1.1 broker for tests (plan Phase 6: "amqtt or a stub broker").

Supports CONNECT (credentials, will, clean session ignored), SUBSCRIBE with ``+``/``#``,
UNSUBSCRIBE, PUBLISH QoS 0/1 with retained messages, PINGREQ and DISCONNECT. Subscribers get
every message at QoS 0 (a legal downgrade). A connection that ends without DISCONNECT publishes
its will. ``amqtt`` was not used: its latest release is a pre-release that does not resolve
with the project's dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field


@dataclass
class Message:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    sender: str | None = None


def _encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(data: bytes, pos: int) -> tuple[str, int]:
    length = int.from_bytes(data[pos : pos + 2], "big")
    return data[pos + 2 : pos + 2 + length].decode(), pos + 2 + length


def topic_matches(pattern: str, topic: str) -> bool:
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part not in ("+", t[i]):
            return False
    return len(p) == len(t)


@dataclass
class _Conn:
    writer: asyncio.StreamWriter
    client_id: str = ""
    subscriptions: list[str] = field(default_factory=list)
    will: Message | None = None
    clean: bool = False


class StubBroker:
    def __init__(self, *, username: str | None = None, password: str | None = None) -> None:
        self.username = username
        self.password = password
        self.port = 0
        self.published: list[Message] = []
        """Every PUBLISH received from a client, in order (the will included)."""
        self.retained: dict[str, Message] = {}
        self.connects: list[str] = []
        self.refuse_with: int | None = None
        self.subscribed: list[str] = []
        self._server: asyncio.Server | None = None
        self._conns: list[_Conn] = []
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> StubBroker:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        await self.drop_connections()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> StubBroker:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # --- test helpers -----------------------------------------------------------------

    async def drop_connections(self) -> None:
        """Close every client socket without DISCONNECT (the broker publishes their wills)."""
        for conn in list(self._conns):
            conn.writer.close()
        await asyncio.sleep(0.05)

    def messages(self, topic: str) -> list[Message]:
        return [m for m in self.published if m.topic == topic]

    def inject(self, topic: str, payload: str | bytes, *, retain: bool = False) -> None:
        """Deliver a message as if another client had published it."""
        data = payload.encode() if isinstance(payload, str) else payload
        self._route(Message(topic, data, 0, retain))

    async def wait_for(self, predicate: object, seconds: float = 3.0) -> None:
        assert callable(predicate)
        end = asyncio.get_running_loop().time() + seconds
        while not predicate():
            if asyncio.get_running_loop().time() > end:
                raise AssertionError("condition not met in time")
            await asyncio.sleep(0.01)

    # --- protocol ---------------------------------------------------------------------

    def _route(self, message: Message) -> None:
        if message.retain:
            if message.payload:
                self.retained[message.topic] = message
            else:
                self.retained.pop(message.topic, None)
        for conn in list(self._conns):
            if any(topic_matches(s, message.topic) for s in conn.subscriptions):
                self._deliver(conn, message)

    @staticmethod
    def _deliver(conn: _Conn, message: Message, *, retain_flag: bool = False) -> None:
        topic = message.topic.encode()
        body = len(topic).to_bytes(2, "big") + topic + message.payload
        header = 0x30 | (1 if retain_flag else 0)
        conn.writer.write(bytes([header]) + _encode_len(len(body)) + body)

    async def _handle(  # noqa: PLR0912 - one branch per packet type
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        conn = _Conn(writer)
        clean = False
        try:
            while True:
                first = await reader.readexactly(1)
                length, shift = 0, 0
                while True:
                    b = (await reader.readexactly(1))[0]
                    length |= (b & 0x7F) << shift
                    shift += 7
                    if not b & 0x80:
                        break
                data = await reader.readexactly(length) if length else b""
                ptype, flags = first[0] >> 4, first[0] & 0x0F
                if ptype == 1:
                    if not self._connect(conn, data):
                        return
                elif ptype == 3:
                    self._publish(conn, flags, data)
                elif ptype == 8:
                    self._subscribe(conn, data)
                elif ptype == 10:
                    pid = data[:2]
                    conn.writer.write(b"\xb0\x02" + pid)
                elif ptype == 12:
                    conn.writer.write(b"\xd0\x00")
                elif ptype == 14:
                    clean = True
                    return
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if conn in self._conns:
                self._conns.remove(conn)
            if conn.will is not None and not clean:
                self.published.append(conn.will)
                self._route(conn.will)
            with contextlib.suppress(Exception):
                writer.close()
            if task is not None:
                self._tasks.discard(task)

    def _connect(self, conn: _Conn, data: bytes) -> bool:
        _name, pos = _string(data, 0)
        flags = data[pos + 1]
        pos += 4  # level, flags, keepalive(2) -> keepalive is at pos+2..pos+4
        conn.client_id, pos = _string(data, pos)
        if flags & 0x04:
            will_topic, pos = _string(data, pos)
            wlen = int.from_bytes(data[pos : pos + 2], "big")
            will_payload = data[pos + 2 : pos + 2 + wlen]
            pos += 2 + wlen
            conn.will = Message(
                will_topic, will_payload, (flags >> 3) & 3, bool(flags & 0x20), conn.client_id
            )
        user = password = None
        if flags & 0x80:
            user, pos = _string(data, pos)
        if flags & 0x40:
            password, pos = _string(data, pos)
        self.connects.append(conn.client_id)
        rc = self.refuse_with or 0
        if (
            rc == 0
            and self.username is not None
            and (user, password)
            != (
                self.username,
                self.password,
            )
        ):
            rc = 4
        conn.writer.write(bytes([0x20, 0x02, 0x00, rc]))
        if rc:
            return False
        self._conns.append(conn)
        return True

    def _publish(self, conn: _Conn, flags: int, data: bytes) -> None:
        qos, retain = (flags >> 1) & 3, bool(flags & 1)
        topic, pos = _string(data, 0)
        if qos:
            conn.writer.write(b"\x40\x02" + data[pos : pos + 2])
            pos += 2
        message = Message(topic, data[pos:], qos, retain, conn.client_id)
        self.published.append(message)
        self._route(message)

    def _subscribe(self, conn: _Conn, data: bytes) -> None:
        pid, pos = data[:2], 2
        granted = bytearray()
        new: list[str] = []
        while pos < len(data):
            topic, pos = _string(data, pos)
            qos = data[pos]
            pos += 1
            conn.subscriptions.append(topic)
            self.subscribed.append(topic)
            new.append(topic)
            granted.append(min(qos, 1))
        body = pid + bytes(granted)
        conn.writer.write(b"\x90" + _encode_len(len(body)) + body)
        for topic in new:
            for retained in list(self.retained.values()):
                if topic_matches(topic, retained.topic):
                    self._deliver(conn, retained, retain_flag=True)
