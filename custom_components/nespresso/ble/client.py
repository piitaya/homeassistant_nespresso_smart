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

"""Owns a BLE session to a Nespresso machine.

Connection lifecycle, authentication, and stale-bond recovery live here.
Coordinator and entities just wrap calls in an ``async with`` block.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from ..const import BARISTA_CHAR_STATUS, VERTUO_CHAR_STATUS, MachineFamily
from ..models import NespressoMachineData
from .parsing import parse_barista_status, parse_vertuonext_status
from .protocol import _authenticate, get_protocol

_LOGGER = logging.getLogger(__name__)


class NespressoClient:
    """A short-lived authenticated BLE session.

    Use as an async context manager:

        async with NespressoClient(device, address, family, auth_key) as client:
            raw = await client.read_all()
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        address: str,
        family: MachineFamily,
        auth_key: str,
    ) -> None:
        self._ble_device = ble_device
        self._address = address
        self._family = family
        self._auth_key = auth_key
        self._client: BleakClient | None = None

    async def __aenter__(self) -> NespressoClient:
        self._client = await _connect_with_recovery(self._ble_device, self._address)
        await _ensure_bonded(self._client, self._address)
        if not self._client.is_connected:
            # pair() can drop the link on machines that reject the bond
            # (e.g. the iPhone app already claimed the bonding slot). Skip
            # auth on a dead connection — running GATT ops would only
            # produce noisy "Method doesn't exist" cascades.
            _LOGGER.warning(
                "BLE link dropped after pair attempt for %s; next poll will retry",
                self._address,
            )
            return self
        if not await _authenticate(self._client, self._auth_key, self._family):
            _LOGGER.warning(
                "Auth failed for %s; the next poll cycle will retry",
                self._address,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._client = None

    @property
    def raw_client(self) -> BleakClient:
        """The underlying BleakClient (only valid inside ``async with``)."""
        assert self._client is not None, "NespressoClient used outside async with"
        return self._client

    async def read_state(self) -> NespressoMachineData:
        """Read every characteristic and return the parsed machine state."""
        protocol = get_protocol(self._family)
        raw = await protocol.async_read_all(self.raw_client, self._auth_key)
        return protocol.parse(raw)

    async def read_status(self) -> dict[str, Any]:
        """Read just the machine status characteristic.

        Used by the brew flow to detect state transitions without a full poll.
        """
        if self._family == MachineFamily.BARISTA:
            data = await self.raw_client.read_gatt_char(BARISTA_CHAR_STATUS)
            return parse_barista_status(bytes(data))
        if self._family == MachineFamily.VERTUO_NEXT:
            data = await self.raw_client.read_gatt_char(VERTUO_CHAR_STATUS)
            return parse_vertuonext_status(bytes(data))
        return {}

    async def write_char(self, char_uuid: str, data: bytes) -> None:
        """Write to a BLE characteristic with response."""
        await self.raw_client.write_gatt_char(char_uuid, data, response=True)
        _LOGGER.debug("Write %s: %s", char_uuid, data.hex())

    async def read_modify_write_char(
        self, char_uuid: str, modify_fn: Callable[[bytearray], None]
    ) -> None:
        """Read a characteristic, mutate the bytes in place, write back."""
        current = await self.raw_client.read_gatt_char(char_uuid)
        _LOGGER.debug("Read-modify-write %s current: %s", char_uuid, current.hex())
        data = bytearray(current)
        modify_fn(data)
        _LOGGER.debug("Read-modify-write %s new: %s", char_uuid, data.hex())
        await self.raw_client.write_gatt_char(char_uuid, bytes(data), response=True)

    async def send_command(
        self, cmd_uuid: str, rsp_uuid: str, data: bytes, retries: int = 3
    ) -> bytes | None:
        """Send a command and wait for its response notification."""
        response: bytearray | None = None

        def on_notify(_sender: object, rsp_data: bytearray) -> None:
            nonlocal response
            response = rsp_data
            _LOGGER.debug("Command response: %s", rsp_data.hex())

        client = self.raw_client
        await client.start_notify(rsp_uuid, on_notify)
        try:
            for attempt in range(retries):
                response = None
                await client.write_gatt_char(cmd_uuid, data, response=True)
                _LOGGER.debug("Command write attempt %d: %s", attempt + 1, data.hex())
                for _ in range(5):
                    if response is not None:
                        break
                    await asyncio.sleep(1)
                if response is not None:
                    break
                await asyncio.sleep(1)
        finally:
            await client.stop_notify(rsp_uuid)
        return bytes(response) if response is not None else None

    async def bst_send(self, cmd_uuid: str, rsp_uuid: str, data: bytes) -> bool:
        """Send data via the BST recipe protocol."""
        from .bst import bst_send

        return await bst_send(self.raw_client, cmd_uuid, rsp_uuid, data)


async def _connect_with_recovery(
    ble_device: BLEDevice, address: str
) -> BleakClient:
    """Connect, clearing a stale BlueZ bond once on connection abort."""
    try:
        return await establish_connection(
            BleakClient, ble_device, address, max_attempts=3
        )
    except (BleakError, TimeoutError) as err:
        if "connection abort" not in str(err).lower():
            raise
        _LOGGER.info("Connection abort for %s, clearing stale BlueZ bond", address)
        try:
            tmp = BleakClient(ble_device)
            await tmp.unpair()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(3)
        return await establish_connection(
            BleakClient, ble_device, address, max_attempts=3
        )


async def _ensure_bonded(client: BleakClient, address: str) -> None:
    """Establish a BLE bond before touching protected characteristics.

    Required for machines that are already paired with another central
    (e.g. the official Nespresso iOS/Android app). Without this, BlueZ
    returns GATT error 15 (Insufficient encryption) on auth writes.
    The call is idempotent — already-paired devices return immediately.
    """
    try:
        await client.pair()
        _LOGGER.debug("BLE pair OK for %s", address)
        # On some BlueZ + ESPHome proxy combinations, pair() invalidates
        # the GATT services cache for a brief moment. Wait for the link
        # to stabilize before the auth flow starts writing characteristics.
        await asyncio.sleep(0.5)
        return
    except Exception as err:  # noqa: BLE001
        err_str = str(err).lower()

    if "authentication" in err_str and "failed" in err_str:
        # The machine refused our bond. Likely the iPhone app took the bonding
        # slot and the credentials we have are stale. Drop the local bond so
        # the next poll attempt starts from a clean BlueZ state.
        _LOGGER.info(
            "BLE pair rejected for %s, clearing stale local bond", address
        )
        try:
            await client.unpair()
        except Exception:  # noqa: BLE001
            pass
        return

    # Common benign cases: backend doesn't support pair (macOS), already
    # paired, or proxy that bonds transparently. Auth will fail clearly if
    # the bond actually wasn't established.
    _LOGGER.debug("BLE pair skipped for %s: %s", address, err_str)
