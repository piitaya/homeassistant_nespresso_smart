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

"""Real-time brewing duration sensor for Nespresso Smart integration.

Updates every second while the machine is brewing, using the same
async_track_time_interval pattern as the Philips HomeID integration.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from .const import DOMAIN, MACHINE_FAMILY_NAMES, MachineFamily
from .coordinator import NespressoCoordinator
from .entity import NespressoEntity

BREWING_STATES = {"brewing"}


class NespressoBrewingDuration(NespressoEntity, SensorEntity):
    """Sensor that shows elapsed brewing time, updating every second."""

    _attr_name = "Brewing duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "s"

    def __init__(
        self,
        coordinator: NespressoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._address = entry.data["address"]
        self._attr_unique_id = f"{self._address}_brewing_duration"
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
        self._brew_start: float | None = None
        self._countdown_unsub: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Start timer if already brewing when entity loads."""
        await super().async_added_to_hass()
        self._update_timer()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timer on removal."""
        self._stop_timer()
        await super().async_will_remove_from_hass()

    def _is_brewing(self) -> bool:
        """Check if machine is currently brewing."""
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.machine_state in BREWING_STATES

    def _start_timer(self) -> None:
        """Start ticking every second."""
        if self._countdown_unsub is not None:
            return

        @callback
        def _tick(_: Any) -> None:
            self.async_write_ha_state()

        self._countdown_unsub = async_track_time_interval(
            self.hass, _tick, timedelta(seconds=1)
        )

    def _stop_timer(self) -> None:
        """Stop the per-second tick."""
        if self._countdown_unsub is not None:
            self._countdown_unsub()
            self._countdown_unsub = None

    def _update_timer(self) -> None:
        """Start or stop timer based on brewing state."""
        if self._is_brewing():
            if self._brew_start is None:
                self._brew_start = time.monotonic()
            self._start_timer()
        else:
            self._stop_timer()
            if self._brew_start is not None:
                self._brew_start = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """React to coordinator data changes."""
        self._update_timer()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> int | None:
        """Return elapsed brewing time in seconds."""
        if self._brew_start is None:
            return 0
        return int(time.monotonic() - self._brew_start)
