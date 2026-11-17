"""Independent Home Assistant integration for the VKmodule Ethernet series."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr, service
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import ConfigType

from .client import VKmoduleClient
from .const import CONF_ANALOG_INPUT, DEFAULT_PORT, DEFAULT_TIMEOUT, DOMAIN, PLATFORMS
from .protocol import DeviceChanged, ProtocolError, UnsupportedDevice
from .runtime_data import VKmoduleConfigEntry, VKmoduleRuntimeData

_LOGGER = logging.getLogger(__name__)


def _duration(value: int) -> int:
    if value % 100:
        raise vol.Invalid("duration_ms must be divisible by 100")
    return value


def _debounce(value: int) -> int:
    if value % 20:
        raise vol.Invalid("debounce_ms must be divisible by 20")
    return value


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions once, even if no devices are currently loaded."""
    service.async_register_platform_entity_service(
        hass, DOMAIN, "pulse", entity_domain="switch",
        schema={vol.Required("duration_ms"): vol.All(vol.Coerce(int),
                 vol.Range(min=100, max=25500), _duration)},
        func="async_pulse",
    )
    service.async_register_platform_entity_service(
        hass, DOMAIN, "configure_input", entity_domain="binary_sensor",
        schema={
            vol.Required("enabled"): bool,
            vol.Required("debounce_ms"): vol.All(vol.Coerce(int),
                vol.Range(min=0, max=5100), _debounce),
        },
        func="async_configure_input",
    )

    async def handle_set_relay_mask(call: ServiceCall) -> None:
        """Target exactly one HA device, never broadcast to all modules."""
        dev = dr.async_get(hass).async_get(call.data["device_id"])
        if dev is None:
            raise HomeAssistantError("VKmodule target device was not found")
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                   if entry.entry_id in dev.config_entries and getattr(entry, "runtime_data", None)]
        if len(entries) != 1:
            raise HomeAssistantError("Target must refer to exactly one connected VKmodule")
        try:
            await entries[0].runtime_data.client.set_all_relays(call.data["mask"])
        except (ConnectionError, OSError, TimeoutError, ProtocolError, ValueError) as exc:
            raise HomeAssistantError(f"Cannot set VKmodule relay mask: {exc}") from exc

    hass.services.async_register(
        DOMAIN, "set_relay_mask", handle_set_relay_mask,
        schema=vol.Schema({
            vol.Required("device_id"): str,
            vol.Required("mask"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        }),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VKmoduleConfigEntry) -> bool:
    """Connect and identify the hardware before creating appropriate entities."""
    host, port = entry.data["host"], entry.data.get("port", DEFAULT_PORT)
    client = VKmoduleClient(host, port, timeout=DEFAULT_TIMEOUT)
    client.analog_input = bool(entry.options.get(CONF_ANALOG_INPUT, False))
    try:
        await client.connect()
    except (UnsupportedDevice, DeviceChanged) as exc:
        await client.close()
        raise ConfigEntryError(str(exc)) from exc
    except (ConnectionError, OSError, TimeoutError, ProtocolError) as exc:
        await client.close()
        raise ConfigEntryNotReady("Cannot connect to VKmodule Ethernet controller") from exc

    assert client.model is not None and client.uid is not None
    if ("uid" in entry.data and int(entry.data["uid"]) != client.uid) or (
        "hw_type" in entry.data and int(entry.data["hw_type"]) != client.hw_type
    ):
        await client.close()
        raise ConfigEntryError("TCP endpoint points to another VKmodule controller")

    # New domain and stable physical identity: no merging with the old socket5 integration.
    device_info = DeviceInfo(
        identifiers={(DOMAIN, f"hw:{client.hw_type}:uid:{client.uid}")},
        name=f"VKmodule {client.model.name} ({host})",
        manufacturer="VKmodule",
        model=("SOCKET-2W" if client.hw_type == 2 and client.analog_input
               else client.model.name),
        sw_version=client.version,
    )
    entry.runtime_data = VKmoduleRuntimeData(client=client, device_info=device_info)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VKmoduleConfigEntry) -> bool:
    """Unsubscribe entities, cancel tasks and close TCP on unload/reload."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.close()
    return True
