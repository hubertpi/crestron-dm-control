"""Power switches whose state comes from automatic display queries."""
from __future__ import annotations

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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


DISPLAY_SWITCHES = (
    ("169.254.0.35", 17, "projector_17_1", "mdi:projector-screen"),
    ("169.254.0.37", 18, "sharp_18_1", "mdi:monitor"),
    ("169.254.0.39", 19, "sony_19_1", "mdi:television"),
    ("169.254.0.41", 20, "sony_20_1", "mdi:television"),
)

POWERED_ON_STATES = {
    "169.254.0.35": {"On", "Warming up"},
    "169.254.0.37": {"On", "Input signal waiting"},
    "169.254.0.39": {"On"},
    "169.254.0.41": {"On"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    proxy = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    entities = []
    for receiver_ip, output_slot, device_key, icon in DISPLAY_SWITCHES:
        output_name = (
            settings.get(f"output_{output_slot}_name", DM_OUTPUT_NAMES[output_slot]).strip()
            or DM_OUTPUT_NAMES[output_slot]
        )
        entities.append(
            DisplayPowerSwitch(
                proxy,
                entry,
                receiver_ip,
                device_key,
                output_name,
                icon,
            )
        )
    async_add_entities(entities)


class DisplayPowerSwitch(SwitchEntity):
    """Display switch using feedback with a short post-command grace period."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        proxy,
        entry: ConfigEntry,
        receiver_ip: str,
        device_key: str,
        output_name: str,
        icon: str,
    ) -> None:
        self._proxy = proxy
        self._entry = entry
        self._receiver_ip = receiver_ip
        self._device_key = device_key
        self._attr_name = f"{output_name} Power"
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{device_key}_power_switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dm_matrix")},
            name=DM_DEVICE_NAME,
            manufacturer="Crestron",
            model=DM_DEVICE_MODEL,
        )

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

    @property
    def _power_state(self) -> str | None:
        if self._receiver_ip == "169.254.0.35":
            code = self._proxy.last_power_code
            return POWER_STATUS_LABELS.get(code, f"PWR={code}") if code is not None else None
        if self._receiver_ip == "169.254.0.37":
            return self._proxy.sharp_power_states.get(self._receiver_ip)
        return self._proxy.sony_power_states.get(self._receiver_ip)

    @property
    def available(self) -> bool:
        last_update = self._proxy.display_status_timestamps.get(self._receiver_ip)
        return (
            self._power_state is not None
            and last_update is not None
            and time.monotonic() - last_update <= DISPLAY_STATUS_TIMEOUT
        )

    @property
    def is_on(self) -> bool:
        reported_on = self._power_state in POWERED_ON_STATES[self._receiver_ip]
        command_state = self._proxy.commanded_power_states.get(self._receiver_ip)
        if command_state is None:
            return reported_on

        requested_on, expires_at = command_state
        if reported_on == requested_on:
            self._proxy.commanded_power_states.pop(self._receiver_ip, None)
            return reported_on
        if time.monotonic() < expires_at:
            return requested_on

        self._proxy.commanded_power_states.pop(self._receiver_ip, None)
        return reported_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_power(False)

    async def _set_power(self, is_on: bool) -> None:
        try:
            await self._proxy.async_set_display_power(self._receiver_ip, is_on)
        except (ValueError, OSError) as exc:
            raise HomeAssistantError(str(exc)) from exc
