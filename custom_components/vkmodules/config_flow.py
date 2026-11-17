"""UI setup, reconfigure endpoint, and model-specific options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlowWithReload

from .client import VKmoduleClient
from .const import CONF_ANALOG_INPUT, DEFAULT_PORT, DOMAIN
from .protocol import ProtocolError, UnsupportedDevice


HOST_SCHEMA = vol.Schema({
    vol.Required("host"): str,
    vol.Optional("port", default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
})


async def probe(host: str, port: int, *, analog: bool = False) -> tuple[int, int, str]:
    """Identify hardware and UID without keeping a permanent TCP connection."""
    client = VKmoduleClient(host, port)
    client.analog_input = analog
    try:
        await client.connect()
        assert client.uid is not None and client.model is not None
        return client.uid, client.hw_type, client.model.name
    finally:
        await client.close()


class VKmoduleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover supported capabilities from 0x03 (hardware type) and 0x04."""

    VERSION = 1

    def __init__(self) -> None:
        self._detected: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input["host"].strip(), user_input["port"]
            if not host:
                errors["base"] = "cannot_connect"
            else:
                try:
                    uid, hw, model_name = await probe(host, port)
                except UnsupportedDevice:
                    errors["base"] = "unsupported_device"
                except (ConnectionError, OSError, TimeoutError, ProtocolError):
                    errors["base"] = "cannot_connect"
                else:
                    # Identify duplicates within the new vkmodules domain only.
                    for entry in self._async_current_entries():
                        if (entry.data.get("host"), entry.data.get("port", DEFAULT_PORT)) == (host, port):
                            return self.async_abort(reason="already_configured")
                        if entry.data.get("uid") == uid and entry.data.get("hw_type") == hw:
                            return self.async_abort(reason="already_configured")
                    await self.async_set_unique_id(f"{hw}:{uid}")
                    self._abort_if_unique_id_configured()
                    self._detected = {"host": host, "port": port, "uid": uid,
                                      "hw_type": hw, "model_name": model_name}
                    if hw == 2:
                        return await self.async_step_socket2_variant()
                    return self._create()
        return self.async_show_form(step_id="user", data_schema=HOST_SCHEMA, errors=errors)

    async def async_step_socket2_variant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Hardware ID does not distinguish SOCKET-2 from SOCKET-2W."""
        errors: dict[str, str] = {}
        if user_input is not None:
            analog = user_input[CONF_ANALOG_INPUT]
            if analog:
                try:
                    uid, _hw, _model = await probe(
                        self._detected["host"], self._detected["port"], analog=True
                    )
                    if uid != self._detected["uid"]:
                        errors["base"] = "device_mismatch"
                except (ConnectionError, OSError, TimeoutError, ProtocolError):
                    errors["base"] = "analog_unavailable"
            if not errors:
                self._detected[CONF_ANALOG_INPUT] = analog
                return self._create()
        return self.async_show_form(
            step_id="socket2_variant",
            data_schema=vol.Schema({vol.Required(CONF_ANALOG_INPUT, default=False): bool}),
            errors=errors,
        )

    def _create(self) -> ConfigFlowResult:
        info = self._detected
        # The analog flag is a UI preference: do not claim hardware detection.
        is_analog = info.pop(CONF_ANALOG_INPUT, False)
        model = "SOCKET-2W" if is_analog else info.pop("model_name")
        info.pop("model_name", None)
        return self.async_create_entry(
            title=f"VKmodule {model} ({info['host']})",
            data=info,
            options={CONF_ANALOG_INPUT: is_analog} if info["hw_type"] == 2 else {},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change host/port, verifying the physical UID to prevent wrong relays."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input["host"].strip(), user_input["port"]
            if not host:
                errors["base"] = "cannot_connect"
            else:
                try:
                    uid, hw, _model = await probe(host, port)
                except UnsupportedDevice:
                    errors["base"] = "unsupported_device"
                except (ConnectionError, OSError, TimeoutError, ProtocolError):
                    errors["base"] = "cannot_connect"
                else:
                    expected = entry.data.get("uid")
                    if expected is None:
                        data = getattr(entry, "runtime_data", None)
                        expected = data.client.uid if data else None
                    expected_hw = entry.data.get("hw_type")
                    if (expected is None or uid != int(expected)
                            or (expected_hw is not None and hw != int(expected_hw))):
                        errors["base"] = "device_mismatch"
                    else:
                        await self.async_set_unique_id(entry.unique_id or f"{hw}:{uid}")
                        self._abort_if_unique_id_mismatch()
                        return self.async_update_reload_and_abort(
                            entry, data_updates={"host": host, "port": port,
                                                 "uid": uid, "hw_type": hw})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(HOST_SCHEMA, entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> VKmoduleOptionsFlow:
        """Offer input polarity and opt-in SOCKET-2W analog mode."""
        return VKmoduleOptionsFlow()


class VKmoduleOptionsFlow(OptionsFlowWithReload):
    """Module-specific options; HA reloads entry automatically after saving."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (user_input.get(CONF_ANALOG_INPUT, False)
                    and not self.config_entry.options.get(CONF_ANALOG_INPUT, False)):
                try:
                    uid, _hw, _model = await probe(
                        self.config_entry.data["host"],
                        self.config_entry.data.get("port", DEFAULT_PORT),
                        analog=True,
                    )
                    if uid != self.config_entry.data.get("uid"):
                        errors["base"] = "device_mismatch"
                except (ConnectionError, OSError, TimeoutError, ProtocolError):
                    errors["base"] = "analog_unavailable"
            if not errors:
                return self.async_create_entry(data=user_input)
        schema = {vol.Required("invert_inputs", default=False): bool}
        if self.config_entry.data.get("hw_type") == 2:
            schema[vol.Required(CONF_ANALOG_INPUT, default=False)] = bool
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.config_entry.options),
            errors=errors,
        )
