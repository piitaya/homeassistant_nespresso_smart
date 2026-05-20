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

"""Event entities for Nespresso Smart integration."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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

ALL_EVENT_TYPES = sorted(
    set(BARISTA_STATE_NAMES.values()) | set(VERTUO_STATE_NAMES.values()) | {"unknown"}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nespresso event entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]
    family = MachineFamily(entry.data["family"])

    if family in (MachineFamily.BARISTA, MachineFamily.VERTUO_NEXT):
        async_add_entities([NespressoStateChangeEvent(coordinator, entry)])


class NespressoStateChangeEvent(NespressoEntity, EventEntity):
    """Event entity that fires when the machine state changes."""

    _attr_name = "State change"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ALL_EVENT_TYPES

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_state_change"
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
        self._last_state: str | None = data.machine_state if data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire event when machine state changes."""
        if self.coordinator.data is None:
            return
        new_state = self.coordinator.data.machine_state
        if self._last_state is not None and new_state != self._last_state:
            self._trigger_event(new_state, {"previous": self._last_state})
        self._last_state = new_state
        self.async_write_ha_state()
