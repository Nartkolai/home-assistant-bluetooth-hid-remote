"""Last-key sensors for Bluetooth HID Remote."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BluetoothHidRemoteConfigEntry
from .const import DOMAIN, EVENT_KEY_PRESSED
from .hid import HidUsage
from .keymap import KeyIdentity, mapped_key_attributes
from .manager import HidInputReport

_LOGGER = logging.getLogger(__name__)

LAST_KEY_DESCRIPTION = SensorEntityDescription(
    key="last_key",
    translation_key="last_key",
    icon="mdi:remote",
)
LAST_KEY_CODE_DESCRIPTION = SensorEntityDescription(
    key="last_key_code",
    translation_key="last_key_code",
    icon="mdi:numeric",
)

# MUZHTEN Douyin remote uses vendor HID page 13 (0x000D) with 4 usages per report.
# The usages are always the same — the key is identified by the first, second, and fourth bytes of data.
# Format: (first_byte, second_byte, fourth_byte)
# First byte: 0x03 = press, 0x02 = release
MUZHTEN_DATA_KEY_MAP: dict[tuple[int, int, int], tuple[str, int]] = {
    # data=02f4c112 → [0x02, 0xf4, 0x12] = DPAD_UP
    (0x02, 0xf4, 0x12): ("DPAD_UP", 19),
    # data=02f4c130 → [0x02, 0xf4, 0x30] = DPAD_DOWN
    (0x02, 0xf4, 0x30): ("DPAD_DOWN", 22),
    # data=022cc112 → [0x02, 0x2c, 0x12] = DPAD_LEFT
    (0x02, 0x2c, 0x12): ("DPAD_LEFT", 21),
    # data=02bcc212 → [0x03, 0xf4, 0x12] = DPAD_RIGHT
    (0x02, 0xbc, 0x12): ("DPAD_RIGHT", 23),
}


def _resolve_muzhten_data(data: bytes) -> tuple[str, int] | None:
    """Resolve MUZHTEN data to (key_name, key_code).
    
    Returns the (name, code) tuple if this is a MUZHTEN report, None otherwise.
    Uses first, second, and fourth bytes for unique identification.
    """
    if len(data) < 4:
        return None
    
    first_byte = data[0]
    if first_byte not in (0x02, 0x03):
        return None
    
    second_byte = data[1]
    fourth_byte = data[3]
    
    key = (first_byte, second_byte, fourth_byte)
    result = MUZHTEN_DATA_KEY_MAP.get(key)
    _LOGGER.debug(
        "MUZHTEN data lookup: data=%s → [0x%02x, 0x%02x, 0x%02x] → key=%s → result=%s",
        data.hex(), first_byte, second_byte, fourth_byte, key, result,
    )
    return result


def key_sensor_attributes(manager, report: HidInputReport, usage: HidUsage) -> dict:
    """Build diagnostic attributes shared by both last-key sensors."""
    attributes = {
        "report_id": report.report_id,
        "characteristic_handle": report.characteristic_handle,
        "data_hex": report.data.hex(),
    }
    attributes.update(mapped_key_attributes(manager.key_mapper, usage))
    return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothHidRemoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up last-key sensors for one remote."""
    manager = entry.runtime_data
    async_add_entities(
        [
            BluetoothHidRemoteLastKeySensor(manager),
            BluetoothHidRemoteLastKeyCodeSensor(manager),
        ]
    )


class BluetoothHidRemoteKeySensorBase(SensorEntity):
    """Base class for sensors retaining the most recent decoded press."""

    _attr_force_update = True
    _attr_has_entity_name = True

    def __init__(self, manager, description: SensorEntityDescription) -> None:
        self.entity_description = description
        self._manager = manager
        self._usage: HidUsage | None = None
        self._identity: KeyIdentity | None = None
        self._report: HidInputReport | None = None
        compact_address = manager.address.replace(":", "").lower()
        self._attr_unique_id = f"{compact_address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.address)},
            connections={(CONNECTION_BLUETOOTH, manager.address)},
            manufacturer="Bluetooth SIG",
            model="BLE HID Remote",
            name=manager.name,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to input reports."""
        await super().async_added_to_hass()
        self.async_on_remove(self._manager.async_add_listener(self._receive_report))

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return the raw and decoded data behind the current state."""
        if self._report is None or self._usage is None:
            return None
        return key_sensor_attributes(self._manager, self._report, self._usage)

    @callback
    def _receive_report(self, report: HidInputReport) -> None:
        """Retain the first decoded usage from each key press."""
        if report.event_type != EVENT_KEY_PRESSED or not report.usages:
            return
        self._report = report
        self._usage = report.usages[0]
        
        # Check for MUZHTEN vendor report first (by data pattern)
        muzhten = _resolve_muzhten_data(report.data)
        if muzhten:
            key_name, key_code = muzhten
            self._identity = KeyIdentity("android", key_code, key_name)
        else:
            self._identity = self._manager.key_mapper.resolve(self._usage)
        self.async_write_ha_state()


class BluetoothHidRemoteLastKeySensor(BluetoothHidRemoteKeySensorBase):
    """Expose the last decoded key as an automation-oriented symbol."""

    def __init__(self, manager) -> None:
        super().__init__(manager, LAST_KEY_DESCRIPTION)

    @property
    def native_value(self) -> str | None:
        """Return the last decoded key name in the selected namespace."""
        return self._identity.name if self._identity is not None else None


class BluetoothHidRemoteLastKeyCodeSensor(BluetoothHidRemoteKeySensorBase):
    """Expose the last decoded HID Usage ID as a number."""

    def __init__(self, manager) -> None:
        super().__init__(manager, LAST_KEY_CODE_DESCRIPTION)

    @property
    def native_value(self) -> int | None:
        """Return the last decoded key code in the selected namespace."""
        return self._identity.code if self._identity is not None else None
