# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Config flow for Nespresso Smart BLE integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    DOMAIN,
    MACHINE_FAMILY_NAMES,
    SERVICE_UUID_TO_FAMILY,
    MachineFamily,
)

_LOGGER = logging.getLogger(__name__)


class NespressoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nespresso BLE machines."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._family: MachineFamily | None = None
        self._name: str = "Nespresso"

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle BLE device discovery."""
        _LOGGER.debug(
            "BLE discovery: address=%s name=%r service_uuids=%s rssi=%s "
            "manufacturer_data=%s service_data=%s",
            discovery_info.address,
            discovery_info.name,
            discovery_info.service_uuids,
            discovery_info.rssi,
            {k: v.hex() for k, v in discovery_info.manufacturer_data.items()}
            if discovery_info.manufacturer_data
            else {},
            {k: v.hex() for k, v in discovery_info.service_data.items()}
            if discovery_info.service_data
            else {},
        )
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._family = self._detect_family(discovery_info)
        family_label = (
            MACHINE_FAMILY_NAMES.get(self._family, "Unknown")
            if self._family
            else "Unknown"
        )

        self._name = discovery_info.name or f"Nespresso {family_label}"
        self.context["title_placeholders"] = {"name": self._name}

        _LOGGER.debug(
            "Detected family=%s name=%r for %s",
            self._family,
            self._name,
            discovery_info.address,
        )
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovered device."""
        if user_input is not None:
            data: dict[str, Any] = {
                "address": self._discovery_info.address  # type: ignore[union-attr]
                if self._discovery_info
                else "",
                "family": self._family.value
                if self._family
                else MachineFamily.VERTUO_NEXT.value,
                "name": self._name,
            }
            token = user_input.get("auth_token", "").strip()
            if token:
                data["auth_key"] = token
            return self.async_create_entry(title=self._name, data=data)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name},
            data_schema=vol.Schema(
                {
                    vol.Optional("auth_token", default=""): str,
                }
            ),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup: direct user to auto-discovery."""
        return self.async_abort(reason="bluetooth_only")

    @staticmethod
    def _detect_family(
        info: BluetoothServiceInfoBleak,
    ) -> MachineFamily | None:
        """Determine machine family from advertised service UUIDs."""
        for uuid in info.service_uuids:
            family = SERVICE_UUID_TO_FAMILY.get(uuid.lower())
            if family is not None:
                return family
        return None
