"""SOCKET-3 temperature and SOCKET-2W analog voltage sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .runtime_data import VKmoduleConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VKmoduleConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    client = entry.runtime_data.client
    assert client.model is not None
    entities: list[SensorEntity] = [
        VKmoduleTemperature(entry, i) for i in range(client.model.temp_count)
    ]
    if client.model.hardware_id == 2 and client.analog_input:
        entities.append(VKmoduleVoltage(entry))
    async_add_entities(entities)


class VKmoduleTemperature(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: VKmoduleConfigEntry, channel: int) -> None:
        self._client = entry.runtime_data.client
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}-temp-{channel}"
        self._attr_translation_placeholders = {"channel": str(channel + 1)}
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def native_value(self) -> int | None:
        return self._client.temperatures[self._channel]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self._state_changed, "temperature", self._channel))

    @callback
    def _state_changed(self) -> None:
        self.async_write_ha_state()


class VKmoduleVoltage(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, entry: VKmoduleConfigEntry) -> None:
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{entry.entry_id}-analog-0"
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def native_value(self) -> float | None:
        return self._client.analog_voltage

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self._state_changed, "analog", 0))

    @callback
    def _state_changed(self) -> None:
        self.async_write_ha_state()
