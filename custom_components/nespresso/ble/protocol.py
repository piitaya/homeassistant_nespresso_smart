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

"""BLE protocol implementations for Nespresso machine families.

Each protocol class knows which GATT characteristics to read for its
machine family. All I/O happens here; parsing is delegated to parsing.py.
"""

from __future__ import annotations

import asyncio
import binascii
import logging
import uuid
from abc import ABC, abstractmethod

from bleak import BleakClient

from ..const import (
    BARISTA_CHAR_AUTH,
    BARISTA_CHAR_INFO,
    BARISTA_CHAR_MACHINE_PARAMS,
    BARISTA_CHAR_PROFILE_VERSION,
    BARISTA_CHAR_RECIPE_INFO,
    BARISTA_CHAR_ONBOARD_STATUS,
    BARISTA_CHAR_PAIR,
    BARISTA_CHAR_SERIAL,
    BARISTA_CHAR_STATUS,
    VERTUO_CHAR_AUTH,
    VERTUO_CHAR_CAPS_COUNTER,
    VERTUO_CHAR_COMMAND_RSP,
    VERTUO_CHAR_ERROR_INFO,
    VERTUO_CHAR_ERROR_SELECTION,
    VERTUO_CHAR_INFO,
    VERTUO_CHAR_IOT_MARKET,
    VERTUO_CHAR_MACHINE_PARAMS,
    VERTUO_CHAR_ONBOARD_STATUS,
    VERTUO_CHAR_PAIR,
    VERTUO_CHAR_PROFILE_VERSION,
    VERTUO_CHAR_SERIAL,
    VERTUO_CHAR_STATUS,
    VERTUO_CHAR_USER_SETTINGS,
    VMINI_CHAR_MACHINE_TOKEN,
    VMINI_CHAR_FOTA_STATUS,
    VMINI_CHAR_FW_REV,
    VMINI_CHAR_MANUFACTURER,
    VMINI_CHAR_MODEL,
    VMINI_CHAR_PAIRING,
    VMINI_CHAR_SERIAL,
    VMINI_CHAR_SHADOW_HEADER,
    VMINI_CHAR_SW_REV,
    VMINI_CHAR_WIFI_CURRENT,
    VMINI_CHAR_WIFI_MAC,
    MachineFamily,
)
from ..models import NespressoMachineData, RawMachineData
from .parsing import (
    parse_barista_machine_info,
    parse_barista_machine_params,
    parse_barista_status,
    parse_caps_counter,
    parse_error_information,
    parse_general_user_settings,
    parse_profile_version,
    parse_serial_number,
    parse_vertuonext_machine_info,
    parse_vertuonext_status,
    parse_vmini_fota_status,
)
from .recipe import parse_recipe_info

_LOGGER = logging.getLogger(__name__)


def _decode_ble_string(data: bytes) -> str:
    """Decode a null-terminated BLE string characteristic."""
    return data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def generate_auth_key() -> str:
    """Generate a random 16-hex-char auth key for machine onboarding."""
    return uuid.uuid4().hex[:16]


# "pair" key maps to CHAR_TX_LEVEL_CHANGE_REQUEST in the APK.
# Writing 0x01 (REDUCE_POWER) initiates the pairing/onboarding sequence.
_AUTH_UUIDS: dict[str, dict[str, str]] = {
    MachineFamily.BARISTA: {
        "auth": BARISTA_CHAR_AUTH,
        "onboard": BARISTA_CHAR_ONBOARD_STATUS,
        "pair": BARISTA_CHAR_PAIR,
        "verify": BARISTA_CHAR_STATUS,
    },
    MachineFamily.VERTUO_NEXT: {
        "auth": VERTUO_CHAR_AUTH,
        "onboard": VERTUO_CHAR_ONBOARD_STATUS,
        "pair": VERTUO_CHAR_PAIR,
        "verify": VERTUO_CHAR_STATUS,
    },
}


async def _authenticate(
    client: BleakClient, auth_key: str, family: MachineFamily
) -> bool:
    """Authenticate with the Nespresso machine.

    Matches the APK flow: write CMID with response, verify by reading
    a protected characteristic. No BLE-level pairing (the APK does not
    call createBond; Android handles link encryption transparently).
    """
    address = client.address

    if family == MachineFamily.VMINI:
        return await _authenticate_vmini(client, auth_key)

    uuids = _AUTH_UUIDS.get(family)
    if not uuids:
        _LOGGER.debug("No auth UUIDs for family %s", family)
        return False

    auth_bytes = binascii.unhexlify(auth_key)

    # Check onboard status
    try:
        onboard_data = await client.read_gatt_char(uuids["onboard"])
        is_onboarded = onboard_data != bytearray(b"\x00")
        _LOGGER.debug(
            "Onboard status for %s: %s (raw=%s)",
            address,
            is_onboarded,
            onboard_data.hex(),
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not read onboard status: %s", err)
        is_onboarded = False

    # Onboard if needed
    if not is_onboarded:
        await _onboard(client, uuids, auth_bytes, address, family)

    # Write CMID with response (matches APK and bulldog)
    try:
        await client.write_gatt_char(uuids["auth"], auth_bytes, response=True)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("CMID write failed for %s: %s", address, err)
        return False

    # Verify auth by reading a protected characteristic
    verify_uuid = uuids.get("verify") or uuids.get("onboard")
    if verify_uuid:
        try:
            await client.read_gatt_char(verify_uuid)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Auth verify read failed for %s: %s", address, err)
            if not is_onboarded:
                return False
            # Machine was already onboarded (likely by the Nespresso app).
            # Force re-onboard to register our CMID, then retry auth.
            _LOGGER.info(
                "Force re-onboard for %s (was onboarded=0x%s)",
                address,
                onboard_data.hex(),
            )
            await _onboard(client, uuids, auth_bytes, address, family)
            try:
                await client.write_gatt_char(uuids["auth"], auth_bytes, response=True)
            except Exception as err2:  # noqa: BLE001
                _LOGGER.debug(
                    "CMID write after re-onboard failed for %s: %s", address, err2
                )
                return False
            try:
                await client.read_gatt_char(verify_uuid)
            except Exception as err2:  # noqa: BLE001
                _LOGGER.debug(
                    "Auth still failed after re-onboard for %s: %s", address, err2
                )
                return False

    _LOGGER.debug("Auth succeeded for %s", address)
    return True


async def _onboard(
    client: BleakClient,
    uuids: dict[str, str],
    auth_bytes: bytes,
    address: str,
    family: MachineFamily,
) -> bool:
    """Onboard a new machine: write TX level + CMID, verify.

    Matches the APK flow: TX level, CMID, wait 2s, verify CMID_TYPE.
    """
    _LOGGER.info("Onboarding %s (%s) with new auth key", address, family.value)

    try:
        await client.write_gatt_char(uuids["pair"], bytearray([1]), response=False)
        _LOGGER.debug("TX level write sent")
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("TX level write failed (non-fatal): %s", err)

    try:
        await client.write_gatt_char(uuids["auth"], auth_bytes, response=True)
        _LOGGER.debug("Onboarding CMID write sent")
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Onboarding CMID write failed for %s: %s", address, err)
        return False

    await asyncio.sleep(2)

    # Verify onboarding succeeded
    try:
        onboard_data = await client.read_gatt_char(uuids["onboard"])
        is_final = onboard_data != bytearray(b"\x00")
        _LOGGER.debug(
            "Onboard verify for %s: %s (raw=%s)", address, is_final, onboard_data.hex()
        )
        return is_final
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Onboard verify read failed for %s: %s", address, err)
        return False


async def _authenticate_vmini(client: BleakClient, auth_key: str) -> bool:
    """Authenticate VMini using 36-byte MachineToken."""
    address = client.address
    try:
        token = auth_key.encode("utf-8").ljust(36, b"\x00")
        _LOGGER.debug(
            "Writing VMini machine token for %s: %s...",
            address,
            token[:8].hex(),
        )
        await client.write_gatt_char(VMINI_CHAR_MACHINE_TOKEN, token, response=True)
        _LOGGER.debug("VMini machine token written successfully")
        return True
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("VMini authentication failed for %s: %s", address, err)
        return False


async def _dump_all_characteristics(client: BleakClient) -> dict[str, str]:
    """Read and log every readable characteristic on the device.

    This is the primary debugging tool for reverse engineering unknown
    command IDs, WiFi byte formats, and other undocumented protocol data.
    Results are included in diagnostics downloads.
    """
    dump: dict[str, str] = {}
    for service in client.services:
        for char in service.characteristics:
            if "read" not in char.properties:
                dump[char.uuid] = f"<not readable, props={char.properties}>"
                continue
            try:
                value = await client.read_gatt_char(char.uuid)
                raw_hex = bytes(value).hex()
                try:
                    text = bytes(value).decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    text = ""
                dump[char.uuid] = raw_hex
                _LOGGER.debug(
                    "GATT %s [%s] = %s (text=%r)",
                    service.uuid,
                    char.uuid,
                    raw_hex,
                    text,
                )
            except Exception as err:  # noqa: BLE001
                dump[char.uuid] = f"<read error: {err}>"
                _LOGGER.debug(
                    "GATT %s [%s] read error: %s",
                    service.uuid,
                    char.uuid,
                    err,
                )
    return dump


async def _read_char(
    client: BleakClient,
    char_uuid: str,
    name: str,
    auth_key: str | None = None,
    family: MachineFamily = MachineFamily.VERTUO_NEXT,
) -> bytearray:
    """Read a GATT characteristic. Auth is done upfront by the coordinator."""
    try:
        value = await client.read_gatt_char(char_uuid)
        _LOGGER.debug("Read %s [%s]: %s", name, char_uuid, value.hex())
        return value
    except Exception as err:
        _LOGGER.error("Failed to read %s [%s]: %s", name, char_uuid, err)
        raise


class AbstractNespressoProtocol(ABC):
    """Base class for BLE protocol implementations."""

    @abstractmethod
    async def async_read_all(
        self, client: BleakClient, auth_key: str | None = None
    ) -> RawMachineData:
        """Read all relevant characteristics in a single session."""

    @abstractmethod
    def parse(self, raw: RawMachineData) -> NespressoMachineData:
        """Build the public dataclass from raw BLE bytes."""


class BaristaProtocol(AbstractNespressoProtocol):
    """BLE protocol for Barista (Original Line) machines."""

    async def async_read_all(
        self, client: BleakClient, auth_key: str | None = None
    ) -> RawMachineData:
        f = MachineFamily.BARISTA
        status = await _read_char(client, BARISTA_CHAR_STATUS, "status", auth_key, f)
        info = await _read_char(client, BARISTA_CHAR_INFO, "machine_info", auth_key, f)
        serial = await _read_char(client, BARISTA_CHAR_SERIAL, "serial", auth_key, f)
        profile = await _read_char(
            client, BARISTA_CHAR_PROFILE_VERSION, "profile_version", auth_key, f
        )
        params = await _read_char(
            client, BARISTA_CHAR_MACHINE_PARAMS, "machine_params", auth_key, f
        )
        # Recipe information (optional)
        recipe_info = None
        try:
            recipe_info = await client.read_gatt_char(BARISTA_CHAR_RECIPE_INFO)
            _LOGGER.debug("Recipe info raw: %s", recipe_info.hex())
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Recipe info not available")
        # GATT dump only when debug logging is active
        gatt_dump = None
        if _LOGGER.isEnabledFor(logging.DEBUG):
            gatt_dump = await _dump_all_characteristics(client)

        return RawMachineData(
            status_bytes=bytes(status),
            info_bytes=bytes(info),
            serial_bytes=bytes(serial),
            profile_version_bytes=bytes(profile),
            machine_params_bytes=bytes(params),
            recipe_info_bytes=bytes(recipe_info) if recipe_info else None,
            gatt_dump=gatt_dump,
        )

    def parse(self, raw: RawMachineData) -> NespressoMachineData:
        assert raw.status_bytes is not None
        assert raw.info_bytes is not None

        status = parse_barista_status(raw.status_bytes)
        info = parse_barista_machine_info(raw.info_bytes)
        serial = parse_serial_number(raw.serial_bytes) if raw.serial_bytes else None

        return NespressoMachineData(
            machine_state=str(status["machine_state"]),
            error_present=bool(status["error_present"]),
            firmware_version=info.get("firmware_version"),
            hardware_version=info.get("hardware_version"),
            serial_number=serial,
            profile_version=parse_profile_version(raw.profile_version_bytes)
            if raw.profile_version_bytes
            else None,
            bootloader_version=info.get("bootloader_version"),
            bluetooth_version=info.get("bluetooth_version"),
            motor_running=bool(status.get("motor_running", False)),
            induction_heating=bool(status.get("induction_heating", False)),
            setup_complete=bool(status.get("setup_complete", False)),
            recipe_count=parse_recipe_info(raw.recipe_info_bytes).max_recipes
            if raw.recipe_info_bytes and len(raw.recipe_info_bytes) >= 8
            else None,
            ble_disabled=parse_barista_machine_params(raw.machine_params_bytes).get(
                "ble_disabled", False
            )
            if raw.machine_params_bytes
            else None,
            gatt_dump=raw.gatt_dump,
        )


class VertuoNextProtocol(AbstractNespressoProtocol):
    """BLE protocol for Vertuo Next (Venus Line) machines."""

    async def async_read_all(
        self, client: BleakClient, auth_key: str | None = None
    ) -> RawMachineData:
        f = MachineFamily.VERTUO_NEXT
        status = await _read_char(client, VERTUO_CHAR_STATUS, "status", auth_key, f)
        info = await _read_char(client, VERTUO_CHAR_INFO, "machine_info", auth_key, f)
        serial = await _read_char(client, VERTUO_CHAR_SERIAL, "serial", auth_key, f)
        profile = await _read_char(
            client, VERTUO_CHAR_PROFILE_VERSION, "profile_version", auth_key, f
        )
        params = await _read_char(
            client, VERTUO_CHAR_MACHINE_PARAMS, "machine_params", auth_key, f
        )
        settings = await _read_char(
            client, VERTUO_CHAR_USER_SETTINGS, "user_settings", auth_key, f
        )
        # Select current active error (index 0) then read error info
        try:
            await client.write_gatt_char(
                VERTUO_CHAR_ERROR_SELECTION, bytes([0]), response=True
            )
            _LOGGER.debug("Error selection set to index 0 (current active)")
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error selection write not available")
        error_info = await _read_char(
            client, VERTUO_CHAR_ERROR_INFO, "error_info", auth_key, f
        )

        # Also read error at index 1 (error present in list) for diagnostics
        error_list_entry = None
        try:
            await client.write_gatt_char(
                VERTUO_CHAR_ERROR_SELECTION, bytes([1]), response=True
            )
            error_list_entry = await client.read_gatt_char(VERTUO_CHAR_ERROR_INFO)
            _LOGGER.debug("Error list entry raw: %s", error_list_entry.hex())
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error list entry not available")

        # Capsule counter (optional, may not be available on all models)
        caps_counter = None
        try:
            caps_counter = await client.read_gatt_char(VERTUO_CHAR_CAPS_COUNTER)
            _LOGGER.debug("Capsule counter raw: %s", caps_counter.hex())
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Capsule counter not available")

        # IoT market name (optional)
        iot_market = None
        try:
            iot_market = await client.read_gatt_char(VERTUO_CHAR_IOT_MARKET)
            _LOGGER.debug("IoT market name: %s", _decode_ble_string(bytes(iot_market)))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("IoT market name not available")

        # Read command response for any unsolicited data (debugging)
        try:
            cmd_rsp = await client.read_gatt_char(VERTUO_CHAR_COMMAND_RSP)
            _LOGGER.debug("VertuoNext command response: %s", cmd_rsp.hex())
        except Exception:  # noqa: BLE001
            _LOGGER.debug("VertuoNext command response not readable")
        # GATT dump only when debug logging is active
        gatt_dump = None
        if _LOGGER.isEnabledFor(logging.DEBUG):
            gatt_dump = await _dump_all_characteristics(client)

        return RawMachineData(
            status_bytes=bytes(status),
            info_bytes=bytes(info),
            serial_bytes=bytes(serial),
            profile_version_bytes=bytes(profile),
            machine_params_bytes=bytes(params),
            user_settings_bytes=bytes(settings),
            error_info_bytes=bytes(error_info),
            caps_counter_bytes=bytes(caps_counter) if caps_counter else None,
            error_list_bytes=bytes(error_list_entry) if error_list_entry else None,
            iot_market_bytes=bytes(iot_market) if iot_market else None,
            gatt_dump=gatt_dump,
        )

    def parse(self, raw: RawMachineData) -> NespressoMachineData:
        assert raw.status_bytes is not None
        assert raw.info_bytes is not None

        status = parse_vertuonext_status(raw.status_bytes)
        info = parse_vertuonext_machine_info(raw.info_bytes)
        serial = parse_serial_number(raw.serial_bytes) if raw.serial_bytes else None

        water_hardness = None
        auto_power_off = None
        if raw.user_settings_bytes:
            settings = parse_general_user_settings(raw.user_settings_bytes)
            water_hardness = settings.get("water_hardness")
            auto_power_off = settings.get("auto_power_off")

        error_code = None
        if raw.error_info_bytes and len(raw.error_info_bytes) >= 3:
            err = parse_error_information(raw.error_info_bytes)
            error_code = err.get("error_code")

        caps_counter = None
        if raw.caps_counter_bytes:
            caps_counter = parse_caps_counter(raw.caps_counter_bytes)

        return NespressoMachineData(
            machine_state=str(status["machine_state"]),
            error_present=bool(status["error_present"]),
            firmware_version=info.get("firmware_version"),
            hardware_version=info.get("hardware_version"),
            serial_number=serial,
            profile_version=parse_profile_version(raw.profile_version_bytes)
            if raw.profile_version_bytes
            else None,
            bootloader_version=info.get("bootloader_version"),
            recipe_db_version=info.get("recipe_db_version"),
            connectivity_fw_version=info.get("connectivity_fw_version"),
            water_tank_empty=bool(status.get("water_tank_empty", False)),
            descaling_needed=bool(status.get("descaling_needed", False)),
            cleaning_needed=bool(status.get("cleaning_needed", False)),
            capsule_container_full=bool(status.get("capsule_container_full", False)),
            brewing_unit_closed=bool(status.get("brewing_unit_closed", False)),
            milk_frother_running=bool(status.get("milk_frother_running", False)),
            led_signaling=bool(status.get("led_signaling", False)),
            cup_length_prog=bool(status.get("cup_length_prog", False)),
            water_hardness=water_hardness,
            auto_power_off=auto_power_off,
            error_code=error_code,
            caps_counter=caps_counter,
            error_list_code=parse_error_information(raw.error_list_bytes).get(
                "error_code"
            )
            if raw.error_list_bytes and len(raw.error_list_bytes) >= 3
            else None,
            iot_market_name=parse_serial_number(raw.iot_market_bytes)
            if raw.iot_market_bytes
            else None,
            gatt_dump=raw.gatt_dump,
        )


class VMiniProtocol(AbstractNespressoProtocol):
    """BLE protocol for VMini (Vertuo Mini) machines."""

    async def async_read_all(
        self, client: BleakClient, auth_key: str | None = None
    ) -> RawMachineData:
        f = MachineFamily.VMINI
        serial = await _read_char(client, VMINI_CHAR_SERIAL, "serial", auth_key, f)
        pairing = await _read_char(client, VMINI_CHAR_PAIRING, "pairing", auth_key, f)
        fw = await _read_char(client, VMINI_CHAR_FW_REV, "firmware_rev", auth_key, f)
        sw = await _read_char(client, VMINI_CHAR_SW_REV, "software_rev", auth_key, f)
        model = await _read_char(client, VMINI_CHAR_MODEL, "model", auth_key, f)
        manufacturer = await _read_char(
            client, VMINI_CHAR_MANUFACTURER, "manufacturer", auth_key, f
        )
        # Optional chars that may not be available before WiFi setup
        wifi_mac = None
        wifi_current = None
        shadow = None
        fota_status = None
        try:
            fota_status = await client.read_gatt_char(VMINI_CHAR_FOTA_STATUS)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("VMini FOTA status not available")
        try:
            wifi_mac = await client.read_gatt_char(VMINI_CHAR_WIFI_MAC)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("VMini WiFi MAC not available")
        try:
            wifi_current = await client.read_gatt_char(VMINI_CHAR_WIFI_CURRENT)
            _LOGGER.debug(
                "VMini WiFi current setting raw: %s (len=%d, text=%r)",
                wifi_current.hex(),
                len(wifi_current),
                bytes(wifi_current).decode("utf-8", errors="replace"),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("VMini WiFi current setting not available")
        try:
            shadow = await client.read_gatt_char(VMINI_CHAR_SHADOW_HEADER)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("VMini shadow header not available")
        _LOGGER.debug(
            "VMini raw: serial=%s pairing=%s fw=%s model=%s shadow=%s",
            serial.hex(),
            pairing.hex(),
            fw.hex(),
            model.hex(),
            shadow.hex() if shadow else "N/A",
        )
        # GATT dump only when debug logging is active
        gatt_dump_result = None
        if _LOGGER.isEnabledFor(logging.DEBUG):
            gatt_dump_result = await _dump_all_characteristics(client)

        return RawMachineData(
            serial_bytes=bytes(serial),
            pairing_byte=pairing[0] if pairing else None,
            firmware_version=_decode_ble_string(bytes(fw)),
            software_version=_decode_ble_string(bytes(sw)),
            model_number=_decode_ble_string(bytes(model)),
            manufacturer=_decode_ble_string(bytes(manufacturer)),
            wifi_mac=_decode_ble_string(bytes(wifi_mac)) if wifi_mac else None,
            shadow_header=_decode_ble_string(bytes(shadow)) if shadow else None,
            fota_status_bytes=bytes(fota_status) if fota_status else None,
            wifi_current_bytes=bytes(wifi_current) if wifi_current else None,
            gatt_dump=gatt_dump_result,
        )

    def parse(self, raw: RawMachineData) -> NespressoMachineData:
        serial = parse_serial_number(raw.serial_bytes) if raw.serial_bytes else None

        fota_status = None
        fota_progress = None
        if raw.fota_status_bytes:
            fota = parse_vmini_fota_status(raw.fota_status_bytes)
            fota_status = str(fota.get("fota_status", "unknown"))
            raw_progress = fota.get("fota_progress", 0)
            fota_progress = (
                int(raw_progress)
                if isinstance(raw_progress, (int, float, str))
                else 0
            )

        return NespressoMachineData(
            machine_state="unknown",
            error_present=False,
            firmware_version=raw.firmware_version,
            hardware_version=raw.software_version,
            serial_number=serial,
            shadow_data=raw.shadow_header,
            fota_status=fota_status,
            fota_progress=fota_progress,
            gatt_dump=raw.gatt_dump,
        )


_PROTOCOL_MAP: dict[MachineFamily, type[AbstractNespressoProtocol]] = {
    MachineFamily.BARISTA: BaristaProtocol,
    MachineFamily.VERTUO_NEXT: VertuoNextProtocol,
    MachineFamily.VMINI: VMiniProtocol,
}


def get_protocol(family: MachineFamily) -> AbstractNespressoProtocol:
    """Return the protocol instance for a given machine family."""
    return _PROTOCOL_MAP[family]()
