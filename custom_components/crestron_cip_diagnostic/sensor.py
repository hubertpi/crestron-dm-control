"""Power status sensors for displays on the Crestron DM matrix."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import update_signal
from .const import (
    DISPLAY_STATUS_TIMEOUT,
    DM_DEVICE_MODEL,
    DM_DEVICE_NAME,
    DM_OUTPUT_NAMES,
    DOMAIN,
)
from .probe import POWER_STATUS_LABELS


DISPLAY_ON_STATES = {
    "169.254.0.35": {"On", "Warming up"},
    "169.254.0.37": {"On", "Input signal waiting"},
    "169.254.0.39": {"On"},
    "169.254.0.41": {"On"},
}


def _configured_output_name(settings: dict, slot: int) -> str:
    return (
        settings.get(f"output_{slot}_name", DM_OUTPUT_NAMES[slot]).strip()
        or DM_OUTPUT_NAMES[slot]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    proxy = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    async_add_entities(
        [
            ProjectorPowerStatusSensor(proxy, entry, _configured_output_name(settings, 17)),
            SonyPowerStatusSensor(
                proxy, entry, "169.254.0.39", _configured_output_name(settings, 19), "sony_19_1"
            ),
            SonyPowerStatusSensor(
                proxy, entry, "169.254.0.41", _configured_output_name(settings, 20), "sony_20_1"
            ),
            SharpPowerStatusSensor(proxy, entry, _configured_output_name(settings, 18)),
        ]
    )


class DisplayPowerStatusSensor(SensorEntity):
    """Base class for a display's decoded power status."""

    _attr_has_entity_name = True
    _attr_name = "Power status"

    def __init__(self, proxy, entry: ConfigEntry) -> None:
        self._proxy = proxy
        self._entry = entry

    @property
    def available(self) -> bool:
        now = time.monotonic()
        last_update = self._proxy.display_status_timestamps.get(self._receiver_ip)
        has_recent_reply = (
            last_update is not None
            and now - last_update <= DISPLAY_STATUS_TIMEOUT
        )
        command_state = self._proxy.commanded_power_states.get(self._receiver_ip)
        command_pending = command_state is not None and now < command_state[1]
        return has_recent_reply or command_pending

    def _with_command_transition(self, reported_status: str | None) -> str | None:
        """Show a transition while the device catches up to a power command."""
        command_state = self._proxy.commanded_power_states.get(self._receiver_ip)
        if command_state is None:
            return reported_status

        requested_on, expires_at = command_state
        if reported_status is not None:
            reported_on = reported_status in DISPLAY_ON_STATES[self._receiver_ip]
            if reported_on == requested_on:
                self._proxy.commanded_power_states.pop(self._receiver_ip, None)
                return reported_status
        if time.monotonic() < expires_at:
            return "Turning on" if requested_on else "Turning off"

        self._proxy.commanded_power_states.pop(self._receiver_ip, None)
        return reported_status

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Keep the latest device reply available without a separate sensor."""
        reply = self._proxy.last_display_replies.get(self._receiver_ip)
        if reply is None:
            return None
        return {"last_serial_reply": reply}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                update_signal(self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class ProjectorPowerStatusSensor(DisplayPowerStatusSensor):
    """Epson projector power status from its PWR? serial response."""

    _attr_icon = "mdi:projector-screen"

    def __init__(self, proxy, entry: ConfigEntry, output_name: str) -> None:
        super().__init__(proxy, entry)
        self._receiver_ip = "169.254.0.35"
        self._attr_name = f"{output_name} Power Status"
        self._attr_unique_id = f"{entry.entry_id}_projector_17_1_power_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dm_matrix")},
            name=DM_DEVICE_NAME,
            manufacturer="Crestron",
            model=DM_DEVICE_MODEL,
        )

    @property
    def native_value(self) -> str | None:
        code = self._proxy.last_power_code
        reported = POWER_STATUS_LABELS.get(code, f"PWR={code}") if code is not None else None
        return self._with_command_transition(reported)


class SonyPowerStatusSensor(DisplayPowerStatusSensor):
    """Sony BRAVIA power state for one matrix output."""

    _attr_icon = "mdi:television"

    def __init__(self, proxy, entry: ConfigEntry, receiver_ip: str, output_name: str, device_key: str) -> None:
        super().__init__(proxy, entry)
        self._receiver_ip = receiver_ip
        self._attr_name = f"{output_name} Power Status"
        self._attr_unique_id = f"{entry.entry_id}_{device_key}_power_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dm_matrix")},
            name=DM_DEVICE_NAME,
            manufacturer="Crestron",
            model=DM_DEVICE_MODEL,
        )

    @property
    def native_value(self) -> str | None:
        return self._with_command_transition(
            self._proxy.sony_power_states.get(self._receiver_ip)
        )


class SharpPowerStatusSensor(DisplayPowerStatusSensor):
    """Sharp touch display power state for matrix output 18.1."""

    # Use a standard MDI glyph that is present in older Home Assistant builds.
    _attr_icon = "mdi:monitor"

    def __init__(self, proxy, entry: ConfigEntry, output_name: str) -> None:
        super().__init__(proxy, entry)
        self._receiver_ip = "169.254.0.37"
        self._attr_name = f"{output_name} Power Status"
        self._attr_unique_id = f"{entry.entry_id}_sharp_18_1_power_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dm_matrix")},
            name=DM_DEVICE_NAME,
            manufacturer="Crestron",
            model=DM_DEVICE_MODEL,
        )

    @property
    def native_value(self) -> str | None:
        return self._with_command_transition(
            self._proxy.sharp_power_states.get(self._receiver_ip)
        )


