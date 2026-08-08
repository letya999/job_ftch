from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Final

from paritylab.models import TLSFingerprint, utc_now_iso

LOGGER = logging.getLogger(__name__)
TLS_HANDSHAKE: Final = 22
CLIENT_HELLO: Final = 1
GREASE_VALUES: Final = frozenset((value << 8) | value for value in range(0x0A, 0x100, 0x10))


class TLSParseError(ValueError):
    pass


class ByteReader:
    __slots__ = ("_data", "_offset")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    def take(self, size: int) -> bytes:
        if size < 0 or self._offset + size > len(self._data):
            raise TLSParseError(f"truncated field: need {size}, have {self.remaining}")
        value = self._data[self._offset : self._offset + size]
        self._offset += size
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("!H", self.take(2))[0]

    def u24(self) -> int:
        raw = self.take(3)
        return (raw[0] << 16) | (raw[1] << 8) | raw[2]

    def vector_u8(self) -> bytes:
        return self.take(self.u8())

    def vector_u16(self) -> bytes:
        return self.take(self.u16())


@dataclass(slots=True, frozen=True)
class ParsedClientHello:
    record_version: int
    legacy_version: int
    supported_versions: tuple[int, ...]
    cipher_suites: tuple[int, ...]
    extension_ids: tuple[int, ...]
    supported_groups: tuple[int, ...]
    ec_point_formats: tuple[int, ...]
    signature_algorithms: tuple[int, ...]
    alpn_protocols: tuple[str, ...]
    server_name: str | None


def is_grease(value: int) -> bool:
    return value in GREASE_VALUES or ((value & 0x0F0F) == 0x0A0A and (value >> 8) == (value & 0xFF))


def parse_tls_client_hello(record: bytes) -> ParsedClientHello:
    """Parse a TLS ClientHello from one or more complete TLS records."""
    if len(record) < 5:
        raise TLSParseError("TLS record header missing")

    offset = 0
    handshake_bytes = bytearray()
    record_version: int | None = None
    while offset + 5 <= len(record):
        content_type = record[offset]
        version = struct.unpack("!H", record[offset + 1 : offset + 3])[0]
        length = struct.unpack("!H", record[offset + 3 : offset + 5])[0]
        end = offset + 5 + length
        if end > len(record):
            raise TLSParseError("incomplete TLS record")
        if content_type != TLS_HANDSHAKE:
            if not handshake_bytes:
                raise TLSParseError(f"first TLS record is type {content_type}, not handshake")
            break
        if record_version is None:
            record_version = version
        handshake_bytes.extend(record[offset + 5 : end])
        if len(handshake_bytes) >= 4:
            expected = 4 + int.from_bytes(handshake_bytes[1:4], "big")
            if len(handshake_bytes) >= expected:
                break
        offset = end

    if record_version is None or len(handshake_bytes) < 4:
        raise TLSParseError("ClientHello handshake missing")
    handshake = ByteReader(bytes(handshake_bytes))
    handshake_type = handshake.u8()
    if handshake_type != CLIENT_HELLO:
        raise TLSParseError(f"handshake type {handshake_type} is not ClientHello")
    message_length = handshake.u24()
    body = ByteReader(handshake.take(message_length))
    legacy_version = body.u16()
    body.take(32)  # random
    body.vector_u8()  # legacy session id

    raw_ciphers = ByteReader(body.vector_u16())
    ciphers: list[int] = []
    while raw_ciphers.remaining:
        ciphers.append(raw_ciphers.u16())

    body.vector_u8()  # legacy compression methods
    if body.remaining == 0:
        extensions_raw = b""
    else:
        extensions_raw = body.vector_u16()

    extensions = ByteReader(extensions_raw)
    extension_ids: list[int] = []
    supported_groups: list[int] = []
    point_formats: list[int] = []
    signature_algorithms: list[int] = []
    supported_versions: list[int] = []
    alpn_protocols: list[str] = []
    server_name: str | None = None

    while extensions.remaining:
        extension_id = extensions.u16()
        payload = ByteReader(extensions.vector_u16())
        extension_ids.append(extension_id)

        if extension_id == 0 and payload.remaining >= 2:  # server_name
            names = ByteReader(payload.vector_u16())
            while names.remaining:
                name_type = names.u8()
                name = names.vector_u16()
                if name_type == 0 and server_name is None:
                    try:
                        server_name = name.decode("idna")
                    except UnicodeError:
                        server_name = name.decode("ascii", errors="replace")
        elif extension_id == 10 and payload.remaining >= 2:  # supported_groups
            groups = ByteReader(payload.vector_u16())
            while groups.remaining:
                supported_groups.append(groups.u16())
        elif extension_id == 11 and payload.remaining >= 1:  # ec_point_formats
            point_formats.extend(payload.vector_u8())
        elif extension_id == 13 and payload.remaining >= 2:  # signature_algorithms
            algorithms = ByteReader(payload.vector_u16())
            while algorithms.remaining:
                signature_algorithms.append(algorithms.u16())
        elif extension_id == 16 and payload.remaining >= 2:  # ALPN
            protocols = ByteReader(payload.vector_u16())
            while protocols.remaining:
                alpn_protocols.append(protocols.vector_u8().decode("ascii", errors="replace"))
        elif extension_id == 43 and payload.remaining >= 1:  # supported_versions
            versions = ByteReader(payload.vector_u8())
            while versions.remaining:
                supported_versions.append(versions.u16())

    return ParsedClientHello(
        record_version=record_version,
        legacy_version=legacy_version,
        supported_versions=tuple(supported_versions),
        cipher_suites=tuple(ciphers),
        extension_ids=tuple(extension_ids),
        supported_groups=tuple(supported_groups),
        ec_point_formats=tuple(point_formats),
        signature_algorithms=tuple(signature_algorithms),
        alpn_protocols=tuple(alpn_protocols),
        server_name=server_name,
    )


def _hyphen(values: tuple[int, ...], *, remove_grease: bool = True) -> str:
    selected = [value for value in values if not remove_grease or not is_grease(value)]
    return "-".join(str(value) for value in selected)


def ja3_fingerprint(hello: ParsedClientHello) -> tuple[str, str]:
    raw = ",".join(
        [
            str(hello.legacy_version),
            _hyphen(hello.cipher_suites),
            _hyphen(hello.extension_ids),
            _hyphen(hello.supported_groups),
            _hyphen(hello.ec_point_formats, remove_grease=False),
        ]
    )
    return raw, hashlib.md5(raw.encode("ascii"), usedforsecurity=False).hexdigest()


def _tls_version_code(hello: ParsedClientHello) -> str:
    versions = [value for value in hello.supported_versions if not is_grease(value)]
    selected = max(versions, default=hello.legacy_version)
    mapping = {
        0x0304: "13",
        0x0303: "12",
        0x0302: "11",
        0x0301: "10",
        0x0300: "s3",
        0x0002: "s2",
    }
    return mapping.get(selected, "00")


def _ja4_alpn(protocols: tuple[str, ...]) -> str:
    if not protocols or not protocols[0]:
        return "00"
    encoded = protocols[0].encode("utf-8", errors="replace")
    if len(encoded) == 1:
        return f"{encoded[0]:02x}"[-2:]
    first, last = encoded[0], encoded[-1]
    if 0x20 <= first <= 0x7E and 0x20 <= last <= 0x7E:
        return chr(first).lower() + chr(last).lower()
    return f"{first:x}"[-1:] + f"{last:x}"[-1:]


def _sha12(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()[:12]


def ja4_fingerprint(hello: ParsedClientHello, *, transport: str = "t") -> tuple[str, str]:
    ciphers = sorted(value for value in hello.cipher_suites if not is_grease(value))
    extensions = sorted(
        value
        for value in hello.extension_ids
        if not is_grease(value) and value not in {0, 16}
    )
    signatures = [value for value in hello.signature_algorithms if not is_grease(value)]
    cipher_text = ",".join(f"{value:04x}" for value in ciphers)
    extension_text = ",".join(f"{value:04x}" for value in extensions)
    signature_text = ",".join(f"{value:04x}" for value in signatures)
    ciphers_hash = _sha12(cipher_text) if ciphers else "000000000000"
    extensions_hash_input = f"{extension_text}_{signature_text}"
    extensions_hash = _sha12(extensions_hash_input) if extensions or signatures else "000000000000"
    non_grease_ext_count = sum(1 for value in hello.extension_ids if not is_grease(value))
    non_grease_cipher_count = sum(1 for value in hello.cipher_suites if not is_grease(value))
    a = (
        f"{transport}{_tls_version_code(hello)}"
        f"{'d' if hello.server_name else 'i'}"
        f"{min(non_grease_cipher_count, 99):02d}"
        f"{min(non_grease_ext_count, 99):02d}"
        f"{_ja4_alpn(hello.alpn_protocols)}"
    )
    raw = f"{a}_{cipher_text}_{extension_text}_{signature_text}"
    return raw, f"{a}_{ciphers_hash}_{extensions_hash}"


@dataclass(slots=True, frozen=True)
class ConnectionObservation:
    connection_id: str
    backend_source_port: int
    client_host: str
    client_port: int
    fingerprint: TLSFingerprint
    opened_monotonic: float


class TLSConnectionRegistry:
    """Maps the proxy's backend source port to original TLS observations."""

    def __init__(self) -> None:
        self._by_backend_port: dict[int, ConnectionObservation] = {}
        self._lock = asyncio.Lock()

    async def register(self, observation: ConnectionObservation) -> None:
        async with self._lock:
            self._by_backend_port[observation.backend_source_port] = observation

    async def lookup(self, backend_peer_port: int) -> ConnectionObservation | None:
        async with self._lock:
            return self._by_backend_port.get(backend_peer_port)

    async def prune(self, older_than_seconds: float = 3600.0) -> None:
        cutoff = time.monotonic() - older_than_seconds
        async with self._lock:
            stale = [
                port
                for port, observation in self._by_backend_port.items()
                if observation.opened_monotonic < cutoff
            ]
            for port in stale:
                self._by_backend_port.pop(port, None)


async def _read_client_hello(reader: asyncio.StreamReader, *, limit: int = 131072) -> bytes:
    collected = bytearray()
    handshake_length: int | None = None
    while len(collected) < limit:
        header = await asyncio.wait_for(reader.readexactly(5), timeout=5.0)
        length = struct.unpack("!H", header[3:5])[0]
        body = await asyncio.wait_for(reader.readexactly(length), timeout=5.0)
        collected.extend(header)
        collected.extend(body)
        if header[0] != TLS_HANDSHAKE:
            break
        if handshake_length is None and len(body) >= 4 and body[0] == CLIENT_HELLO:
            handshake_length = 4 + int.from_bytes(body[1:4], "big")
        handshake_payload = sum(
            struct.unpack("!H", collected[index + 3 : index + 5])[0]
            for index in _tls_record_offsets(bytes(collected))
            if collected[index] == TLS_HANDSHAKE
        )
        if handshake_length is not None and handshake_payload >= handshake_length:
            break
    if len(collected) >= limit:
        raise TLSParseError("ClientHello exceeds configured limit")
    return bytes(collected)


def _tls_record_offsets(data: bytes) -> list[int]:
    offsets: list[int] = []
    offset = 0
    while offset + 5 <= len(data):
        length = struct.unpack("!H", data[offset + 3 : offset + 5])[0]
        if offset + 5 + length > len(data):
            break
        offsets.append(offset)
        offset += 5 + length
    return offsets


def make_fingerprint(
    raw: bytes,
    *,
    connection_id: str,
    client_host: str,
    client_port: int,
    backend_source_port: int,
) -> TLSFingerprint:
    try:
        hello = parse_tls_client_hello(raw)
        ja3_raw, ja3 = ja3_fingerprint(hello)
        ja4_raw, ja4 = ja4_fingerprint(hello)
        return TLSFingerprint(
            connection_id=connection_id,
            observed_at=utc_now_iso(),
            client_host=client_host,
            client_port=client_port,
            backend_source_port=backend_source_port,
            record_version=hello.record_version,
            legacy_version=hello.legacy_version,
            supported_versions=hello.supported_versions,
            cipher_suites=hello.cipher_suites,
            extension_ids=hello.extension_ids,
            supported_groups=hello.supported_groups,
            ec_point_formats=hello.ec_point_formats,
            signature_algorithms=hello.signature_algorithms,
            alpn_protocols=hello.alpn_protocols,
            server_name=hello.server_name,
            ja3_raw=ja3_raw,
            ja3=ja3,
            ja4_raw=ja4_raw,
            ja4=ja4,
        )
    except Exception as exc:
        return TLSFingerprint(
            connection_id=connection_id,
            observed_at=utc_now_iso(),
            client_host=client_host,
            client_port=client_port,
            backend_source_port=backend_source_port,
            record_version=None,
            legacy_version=None,
            supported_versions=(),
            cipher_suites=(),
            extension_ids=(),
            supported_groups=(),
            ec_point_formats=(),
            signature_algorithms=(),
            alpn_protocols=(),
            server_name=None,
            ja3_raw=None,
            ja3=None,
            ja4_raw=None,
            ja4=None,
            parse_error=f"{type(exc).__name__}: {exc}",
        )


class TLSMirrorProxy:
    """Passive ClientHello observer and byte-for-byte TCP proxy.

    The proxy does not terminate TLS and therefore cannot alter the browser's
    handshake. It reads only the cleartext ClientHello, records fingerprints,
    forwards the exact bytes to the local Hypercorn TLS endpoint, and then
    pipes encrypted traffic in both directions.
    """

    def __init__(
        self,
        *,
        listen_host: str,
        listen_port: int,
        backend_host: str,
        backend_port: int,
        registry: TLSConnectionRegistry,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.registry = registry
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.listen_host,
            port=self.listen_port,
            start_serving=True,
        )
        sockets = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        LOGGER.info("TLS mirror listening on %s", sockets)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or ("unknown", 0)
        client_host, client_port = str(peer[0]), int(peer[1])
        connection_id = uuid.uuid4().hex
        backend_writer: asyncio.StreamWriter | None = None
        try:
            initial = await _read_client_hello(reader)
            backend_reader, backend_writer = await asyncio.open_connection(
                self.backend_host,
                self.backend_port,
            )
            backend_sockname = backend_writer.get_extra_info("sockname") or ("", 0)
            backend_source_port = int(backend_sockname[1])
            fingerprint = make_fingerprint(
                initial,
                connection_id=connection_id,
                client_host=client_host,
                client_port=client_port,
                backend_source_port=backend_source_port,
            )
            await self.registry.register(
                ConnectionObservation(
                    connection_id=connection_id,
                    backend_source_port=backend_source_port,
                    client_host=client_host,
                    client_port=client_port,
                    fingerprint=fingerprint,
                    opened_monotonic=time.monotonic(),
                )
            )
            backend_writer.write(initial)
            await backend_writer.drain()
            client_to_backend = asyncio.create_task(self._pipe(reader, backend_writer))
            backend_to_client = asyncio.create_task(self._pipe(backend_reader, writer))
            done, pending = await asyncio.wait(
                {client_to_backend, backend_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, TLSParseError) as exc:
            LOGGER.debug("TLS proxy connection %s ended: %s", connection_id, exc)
        except Exception:
            LOGGER.exception("TLS proxy connection %s failed", connection_id)
        finally:
            if backend_writer is not None:
                backend_writer.close()
                await backend_writer.wait_closed()
            writer.close()
            await writer.wait_closed()

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.write_eof()
            except (AttributeError, OSError, RuntimeError):
                pass
