"""Contact-open binary sensors for supported VKmodule discrete inputs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError

from .protocol import ProtocolError
from .runtime_data import VKmoduleConfigEntry

PARALLEL_UPDATES = 0  # All I/O is serialized by the TCP client.


async def async_setup_entry(hass: HomeAssistant, entry: VKmoduleConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    """Only create channels supported by this exact hardware model."""
    client = entry.runtime_data.client
    assert client.model is not None
    channels = range(client.model.input_count)
    if client.model.hardware_id == 2 and client.analog_input:
        # Input 0 has been configured as analog in the module's own web UI.
        channels = range(1, client.model.input_count)
    async_add_entities([VKmoduleInput(entry, i) for i in channels])


class VKmoduleInput(BinarySensorEntity):
    """A dry-contact input. Protocol: 0=closed, 1=open."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_translation_key = "input"

    def __init__(self, entry: VKmoduleConfigEntry, channel: int) -> None:
        self._client = entry.runtime_data.client
        self._channel = channel
        self._invert = entry.options.get("invert_inputs", False)
        self._attr_unique_id = f"{entry.entry_id}-in-{channel}"  # Stable for this integration entry.
        self._attr_translation_placeholders = {"channel": str(channel + 1)}
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def is_on(self) -> bool | None:
        return self._client.inputs[self._channel] != self._invert

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self._state_changed, "input", self._channel))

    @callback
    def _state_changed(self) -> None:
        self.async_write_ha_state()

    async def async_configure_input(self, enabled: bool, debounce_ms: int) -> None:
        """Configure controller-side input handling/debounce (20 ms steps)."""
        try:
            await self._client.set_input_config(self._channel, enabled, debounce_ms)
        except (ConnectionError, OSError, TimeoutError, ProtocolError, ValueError) as exc:
            raise HomeAssistantError(f"VKmodule input configuration failed: {exc}") from exc
