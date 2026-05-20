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

"""Binary sensor entities for Nespresso Smart integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, MACHINE_FAMILY_NAMES, MachineFamily
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity
from .models import NespressoMachineData


@dataclass(frozen=True)
class NespressoBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with machine family filter."""

    families: frozenset[MachineFamily] = frozenset(MachineFamily)
    value_fn: Callable[[NespressoMachineData], bool | None] = lambda _: None


BINARY_SENSOR_DESCRIPTIONS: tuple[NespressoBinarySensorDescription, ...] = (
    NespressoBinarySensorDescription(
        key="error_present",
        translation_key="error_present",
        name="Error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        families=frozenset({MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.error_present,
    ),
    NespressoBinarySensorDescription(
        key="water_tank_empty",
        translation_key="water_tank_empty",
        name="Water tank empty",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:cup-water",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.water_tank_empty,
    ),
    NespressoBinarySensorDescription(
        key="descaling_needed",
        translation_key="descaling_needed",
        name="Descaling needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle-outline",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.descaling_needed,
    ),
    NespressoBinarySensorDescription(
        key="cleaning_needed",
        translation_key="cleaning_needed",
        name="Cleaning needed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:broom",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.cleaning_needed,
    ),
    NespressoBinarySensorDescription(
        key="capsule_container_full",
        translation_key="capsule_container_full",
        name="Capsule container full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:delete-variant",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.capsule_container_full,
    ),
    NespressoBinarySensorDescription(
        key="induction_heating",
        translation_key="induction_heating",
        name="Induction heater",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:radiator",
        families=frozenset({MachineFamily.BARISTA}),
        value_fn=lambda d: d.induction_heating,
    ),
    NespressoBinarySensorDescription(
        key="milk_frother_running",
        translation_key="milk_frother_running",
        name="Milk frother",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:blender",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.milk_frother_running,
    ),
    NespressoBinarySensorDescription(
        key="led_signaling",
        translation_key="led_signaling",
        name="LED signaling",
        icon="mdi:led-on",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.led_signaling,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso binary sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    entities = [
        NespressoBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSOR_DESCRIPTIONS
        if family in desc.families
    ]
    async_add_entities(entities)


class NespressoBinarySensor(NespressoEntity, BinarySensorEntity):
    """A Nespresso machine binary sensor."""

    entity_description: NespressoBinarySensorDescription

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
        description: NespressoBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data['address']}_{description.key}"
        family = MachineFamily(entry.data["family"])
        data = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["address"])},
            name=entry.data.get("name", "Nespresso"),
            manufacturer="Nespresso",
            model=MACHINE_FAMILY_NAMES.get(family, "Unknown"),
            serial_number=data.serial_number if data else None,
            sw_version=data.firmware_version if data else None,
            hw_version=data.hardware_version if data else None,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
