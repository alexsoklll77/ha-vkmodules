"""Device diagnostics that never include IP/UID or live input states."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .runtime_data import VKmoduleConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VKmoduleConfigEntry,
) -> dict[str, Any]:
    client = entry.runtime_data.client
    return {
        "configuration": async_redact_data(dict(entry.data), {"host", "uid"}),
        "options": dict(entry.options),
        "connection": {"available": client.available, "port": client.port},
        "device": {
            "hardware_id": client.hw_type,
            "model": "SOCKET-2W" if client.hw_type == 2 and client.analog_input
                     else client.model.name if client.model else None,
            "firmware_version": client.version,
            "firmware_variant": client.fw_type,
            "digital_inputs": len(client.inputs),
            "relays": len(client.relays),
            "temperature_channels": len(client.temperatures),
            "analog_input_selected": client.analog_input,
        },
    }
