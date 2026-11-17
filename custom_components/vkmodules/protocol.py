"""VKmodule TCP packet layouts from the manufacturer's 2009-2022 protocol.

A packet is a command/event ID followed by a MODEL-DEPENDENT fixed-length
payload. Never use a SOCKET-5 payload map for a different model.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProtocolError(Exception):
    """Malformed, unsupported, or unexpected protocol response."""


class UnsupportedDevice(ProtocolError):
    """The controller identifies as an undocumented hardware type."""


class DeviceChanged(ProtocolError):
    """A different physical module appeared at the configured endpoint."""


CMD_PING = 0x01
CMD_REBOOT = 0x02
CMD_VERSION = 0x03
CMD_UID = 0x04
EVT_REJECT = 0x0F


@dataclass(frozen=True, slots=True)
class Model:
    """The protocol's capabilities for one hardware type."""

    hardware_id: int
    name: str
    input_count: int
    relay_count: int
    temp_count: int = 0
    input_config_cmd: int | None = None
    input_change_evt: int | None = None
    relay_cmd: int | None = None
    state_cmd: int = 0x23
    state_size: int = 0
    packed_state: bool = False
    group_relay_cmd: int | None = None

    @property
    def event_lengths(self) -> dict[int, int]:
        """Return exactly the events valid for this model."""
        lengths = {CMD_PING: 0, CMD_VERSION: 4, CMD_UID: 2, EVT_REJECT: 1}
        if self.input_config_cmd is not None:
            lengths[self.input_config_cmd] = 3
            assert self.input_change_evt is not None
            lengths[self.input_change_evt] = 2
        if self.relay_cmd is not None:
            lengths[self.relay_cmd] = 3
        lengths[self.state_cmd] = self.state_size
        if self.group_relay_cmd is not None:
            lengths[self.group_relay_cmd] = 2
        if self.hardware_id == 2:
            # SOCKET-2W 0x24; SOCKET-2 does not emit it.
            lengths[0x24] = 2
        if self.temp_count:
            lengths[0x41] = 1
            lengths[0x42] = 1
        return lengths


MODELS: dict[int, Model] = {
    # 0x30/0x31/0x32 are specific to SOCKET-1.
    3: Model(3, "SOCKET-1", 4, 0, input_config_cmd=0x30,
             input_change_evt=0x31, state_cmd=0x32, state_size=4),
    2: Model(2, "SOCKET-2", 2, 2, input_config_cmd=0x20,
             input_change_evt=0x21, relay_cmd=0x22, state_size=4),
    4: Model(4, "SOCKET-3", 0, 2, temp_count=2, relay_cmd=0x43,
             state_cmd=0x44, state_size=4),
    5: Model(5, "SOCKET-4", 0, 8, relay_cmd=0x22, state_size=8),
    6: Model(6, "SOCKET-5", 4, 4, input_config_cmd=0x20,
             input_change_evt=0x21, relay_cmd=0x22, state_size=8),
    7: Model(7, "SOCKET-GIANT", 16, 16, input_config_cmd=0x20,
             input_change_evt=0x21, relay_cmd=0x22, state_size=4,
             packed_state=True, group_relay_cmd=0x25),
}
# Hardware type 1 (VRD-E) has no model-specific command definitions in this PDF.
# SOCKET-2W is reported as type 2 in this document (no separate hardware ID).


def decode_temperature(raw: int) -> int:
    """Sign-magnitude temperature: bit 7 indicates a negative value."""
    return -(raw & 0x7F) if raw & 0x80 else raw & 0x7F


def decode_state(model: Model, payload: tuple[int, ...]) -> tuple[list[bool], list[bool], list[int]]:
    """Decode the full device state, respecting each module's own framing."""
    if len(payload) != model.state_size:
        raise ProtocolError(f"Invalid {model.name} state size: {len(payload)}")
    if model.packed_state:
        ins = (payload[0] << 8) | payload[1]
        outs = (payload[2] << 8) | payload[3]
        return ([bool(ins & (1 << i)) for i in range(16)],
                [bool(outs & (1 << i)) for i in range(16)], [])
    if model.temp_count:
        for value in payload[model.temp_count:]:
            if value not in (0, 1):
                raise ProtocolError("Relay state must be 0 or 1")
        return ([], [bool(x) for x in payload[model.temp_count:]],
                [decode_temperature(x) for x in payload[:model.temp_count]])
    if any(value not in (0, 1) for value in payload):
        raise ProtocolError("Digital input/relay state must be 0 or 1")
    n = model.input_count
    return [bool(x) for x in payload[:n]], [bool(x) for x in payload[n:]], []


def encode_group_relays(mask: int) -> bytes:
    """Two bytes, high then low, for SOCKET-GIANT's full relay bitmap."""
    if not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask <= 0xFFFF:
        raise ValueError("mask must be an integer between 0 and 65535")
    return mask.to_bytes(2, "big")
