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

"""Coordinator for Nespresso BLE machines.

Polls are driven by advertisements via ``ActiveBluetoothDataUpdateCoordinator``.
All BLE connection lifecycle lives in :class:`.ble.client.NespressoClient`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback

from .ble.client import NespressoClient
from .ble.protocol import generate_auth_key
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MachineFamily
from .models import NespressoMachineData

_LOGGER = logging.getLogger(__name__)


class NespressoCoordinator(ActiveBluetoothDataUpdateCoordinator[NespressoMachineData]):
    """Coordinator that polls a Nespresso machine on advertisement events."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        family: MachineFamily,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll_device,
            connectable=True,
        )
        self.family = family
        self.auth_key: str | None = None
        self.brew_type: str = "espresso"
        self.brew_temperature: str = "medium"
        self._ble_lock = asyncio.Lock()
        self._device_id: str | None = None

    def set_device_id(self, device_id: str) -> None:
        """Set the HA device ID for event firing."""
        self._device_id = device_id

    @callback
    def _needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Poll once per ``DEFAULT_SCAN_INTERVAL`` if a connectable device is cached."""
        return (
            self.hass.state is CoreState.running
            and (
                seconds_since_last_poll is None
                or seconds_since_last_poll >= DEFAULT_SCAN_INTERVAL
            )
            and bool(
                async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
            )
        )

    async def async_request_refresh(self) -> None:
        """Trigger an immediate poll, bypassing the poll-interval gate.

        Used by the brew flow to drive state-transition loops.
        """
        if self._last_service_info is None:
            _LOGGER.debug("No advertisement yet; refresh skipped")
            return
        await self._debounced_poll.async_call()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[NespressoClient]:
        """Open an authenticated BLE session under the BLE lock.

        Entities that need to write or send commands use this.

            async with coordinator.session() as client:
                await client.write_char(...)
        """
        device = (
            self._last_service_info.device if self._last_service_info else None
        ) or async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise RuntimeError("Machine not currently reachable")
        if self.auth_key is None:
            self.auth_key = generate_auth_key()
        async with self._ble_lock:
            async with NespressoClient(
                device, self.address, self.family, self.auth_key
            ) as client:
                yield client

    async def async_write_char(self, char_uuid: str, data: bytes) -> None:
        """One-shot write to a characteristic."""
        async with self.session() as client:
            await client.write_char(char_uuid, data)

    async def async_read_modify_write_char(
        self, char_uuid: str, modify_fn: Callable[[bytearray], None]
    ) -> None:
        """One-shot read-modify-write of a characteristic."""
        async with self.session() as client:
            await client.read_modify_write_char(char_uuid, modify_fn)

    async def _async_poll_device(
        self, service_info: BluetoothServiceInfoBleak
    ) -> NespressoMachineData:
        """Poll callback wired into the active coordinator."""
        if self.auth_key is None:
            self.auth_key = generate_auth_key()
        async with self._ble_lock:
            async with NespressoClient(
                service_info.device, self.address, self.family, self.auth_key
            ) as client:
                new_data = await client.read_state()
        # ``self.data`` still holds the old value here — parent assigns after
        # we return — so the trigger sees the real transition.
        self._fire_state_triggers(new_data)
        _LOGGER.debug(
            "Poll OK %s: state=%s error=%s fw=%s",
            self.family.value,
            new_data.machine_state,
            new_data.error_present,
            new_data.firmware_version,
        )
        return new_data

    def _fire_state_triggers(self, new_data: NespressoMachineData) -> None:
        """Fire bus events for device triggers on state changes."""
        if self._device_id is None or self.data is None:
            return
        old_state = self.data.machine_state
        new_state = new_data.machine_state
        if old_state == new_state:
            return

        triggers = []
        if new_state == "brewing":
            triggers.append("brewing_started")
        if old_state == "brewing":
            triggers.append("brewing_finished")
        if new_state == "error":
            triggers.append("error_occurred")
        if new_state == "ready":
            triggers.append("ready")
        if new_state == "standby":
            triggers.append("standby")

        _LOGGER.debug(
            "State transition: %s -> %s, triggers=%s", old_state, new_state, triggers
        )
        for trigger_type in triggers:
            self.hass.bus.async_fire(
                f"{DOMAIN}_state_change",
                {
                    "device_id": self._device_id,
                    "type": trigger_type,
                    "old_state": old_state,
                    "new_state": new_state,
                },
            )

