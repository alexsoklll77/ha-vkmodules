"""Typed configuration-entry runtime data."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .client import VKmoduleClient


@dataclass(slots=True)
class VKmoduleRuntimeData:
    client: VKmoduleClient
    device_info: DeviceInfo


VKmoduleConfigEntry = ConfigEntry[VKmoduleRuntimeData]
