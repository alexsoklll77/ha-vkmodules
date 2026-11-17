"""Relay switch entities for VKmodule SOCKET-2/2W/3/4/5/GIANT."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .protocol import ProtocolError
from .runtime_data import VKmoduleConfigEntry

PARALLEL_UPDATES = 0
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: VKmoduleConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    client = entry.runtime_data.client
    assert client.model is not None
    async_add_entities([VKmoduleRelay(entry, index) for index in range(client.model.relay_count)])


class VKmoduleRelay(SwitchEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "relay"

    def __init__(self, entry: VKmoduleConfigEntry, channel: int) -> None:
        self._client = entry.runtime_data.client
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}-relay-{channel}"  # Stable for this integration entry.
        self._attr_translation_placeholders = {"channel": str(channel + 1)}
        self._attr_device_info = entry.runtime_data.device_info
        self._refresh_tasks: set[asyncio.Task[None]] = set()

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def is_on(self) -> bool | None:
        return self._client.relays[self._channel]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self._state_changed, "relay", self._channel))

    @callback
    def _state_changed(self) -> None:
        self.async_write_ha_state()

    async def _switch(self, state: bool, duration_ms: int = 0) -> None:
        try:
            await self._client.set_relay(self._channel, state, duration_ms)
        except (ConnectionError, OSError, TimeoutError, ProtocolError, ValueError) as exc:
            raise HomeAssistantError(f"VKmodule relay command failed: {exc}") from exc

    async def async_turn_on(self, **kwargs) -> None:
        await self._switch(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._switch(False)

    async def async_pulse(self, duration_ms: int) -> None:
        """Use the hardware pulse timer, never a Home Assistant delay to turn off."""
        await self._switch(True, duration_ms)
        task = self.hass.async_create_background_task(
            self._refresh_after_pulse(duration_ms),
            f"vkmodule-pulse-relay-{self._channel}",
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def _refresh_after_pulse(self, duration_ms: int) -> None:
        try:
            await asyncio.sleep(duration_ms / 1000 + 0.25)
            await self._client.refresh_state()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, TimeoutError, ProtocolError) as exc:
            _LOGGER.debug("VKmodule pulse refresh skipped: %s", exc)

    async def async_will_remove_from_hass(self) -> None:
        for task in tuple(self._refresh_tasks):
            task.cancel()
        await super().async_will_remove_from_hass()
