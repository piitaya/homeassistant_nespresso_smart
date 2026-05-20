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

"""Diagnostics support for Nespresso Smart integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import NespressoCoordinator

REDACT_KEYS = {"address", "serial_number", "auth_key"}


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from a dictionary."""
    return {k: "**REDACTED**" if k in REDACT_KEYS else v for k, v in data.items()}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: NespressoCoordinator = data["coordinator"]

    diag: dict[str, Any] = {
        "config_entry": _redact(
            {
                "address": entry.data.get("address"),
                "family": entry.data.get("family"),
                "name": entry.data.get("name"),
                "options": dict(entry.options),
            }
        ),
        "coordinator": {
            "available": coordinator.available,
            "last_poll_successful": coordinator.last_poll_successful,
            "scan_interval_seconds": DEFAULT_SCAN_INTERVAL,
            "last_seen": coordinator.last_seen,
        },
    }

    if coordinator.data is not None:
        data_dict = asdict(coordinator.data)
        gatt_dump = data_dict.pop("gatt_dump", None)
        diag["machine_data"] = _redact(data_dict)
        if gatt_dump:
            diag["gatt_characteristic_dump"] = gatt_dump

    diag["debug_info"] = {
        "family": coordinator.family.value,
    }

    return diag
