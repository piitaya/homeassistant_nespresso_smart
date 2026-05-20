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

"""Select entities for Nespresso Smart integration."""

from __future__ import annotations

import logging

from bleak import BleakError
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import (
    BARISTA_CHAR_LANGUAGE,
    BARISTA_CHAR_RECIPE_SELECTION,
    DOMAIN,
    MACHINE_FAMILY_NAMES,
    MachineFamily,
)
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity

_LOGGER = logging.getLogger(__name__)

BARISTA_LANGUAGES = [
    "en",
    "fr",
    "de",
    "it",
    "es",
    "pt",
    "nl",
    "ja",
    "ko",
    "zh",
    "ru",
]

BARISTA_RECIPES = [
    "espresso",
    "lungo",
    "extra_lungo",
    "cappuccino",
    "latte_macchiato",
    "custom",
]

VERTUO_BREW_TYPES = [
    "ristretto",
    "espresso",
    "lungo",
    "hot_water",
    "americano",
]

VERTUO_BREW_TYPE_VALUES: dict[str, int] = {
    "ristretto": 0,
    "espresso": 1,
    "lungo": 2,
    "hot_water": 4,
    "americano": 5,
}

VERTUO_TEMPERATURES = [
    "low",
    "medium",
    "high",
]

VERTUO_TEMPERATURE_VALUES: dict[str, int] = {
    "low": 1,
    "medium": 0,
    "high": 2,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    entities: list[SelectEntity] = []
    if family == MachineFamily.BARISTA:
        entities.append(NespressoRecipeSelect(coordinator, entry))
        entities.append(NespressoLanguageSelect(coordinator, entry))
    if family == MachineFamily.VERTUO_NEXT:
        entities.append(NespressoVertuoBrewTypeSelect(coordinator, entry))
        entities.append(NespressoVertuoTemperatureSelect(coordinator, entry))
    async_add_entities(entities)


class NespressoRecipeSelect(NespressoEntity, SelectEntity):
    """Select entity for Barista recipe selection."""

    _attr_name = "Recipe"
    _attr_icon = "mdi:coffee"
    _attr_options = BARISTA_RECIPES
    _attr_current_option: str | None = None

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_recipe_select"
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

    async def async_select_option(self, option: str) -> None:
        """Write recipe index to machine via BLE."""
        if option not in BARISTA_RECIPES:
            _LOGGER.debug("Invalid recipe option: %r", option)
            return
        index = BARISTA_RECIPES.index(option)
        _LOGGER.debug("Writing recipe: option=%s index=%d", option, index)

        try:
            await self.coordinator.async_write_char(
                BARISTA_CHAR_RECIPE_SELECTION, bytes([index])
            )
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.info("Recipe set to %s (index %d)", option, index)
            await self.coordinator.async_request_refresh()
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to set recipe: %s", err)


class NespressoLanguageSelect(NespressoEntity, SelectEntity):
    """Select entity for Barista language setting."""

    _attr_name = "Language"
    _attr_icon = "mdi:translate"
    _attr_options = BARISTA_LANGUAGES
    _attr_current_option: str | None = None

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_language_select"
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

    async def async_select_option(self, option: str) -> None:
        """Write language code to machine via BLE."""
        if option not in BARISTA_LANGUAGES:
            _LOGGER.debug("Invalid language option: %r", option)
            return

        _LOGGER.debug(
            "Writing language: option=%s bytes=%s char=%s",
            option,
            option.encode("utf-8")[:2].hex(),
            BARISTA_CHAR_LANGUAGE,
        )

        try:
            lang_bytes = option.encode("utf-8")[:2].ljust(2, b"\x00")
            await self.coordinator.async_write_char(BARISTA_CHAR_LANGUAGE, lang_bytes)
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.info("Language set to %s", option)
        except (BleakError, TimeoutError) as err:
            _LOGGER.error("Failed to set language: %s", err)


class NespressoVertuoBrewTypeSelect(NespressoEntity, SelectEntity):
    """Select entity for Vertuo Next brew type."""

    _attr_name = "Brew type"
    _attr_icon = "mdi:coffee"
    _attr_options = VERTUO_BREW_TYPES
    _attr_current_option = "espresso"

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_vertuo_brew_type"
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

    async def async_select_option(self, option: str) -> None:
        """Select brew type (stored on coordinator, used by brew button)."""
        if option not in VERTUO_BREW_TYPES:
            return
        self.coordinator.brew_type = option
        self._attr_current_option = option
        self.async_write_ha_state()


class NespressoVertuoTemperatureSelect(NespressoEntity, SelectEntity):
    """Select entity for Vertuo Next brew temperature."""

    _attr_name = "Brew temperature"
    _attr_icon = "mdi:thermometer"
    _attr_options = VERTUO_TEMPERATURES
    _attr_current_option: str | None = "medium"

    async def async_select_option(self, option: str) -> None:
        """Select brew temperature (stored on coordinator, used by brew button)."""
        if option not in VERTUO_TEMPERATURES:
            return
        self.coordinator.brew_temperature = option
        self._attr_current_option = option
        self.async_write_ha_state()

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_vertuo_brew_temp"
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
