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

"""Sensor entities for Nespresso Smart integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import (
    BARISTA_STATE_NAMES,
    DOMAIN,
    MACHINE_FAMILY_NAMES,
    VERTUO_STATE_NAMES,
    MachineFamily,
)
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity
from .models import NespressoMachineData
from .timer_sensor import NespressoBrewingDuration

ALL_STATE_OPTIONS: list[str] = sorted(
    set(BARISTA_STATE_NAMES.values()) | set(VERTUO_STATE_NAMES.values()) | {"unknown"}
)


@dataclass(frozen=True)
class NespressoSensorDescription(SensorEntityDescription):
    """Sensor description with machine family filter."""

    families: frozenset[MachineFamily] = frozenset(MachineFamily)
    value_fn: Callable[[NespressoMachineData], str | int | None] = lambda _: None


SENSOR_DESCRIPTIONS: tuple[NespressoSensorDescription, ...] = (
    NespressoSensorDescription(
        key="machine_state",
        translation_key="machine_state",
        name="State",
        icon="mdi:coffee-maker",
        device_class=SensorDeviceClass.ENUM,
        options=ALL_STATE_OPTIONS,
        families=frozenset({MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.machine_state,
    ),
    NespressoSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        name="Firmware version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware_version,
    ),
    NespressoSensorDescription(
        key="hardware_version",
        translation_key="hardware_version",
        name="Hardware version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.hardware_version,
    ),
    NespressoSensorDescription(
        key="recipe_count",
        translation_key="recipe_count",
        name="Recipe slots",
        icon="mdi:book-open-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.BARISTA}),
        value_fn=lambda d: d.recipe_count,
    ),
    NespressoSensorDescription(
        key="profile_version",
        translation_key="profile_version",
        name="Profile version",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.profile_version,
    ),
    NespressoSensorDescription(
        key="bootloader_version",
        translation_key="bootloader_version",
        name="Bootloader version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.bootloader_version,
    ),
    NespressoSensorDescription(
        key="bluetooth_version",
        translation_key="bluetooth_version",
        name="Bluetooth version",
        icon="mdi:bluetooth",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.BARISTA}),
        value_fn=lambda d: d.bluetooth_version,
    ),
    NespressoSensorDescription(
        key="recipe_db_version",
        translation_key="recipe_db_version",
        name="Recipe database version",
        icon="mdi:database-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.recipe_db_version,
    ),
    NespressoSensorDescription(
        key="connectivity_fw_version",
        translation_key="connectivity_fw_version",
        name="Connectivity firmware",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.connectivity_fw_version,
    ),
    NespressoSensorDescription(
        key="error_code",
        translation_key="error_code",
        name="Error code",
        icon="mdi:alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.error_code,
    ),
    NespressoSensorDescription(
        key="error_list_code",
        translation_key="error_list_code",
        name="Error log code",
        icon="mdi:alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.error_list_code,
    ),
    NespressoSensorDescription(
        key="iot_market_name",
        translation_key="iot_market_name",
        name="IoT market",
        icon="mdi:earth",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.iot_market_name,
    ),
    NespressoSensorDescription(
        key="caps_counter",
        translation_key="caps_counter",
        name="Capsule counter",
        icon="mdi:counter",
        families=frozenset({MachineFamily.VERTUO_NEXT}),
        value_fn=lambda d: d.caps_counter,
    ),
    NespressoSensorDescription(
        key="shadow_data",
        translation_key="shadow_data",
        name="Device shadow",
        icon="mdi:cloud-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VMINI}),
        value_fn=lambda d: d.shadow_data,
    ),
    NespressoSensorDescription(
        key="fota_status",
        translation_key="fota_status",
        name="Firmware update status",
        icon="mdi:cellphone-arrow-down",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VMINI}),
        value_fn=lambda d: d.fota_status,
    ),
    NespressoSensorDescription(
        key="fota_progress",
        translation_key="fota_progress",
        name="Firmware update progress",
        icon="mdi:progress-download",
        entity_category=EntityCategory.DIAGNOSTIC,
        families=frozenset({MachineFamily.VMINI}),
        value_fn=lambda d: d.fota_progress,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    entities: list[SensorEntity] = [
        NespressoSensor(coordinator, entry, desc)
        for desc in SENSOR_DESCRIPTIONS
        if family in desc.families
    ]

    # Real-time brewing duration sensor
    if family in (MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT):
        entities.append(NespressoBrewingDuration(coordinator, entry))

    async_add_entities(entities)


class NespressoSensor(NespressoEntity, SensorEntity):
    """A Nespresso machine sensor."""

    entity_description: NespressoSensorDescription

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
        description: NespressoSensorDescription,
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
    def native_value(self) -> str | int | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
