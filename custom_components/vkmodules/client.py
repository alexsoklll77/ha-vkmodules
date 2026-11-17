"""Async, event-driven TCP client for all documented VKmodule SOCKET controllers.

Protocol packets have no sequence numbers. Only one request may be pending at
any time; an unanswered request invalidates the stream and forces a reconnect.
No automatic retry of relay-changing commands is ever performed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from .const import DEFAULT_TIMEOUT
from .protocol import (
    CMD_PING, CMD_UID, CMD_VERSION, EVT_REJECT, MODELS, DeviceChanged,
    Model, ProtocolError, UnsupportedDevice, decode_state, decode_temperature,
    encode_group_relays,
)

_LOGGER = logging.getLogger(__name__)


class VKmoduleClient:
    """One TCP connection, request queue, event receiver and recovery loop."""

    def __init__(self, host: str, port: int, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._receiver: asyncio.Task[None] | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[int, asyncio.Future[tuple[int, ...]]] | None = None
        self._subscribers: dict[Callable[[], None], tuple[str | None, int | None]] = {}
        self._stopping = True
        self._ever_connected = False
        self.available = False
        self.model: Model | None = None
        self.hw_type: int | None = None
        self.uid: int | None = None
        self.version: str | None = None
        self.fw_type: int | None = None
        self.inputs: list[bool] = []  # True = open contact (protocol 1).
        self.relays: list[bool] = []  # True = energized (protocol 1).
        self.temperatures: list[int | None] = []
        self.analog_voltage: float | None = None
        self.analog_input = False
        self._expected_identity: tuple[int, int] | None = None

    def subscribe(
        self, callback: Callable[[], None],
        kind: str | None = None, channel: int | None = None,
    ) -> Callable[[], None]:
        """Subscribe to one entity/channel; no filter means all changes."""
        self._subscribers[callback] = (kind, channel)
        return lambda: self._subscribers.pop(callback, None)

    def _notify(self, kind: str | None = None, channel: int | None = None) -> None:
        for cb, (wanted_kind, wanted_channel) in tuple(self._subscribers.items()):
            if kind is not None and wanted_kind is not None and wanted_kind != kind:
                continue
            if channel is not None and wanted_channel is not None and wanted_channel != channel:
                continue
            try:
                cb()
            except Exception:
                _LOGGER.exception("VKmodule state listener raised")

    def _set_available(self, value: bool, reason: str | None = None) -> None:
        if self.available == value:
            return
        self.available = value
        if value:
            if self._ever_connected:
                _LOGGER.info("VKmodule %s:%s connection restored", self.host, self.port)
            else:
                _LOGGER.info("VKmodule %s:%s connected", self.host, self.port)
                self._ever_connected = True
        elif not self._stopping:
            _LOGGER.info("VKmodule %s:%s unavailable%s", self.host, self.port,
                         f": {reason}" if reason else "")
        self._notify()

    async def connect(self) -> None:
        """Identify the module, load state, then start recovery/heartbeat."""
        if self._supervisor is not None:
            raise RuntimeError("Already started")
        self._stopping = False
        try:
            await self._establish()
        except BaseException:
            self._stopping = True
            await self._disconnect()
            raise
        self._supervisor = asyncio.create_task(self._supervise(), name="vkmodule-supervisor")

    async def _establish(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        # Identify with common events only. Unsolicited model-specific events
        # before 0x03 are impossible to frame without knowing their lengths.
        self._receiver = asyncio.create_task(self._receive(), name="vkmodule-receiver")
        hw, major, minor, fw = await self._request(CMD_VERSION, CMD_VERSION)
        model = MODELS.get(hw)
        if model is None:
            raise UnsupportedDevice(f"Undocumented VKmodule hardware type {hw}")
        # The controller may asynchronously report input changes while UID is
        # requested. Once hardware type is known, frame those events correctly.
        self.model = model
        uid_hi, uid_lo = await self._request(CMD_UID, CMD_UID)
        identity = (hw, uid_hi << 8 | uid_lo)
        if self._expected_identity is not None and identity != self._expected_identity:
            raise DeviceChanged("Different device at the configured TCP endpoint")
        self.hw_type, self.uid = identity
        self._expected_identity = identity
        self.model = model
        self.version = f"{major}.{minor}"
        self.fw_type = fw
        self.inputs = [False] * model.input_count
        self.relays = [False] * model.relay_count
        self.temperatures = [None] * model.temp_count
        self.analog_voltage = None
        await self.refresh_state()
        if self.analog_input:
            await self.refresh_analog()
        self._set_available(True)

    async def _receive(self) -> None:
        """Only coroutine allowed to read the TCP input stream."""
        reader = self._reader
        assert reader is not None
        reason = "TCP stream closed"
        try:
            while True:
                event = (await reader.readexactly(1))[0]
                # Before identification we can only frame common event IDs.
                lengths = (self.model.event_lengths if self.model is not None
                           else {CMD_PING: 0, CMD_VERSION: 4, CMD_UID: 2, EVT_REJECT: 1})
                if event not in lengths:
                    raise ProtocolError(f"Unknown event 0x{event:02X}; stream cannot be framed")
                payload = tuple(await reader.readexactly(lengths[event]))
                _LOGGER.debug("VKmodule RX %02x %s", event, bytes(payload).hex())
                if self.model is not None:
                    self._parse_event(event, payload)
                if self._pending is not None:
                    expected, future = self._pending
                    if not future.done():
                        if event == EVT_REJECT:
                            future.set_exception(ProtocolError(
                                f"Device rejected command 0x{payload[0]:02X}"))
                        elif event == expected:
                            future.set_result(payload)
        except asyncio.CancelledError:
            reason = "receiver cancelled"
        except (asyncio.IncompleteReadError, OSError, ConnectionError, ProtocolError) as exc:
            reason = str(exc) or type(exc).__name__
            _LOGGER.debug("VKmodule receiver stopped: %s", reason)
        except Exception:
            reason = "receiver exception"
            _LOGGER.exception("Unexpected VKmodule receiver error")
        finally:
            self._set_available(False, reason)
            if self._pending is not None and not self._pending[1].done():
                self._pending[1].set_exception(ConnectionError("VKmodule connection lost"))

    def _parse_event(self, event: int, payload: tuple[int, ...]) -> None:
        model = self.model
        assert model is not None
        changes: list[tuple[str, int | None]] = []
        if event == model.state_cmd:
            ins, relays, temps = decode_state(model, payload)
            for i, value in enumerate(ins):
                if self.inputs[i] != value:
                    changes.append(("input", i))
            for i, value in enumerate(relays):
                if self.relays[i] != value:
                    changes.append(("relay", i))
            for i, value in enumerate(temps):
                if self.temperatures[i] != value:
                    changes.append(("temperature", i))
            self.inputs, self.relays = ins, relays
            if model.temp_count:
                self.temperatures = temps
        elif event == model.input_change_evt:
            channel, state = payload
            if not 0 <= channel < model.input_count or state not in (0, 1):
                raise ProtocolError("Invalid digital input event")
            value = bool(state)
            if self.inputs[channel] != value:
                self.inputs[channel] = value
                changes.append(("input", channel))
        elif event == model.relay_cmd:
            channel, state, _ticks = payload
            if not 0 <= channel < model.relay_count or state not in (0, 1):
                raise ProtocolError("Invalid relay event")
            value = bool(state)
            if self.relays[channel] != value:
                self.relays[channel] = value
                changes.append(("relay", channel))
        elif model.temp_count and event in (0x41, 0x42):
            channel = event - 0x41
            value = decode_temperature(payload[0])
            if self.temperatures[channel] != value:
                self.temperatures[channel] = value
                changes.append(("temperature", channel))
        elif model.hardware_id == 2 and event == 0x24:
            raw = (payload[0] << 8) | payload[1]
            if raw > 1024:
                raise ProtocolError("SOCKET-2W ADC reading exceeds documented range")
            value = round(raw / 1024, 4)
            if self.analog_voltage != value:
                self.analog_voltage = value
                changes.append(("analog", 0))
        elif event == model.group_relay_cmd:
            mask = payload[0] << 8 | payload[1]
            states = [bool(mask & (1 << i)) for i in range(16)]
            for i, value in enumerate(states):
                if self.relays[i] != value:
                    changes.append(("relay", i))
            self.relays = states
        for kind, channel in changes:
            self._notify(kind, channel)

    async def _send(self, command: int, payload: bytes = b"") -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ConnectionError("VKmodule not connected")
        packet = bytes((command,)) + payload
        _LOGGER.debug("VKmodule TX %s", packet.hex())
        writer.write(packet)
        await writer.drain()

    async def _request(self, command: int, response: int,
                       payload: bytes = b"") -> tuple[int, ...]:
        async with self._lock:
            if self._receiver is None or self._receiver.done():
                raise ConnectionError("VKmodule receiver is not active")
            future: asyncio.Future[tuple[int, ...]] = asyncio.get_running_loop().create_future()
            self._pending = (response, future)  # Must precede writing.
            try:
                await self._send(command, payload)
                return await asyncio.wait_for(future, self.timeout)
            except (TimeoutError, asyncio.CancelledError):
                # No request IDs: never trust the stream after an abandoned reply.
                # Do not wait for reconnect here; it is owned by the supervisor.
                await self._disconnect()
                raise
            finally:
                if self._pending is not None and self._pending[1] is future:
                    self._pending = None
                if not future.done():
                    future.cancel()

    async def _disconnect(self) -> None:
        self._set_available(False)
        task = self._receiver
        self._receiver = None
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            writer.close()
            with suppress(OSError, TimeoutError, ConnectionError):
                await asyncio.wait_for(writer.wait_closed(), self.timeout)

    async def _supervise(self) -> None:
        backoff = 1
        try:
            while not self._stopping:
                if self._receiver is not None and not self._receiver.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(self._receiver), 30)
                    except TimeoutError:
                        try:
                            await self.ping()
                            await self.refresh_state()
                            if self.analog_input:
                                await self.refresh_analog()
                        except (ConnectionError, OSError, TimeoutError, ProtocolError) as exc:
                            _LOGGER.debug("VKmodule heartbeat failed: %s", exc)
                            await self._disconnect()
                        continue
                await self._disconnect()
                if self._stopping:
                    break
                try:
                    await self._establish()
                    backoff = 1
                except asyncio.CancelledError:
                    raise
                except (ConnectionError, OSError, TimeoutError, ProtocolError, ValueError) as exc:
                    _LOGGER.debug("VKmodule reconnect attempt failed: %s", exc)
                    await self._disconnect()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        self._stopping = True
        task = self._supervisor
        self._supervisor = None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._disconnect()
        self._subscribers.clear()

    async def ping(self) -> None:
        await self._request(CMD_PING, CMD_PING)

    async def refresh_state(self) -> None:
        if self.model is None:
            raise ProtocolError("Model not identified")
        await self._request(self.model.state_cmd, self.model.state_cmd)

    async def refresh_analog(self) -> None:
        if self.model is None or self.model.hardware_id != 2:
            raise ProtocolError("Analog input is only documented for SOCKET-2W")
        # Deliberately opt-in: SOCKET-2 (same hardware type) may reject 0x24.
        await self._request(0x24, 0x24)

    async def set_relay(self, channel: int, on: bool, duration_ms: int = 0) -> None:
        model = self.model
        if model is None or model.relay_cmd is None:
            raise ProtocolError("This module does not have relays")
        if not 0 <= channel < model.relay_count:
            raise ValueError(f"Relay channel must be 0..{model.relay_count - 1}")
        if not 0 <= duration_ms <= 25500 or duration_ms % 100:
            raise ValueError("Pulse duration must be 0..25500 ms, step 100 ms")
        if not on and duration_ms:
            raise ValueError("Duration is only supported when turning ON")
        result = await self._request(model.relay_cmd, model.relay_cmd,
                                     bytes((channel, int(on), duration_ms // 100)))
        if result[0] != channel or result[1] != int(on):
            # Command may have physically executed; do not retry it.
            raise ProtocolError("Relay confirmation did not match command")

    async def set_input_config(self, channel: int, enabled: bool,
                               debounce_ms: int) -> None:
        model = self.model
        if model is None or model.input_config_cmd is None:
            raise ProtocolError("Input configuration is unsupported")
        if not 0 <= channel < model.input_count:
            raise ValueError("Invalid input channel")
        if not 0 <= debounce_ms <= 5100 or debounce_ms % 20:
            raise ValueError("Debounce must be 0..5100 ms, step 20 ms")
        result = await self._request(model.input_config_cmd, model.input_config_cmd,
                                     bytes((channel, int(enabled), debounce_ms // 20)))
        if result != (channel, int(enabled), debounce_ms // 20):
            raise ProtocolError("Input configuration confirmation mismatch")

    async def get_input_config(self, channel: int) -> tuple[int, bool, int]:
        model = self.model
        if model is None or model.input_config_cmd is None:
            raise ProtocolError("Input configuration is unsupported")
        if not 0 <= channel < model.input_count:
            raise ValueError("Invalid input channel")
        # SOCKET-1: command 0x31 responds with 0x30; others 0x21 -> 0x20.
        reply = await self._request(model.input_config_cmd + 1,
                                    model.input_config_cmd, bytes((channel,)))
        if reply[0] != channel:
            raise ProtocolError("Input config refers to wrong channel")
        return reply[0], bool(reply[1]), reply[2] * 20

    async def set_all_relays(self, mask: int) -> None:
        model = self.model
        if model is None or model.group_relay_cmd is None:
            raise ProtocolError("Group command is only supported by SOCKET-GIANT")
        payload = encode_group_relays(mask)
        result = await self._request(model.group_relay_cmd, model.group_relay_cmd, payload)
        if bytes(result) != payload:
            raise ProtocolError("Group relay confirmation mismatch")
