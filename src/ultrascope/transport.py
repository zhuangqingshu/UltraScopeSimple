"""Instrument transport: everything that actually touches the VISA session.

Nothing above this module imports pyvisa, which is what makes the decoding and
command layers testable without hardware (see FakeTransport).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional

try:  # typing.Protocol is 3.8+, but keep the import defensive for older stubs
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment]

# The DS1000E is slow; deep-memory reads of 1M points need a very long timeout.
TIMEOUT_NORM_MS = 10_000
TIMEOUT_RAW_MS = 120_000

CHUNK_SIZE = 1024 * 1024


class Transport(Protocol):
    """The only surface Scope is allowed to use to reach the instrument."""

    def write(self, cmd: str) -> None: ...

    def query(self, cmd: str) -> str: ...

    def read_raw(self) -> bytes: ...

    def close(self) -> None: ...

    @contextmanager
    def timeout(self, ms: int) -> Iterator[None]: ...


class PyVisaTransport:
    """Transport backed by a live pyvisa resource."""

    def __init__(self, resource: str, rm=None):
        import pyvisa

        self.rm = rm or pyvisa.ResourceManager()
        self.resource = resource
        self.dev = self.rm.open_resource(resource)
        self.dev.timeout = TIMEOUT_NORM_MS
        self.dev.chunk_size = CHUNK_SIZE

    def write(self, cmd: str) -> None:
        self.dev.write(cmd)

    def query(self, cmd: str) -> str:
        return self.dev.query(cmd).strip()

    def read_raw(self) -> bytes:
        return self.dev.read_raw()

    @contextmanager
    def timeout(self, ms: int) -> Iterator[None]:
        """Raise the VISA timeout for one operation, then always put it back."""
        previous = self.dev.timeout
        self.dev.timeout = ms
        try:
            yield
        finally:
            self.dev.timeout = previous

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass


class FakeTransport:
    """Scripted transport for tests.

    ``responses`` maps a query string to the text the instrument would answer;
    ``blocks`` is the queue of IEEE 488.2 payloads handed out by read_raw().
    Every command that was written is recorded in ``written``, which is how the
    "a setting you did not pass is never sent" contract gets asserted.
    """

    def __init__(self, responses: Optional[Dict[str, str]] = None,
                 blocks: Optional[List[bytes]] = None):
        self.responses = dict(responses or {})
        self.blocks = list(blocks or [])
        self.written: List[str] = []
        self.closed = False
        self.timeouts: List[int] = []

    def write(self, cmd: str) -> None:
        self.written.append(cmd)

    def query(self, cmd: str) -> str:
        self.written.append(cmd)
        try:
            return self.responses[cmd].strip()
        except KeyError:
            raise AssertionError(f"FakeTransport has no scripted answer for {cmd!r}")

    def read_raw(self) -> bytes:
        if not self.blocks:
            raise AssertionError("FakeTransport ran out of scripted blocks")
        return self.blocks.pop(0)

    @contextmanager
    def timeout(self, ms: int) -> Iterator[None]:
        self.timeouts.append(ms)
        yield

    def close(self) -> None:
        self.closed = True
