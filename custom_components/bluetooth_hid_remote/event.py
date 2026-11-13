"""Event entity for Bluetooth HID Remote."""

from __future__ import annotations

from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
    EventEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BluetoothHidRemoteConfigEntry
from .const import DOMAIN, EVENT_TYPES
from .keymap import KeyMapper, mapped_key_attributes
from .manager import HidInputReport

DESCRIPTION = EventEntityDescription(
    key="remote_button",
    translation_key="remote_button",
    device_class=EventDeviceClass.BUTTON,
    event_types=EVENT_TYPES,
)

# MUZHTEN Douyin remote uses vendor HID page 13 (0x000D) with 4 usages per report.
# The usages are always the same — the key is identified by the first, second, and fourth bytes of data.
# Format: (first_byte, second_byte, fourth_byte)
# First byte: 0x03 = press, 0x02 = release
MUZHTEN_DATA_KEY_MAP: dict[tuple[int, int, int], str] = {
    # data=02f4c112 → [0x02, 0xf4, 0x12] = DPAD_UP
    (0x02, 0xf4, 0x12): "DPAD_UP",
    # data=02f4c130 → [0x02, 0xf4, 0x30] = DPAD_DOWN
    (0x02, 0xf4, 0x30): "DPAD_DOWN",
    # data=022cc112 → [0x02, 0x2c, 0x12] = DPAD_LEFT
    (0x02, 0x2c, 0x12): "DPAD_LEFT",
    # data=02bcc212 → [0x03, 0xf4, 0x12] = DPAD_RIGHT
    (0x02, 0xbc, 0x12): "DPAD_RIGHT",
    # data=03f5c112 → [0x03, 0xf5, 0x12] = DPAD_CENTER
    (0x03, 0xf5, 0x12): "DPAD_CENTER",
}


def _resolve_muzhten_data(data: bytes) -> str | None:
    """Resolve MUZHTEN data to a key name.
    
    Returns the key name if this is a MUZHTEN report, None otherwise.
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
    return MUZHTEN_DATA_KEY_MAP.get(key)


def event_attributes(report: HidInputReport, mapper: KeyMapper) -> dict:
    """Build backward-compatible raw and decoded event attributes."""
    attributes = {
        "report_id": report.report_id,
        "characteristic_handle": report.characteristic_handle,
        "data_hex": report.data.hex(),
    }
    
    # Check for MUZHTEN vendor report (by data pattern)
    muzhten_key = _resolve_muzhten_data(report.data)
    if muzhten_key:
        # Find the Android key code for this key name
        from .android_keycodes import ANDROID_KEY_CODES
        android_name = muzhten_key.removeprefix("KEYCODE_")
        key_code = ANDROID_KEY_CODES.get(android_name, report.usages[-1].usage_id)
        attributes["key_profile"] = "muzhten_douyin"
        attributes["key_namespace"] = "android"
        attributes["key_code"] = key_code
        attributes["key_name"] = android_name
        attributes["keys"] = [{
            "key_profile": "muzhten_douyin",
            "key_namespace": "android",
            "key_code": key_code,
            "key_name": android_name,
            "hid_usage_page": report.usages[-1].usage_page if report.usages else 0,
            "hid_usage_page_hex": f"0x{report.usages[-1].usage_page:04X}" if report.usages else "0x0000",
            "hid_usage_page_name": "Vendor (page 0x000D)",
            "hid_usage_id": report.usages[-1].usage_id if report.usages else 0,
            "hid_usage_id_hex": f"0x{report.usages[-1].usage_id:04X}" if report.usages else "0x0000",
            "hid_usage_name": f"MUZHTEN button ({muzhten_key})",
        }]
        return attributes
    
    # Standard keymap resolution for non-MUZHTEN reports
    keys = [mapped_key_attributes(mapper, usage) for usage in report.usages]
    if keys:
        attributes["keys"] = keys
    if len(keys) == 1:
        attributes.update(keys[0])
    return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothHidRemoteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the remote's event entity."""
    async_add_entities([BluetoothHidRemoteEvent(entry.runtime_data)])


class BluetoothHidRemoteEvent(EventEntity):
    """Expose raw HOGP input reports through a Home Assistant event entity."""

    _attr_has_entity_name = True
    entity_description = DESCRIPTION

    def __init__(self, manager) -> None:
        self._manager = manager
        compact_address = manager.address.replace(":", "").lower()
        self._attr_unique_id = f"{compact_address}_remote_button"
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

    @callback
    def _receive_report(self, report: HidInputReport) -> None:
        """Publish one raw report as a press or release event."""
        self._trigger_event(
            report.event_type,
            event_attributes(report, self._manager.key_mapper),
        )
        self.async_write_ha_state()
