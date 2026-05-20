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

"""Button entities for Nespresso Smart integration."""

from __future__ import annotations

import asyncio
import logging
import time

from bleak import BleakError
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MACHINE_FAMILY_NAMES,
    VERTUO_CHAR_COMMAND_REQ,
    VERTUO_CHAR_COMMAND_RSP,
    VMINI_CHAR_FOTA_COMMAND,
    MachineFamily,
)
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity

_LOGGER = logging.getLogger(__name__)

WAKING_STATES = frozenset({"power_save", "standby"})
WAITING_STATES = frozenset({"heating", "initializing", "ready_old_capsule"})
WAKE_TIMEOUT_SECONDS = 300
STATE_POLL_INTERVAL = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    entities: list[ButtonEntity] = []
    if family == MachineFamily.VMINI:
        entities.append(NespressoFotaCheckButton(coordinator, entry))
    if family == MachineFamily.VERTUO_NEXT:
        entities.append(NespressoVertuoBrewButton(coordinator, entry))
    async_add_entities(entities)


def _make_device_info(coordinator: NespressoCoordinator, entry: ConfigEntry) -> DeviceInfo:
    family = MachineFamily(entry.data["family"])
    data = coordinator.data
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data["address"])},
        name=entry.data.get("name", "Nespresso"),
        manufacturer="Nespresso",
        model=MACHINE_FAMILY_NAMES.get(family, "Unknown"),
        serial_number=data.serial_number if data else None,
        sw_version=data.firmware_version if data else None,
        hw_version=data.hardware_version if data else None,
    )


class NespressoFotaCheckButton(NespressoEntity, ButtonEntity):
    """Button to check for firmware updates on VMini."""

    _attr_name = "Check firmware update"
    _attr_icon = "mdi:cellphone-arrow-down"

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_fota_check"
        self._attr_device_info = _make_device_info(coordinator, entry)

    async def async_press(self) -> None:
        """Send CHECK_FOR_UPDATE command (0x00) to CHAR_FOTA_COMMAND."""
        try:
            await self.coordinator.async_write_char(
                VMINI_CHAR_FOTA_COMMAND, bytes([0x00])
            )
            _LOGGER.info("FOTA check command sent to %s", self._address)
            await self.coordinator.async_request_refresh()
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to send FOTA check: %s", err)


class NespressoVertuoBrewButton(NespressoEntity, ButtonEntity):
    """Button to start brewing on Vertuo Next."""

    _attr_name = "Brew"
    _attr_icon = "mdi:coffee"

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._brew_pending = False
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_vertuo_brew"
        self._attr_device_info = _make_device_info(coordinator, entry)

    async def async_press(self) -> None:
        """Wait for the machine to be ready, then send the brew command.

        Only one brew at a time. Duplicate presses while a brew is queued
        are ignored.
        """
        if self._brew_pending:
            _LOGGER.debug("Brew already pending, ignoring duplicate press")
            return
        self._brew_pending = True
        try:
            state = await self._wait_until_ready()
            if state != "ready":
                return
            await self._send_brew_command()
        finally:
            self._brew_pending = False
        await self.coordinator.async_request_refresh()

    def _current_state(self) -> str | None:
        data = self.coordinator.data
        return data.machine_state if data else None

    async def _wait_until_ready(self) -> str | None:
        """Poll-driven wait for the machine to reach ``ready``.

        Returns the final observed state. A non-``ready`` return means the
        caller should abort the brew (timeout, error, or unsupported state).
        """
        state = self._current_state()
        if state in WAKING_STATES:
            state = await self._wait_for_wake(state)
            if state in WAKING_STATES:
                return state

        if state == "ready_old_capsule":
            await self._notify(
                "nespresso_capsule",
                "Nespresso: replace capsule",
                "Please eject the used capsule and insert a fresh one. "
                "Brewing will start automatically when the machine is ready.",
            )

        if state in WAITING_STATES:
            _LOGGER.info("Machine is %s, waiting for ready...", state)
            while state in WAITING_STATES:
                await asyncio.sleep(STATE_POLL_INTERVAL)
                await self.coordinator.async_request_refresh()
                state = self._current_state()

        await self._dismiss_notification("nespresso_capsule")

        if state != "ready":
            _LOGGER.error("Machine is in state '%s', cannot brew", state)
            await self._notify(
                "nespresso_brew_error",
                "Nespresso: cannot brew",
                f"The machine is in state '{state}' and cannot brew. "
                "It needs to be in 'ready' state.",
            )
        return state

    async def _wait_for_wake(self, state: str | None) -> str | None:
        """Hold until the user wakes the machine, or timeout."""
        await self._notify(
            "nespresso_power_save",
            "Nespresso: machine asleep",
            "The machine is in power save mode. "
            "Press the button on the machine to wake it up. "
            "Brewing will start automatically when the machine is ready.",
        )
        _LOGGER.info("Machine is %s, waiting up to 5 min for wake...", state)
        deadline = time.monotonic() + WAKE_TIMEOUT_SECONDS
        while state in WAKING_STATES and time.monotonic() < deadline:
            await asyncio.sleep(STATE_POLL_INTERVAL)
            await self.coordinator.async_request_refresh()
            state = self._current_state()
        await self._dismiss_notification("nespresso_power_save")
        if state in WAKING_STATES:
            _LOGGER.error("Timeout waiting for machine to wake up")
        return state

    async def _send_brew_command(self) -> None:
        """Send the brew command on a fresh session, with BST fallback."""
        from .ble.bst import encode_recipe_data
        from .select import VERTUO_BREW_TYPE_VALUES, VERTUO_TEMPERATURE_VALUES

        brew_type = VERTUO_BREW_TYPE_VALUES.get(self.coordinator.brew_type, 1)
        temp = VERTUO_TEMPERATURE_VALUES.get(self.coordinator.brew_temperature, 0)

        # Simple CCommandReq (works on Vertuo Next).
        buf = bytearray(10)
        buf[0] = 3  # cmdID: machine command
        buf[1] = 5  # subCmdID: start brew
        buf[2] = 7  # dataControl: dataLength=7
        buf[3] = 4  # data[0]: brew subtype
        buf[8] = temp  # data[5]: temperature
        buf[9] = brew_type  # data[6]: brew type

        _LOGGER.info(
            "Brewing on %s: type=%d temp=%d cmd=%s",
            self._address,
            brew_type,
            temp,
            buf.hex(),
        )

        try:
            async with self.coordinator.session() as client:
                rsp = await client.send_command(
                    VERTUO_CHAR_COMMAND_REQ, VERTUO_CHAR_COMMAND_RSP, bytes(buf)
                )
                if rsp:
                    _LOGGER.info("Brew response from %s: %s", self._address, rsp.hex())
                    return

                _LOGGER.info("No response to simple brew, trying BST recipe")
                recipe_data = encode_recipe_data(
                    "3/0/1000/0/500/0/0/2/94/85/155/498/0/50/0/0/0"
                )
                ok = await client.bst_send(
                    VERTUO_CHAR_COMMAND_REQ, VERTUO_CHAR_COMMAND_RSP, recipe_data
                )
                if ok:
                    _LOGGER.info("BST recipe sent to %s", self._address)
                else:
                    _LOGGER.warning("BST recipe failed on %s", self._address)
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to send brew command: %s", err)

    async def _notify(self, notification_id: str, title: str, message: str) -> None:
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {"message": message, "title": title, "notification_id": notification_id},
        )

    async def _dismiss_notification(self, notification_id: str) -> None:
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": notification_id},
        )
