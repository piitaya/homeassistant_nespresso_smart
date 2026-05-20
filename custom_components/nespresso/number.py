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

"""Number entities for Nespresso Smart integration."""

from __future__ import annotations

import logging

from bleak import BleakError
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import (
    DOMAIN,
    MACHINE_FAMILY_NAMES,
    VERTUO_CHAR_USER_SETTINGS,
    MachineFamily,
)
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    entities: list[NumberEntity] = []
    if family == MachineFamily.VERTUO_NEXT:
        entities.append(NespressoWaterHardness(coordinator, entry))
        entities.append(NespressoAutoPowerOff(coordinator, entry))
    async_add_entities(entities)


class NespressoWaterHardness(NespressoEntity, NumberEntity):
    """Number entity for Vertuo Next water hardness setting."""

    _attr_name = "Water hardness"
    _attr_icon = "mdi:water"
    _attr_native_min_value = 0
    _attr_native_max_value = 6
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_water_hardness_setting"
        family = MachineFamily(entry.data["family"])
        data = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=entry.data.get("name", "Nespresso"),
            manufacturer="Nespresso",
            model=MACHINE_FAMILY_NAMES.get(family, "Unknown"),
            serial_number=data.serial_number if data else None,
            sw_version=data.firmware_version if data else None,
            hw_version=data.hardware_version if data else None,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current water hardness level."""
        if self.coordinator.data is None:
            return None
        wh = self.coordinator.data.water_hardness
        return float(wh) if wh is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Write water hardness to machine via BLE.

        Writes all 4 bytes of CHAR_GENERAL_USER_SETTINGS:
          bytes 0-1: machineAPOTime (LSB, preserved from current)
          byte 2: waterHardness (new value)
          byte 3: activeTime2StandBy (preserved from current)
        """
        _LOGGER.debug("Writing water hardness: value=%d", int(value))
        int_val = int(value)

        def modify(data: bytearray) -> None:
            if len(data) >= 4:
                data[2] = int_val & 0xFF

        try:
            await self.coordinator.async_read_modify_write_char(
                VERTUO_CHAR_USER_SETTINGS, modify
            )
            _LOGGER.info("Water hardness set to %d", int_val)
            await self.coordinator.async_request_refresh()
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to set water hardness: %s", err)


class NespressoAutoPowerOff(NespressoEntity, NumberEntity):
    """Number entity for Vertuo Next auto power off time."""

    _attr_name = "Auto power off"
    _attr_icon = "mdi:timer-off-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = 65535
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_auto_power_off_setting"
        family = MachineFamily(entry.data["family"])
        data = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=entry.data.get("name", "Nespresso"),
            manufacturer="Nespresso",
            model=MACHINE_FAMILY_NAMES.get(family, "Unknown"),
            serial_number=data.serial_number if data else None,
            sw_version=data.firmware_version if data else None,
            hw_version=data.hardware_version if data else None,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current auto power off time."""
        if self.coordinator.data is None:
            return None
        apo = self.coordinator.data.auto_power_off
        return float(apo) if apo is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Write auto power off time to machine via BLE.

        Writes all 4 bytes of CHAR_GENERAL_USER_SETTINGS:
          bytes 0-1: machineAPOTime (LSB, new value)
          byte 2: waterHardness (preserved)
          byte 3: activeTime2StandBy (preserved)
        """
        _LOGGER.debug("Writing auto power off: value=%d", int(value))
        int_val = int(value) & 0xFFFF

        def modify(data: bytearray) -> None:
            if len(data) >= 4:
                data[0] = int_val & 0xFF
                data[1] = (int_val >> 8) & 0xFF

        try:
            await self.coordinator.async_read_modify_write_char(
                VERTUO_CHAR_USER_SETTINGS, modify
            )
            _LOGGER.info("Auto power off set to %d", int(value))
            await self.coordinator.async_request_refresh()
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to set auto power off: %s", err)
