"""Direct DM-MD8x8 matrix input selectors."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import route_update_signal
from .const import (
    DEFAULT_INPUT_NAMES,
    DM_INPUT_SLOTS,
    DM_OUTPUT_NAMES,
    DM_OUTPUT_SLOTS,
    DM_DEVICE_MODEL,
    DM_DEVICE_NAME,
    DOMAIN,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    proxy = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    entities = []

    for output_slot in DM_OUTPUT_SLOTS:
        key = f"output_{output_slot}_inputs"
        enabled_slots = settings.get(key, [str(slot) for slot in DM_INPUT_SLOTS])
        enabled = {int(value) for value in enabled_slots}
        entities.append(DMOutputInputSelect(proxy, entry, output_slot, enabled, settings))

    async_add_entities(entities)


class DMOutputInputSelect(SelectEntity):
    """Choose a configured DM input for one physical matrix output slot."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:video-input-hdmi"

    def __init__(self, proxy, entry: ConfigEntry, output_slot: int, enabled: set[int], settings: dict):
        self._proxy = proxy
        self._output_slot = output_slot
        self._input_labels = {
            (
                f"Input {slot} · "
                f"{settings.get(f'input_{slot}_name', DEFAULT_INPUT_NAMES[slot]).strip() or DEFAULT_INPUT_NAMES[slot]}"
            ): slot
            for slot in DM_INPUT_SLOTS
            if slot in enabled
        }
        self._attr_options = ["Off", *self._input_labels]
        self._attr_current_option = None
        output_name = settings.get(f"output_{output_slot}_name", DM_OUTPUT_NAMES[output_slot])
        self._output_name = output_name.strip() or DM_OUTPUT_NAMES[output_slot]
        self._attr_name = f"{self._output_name} Source"
        self._attr_unique_id = f"{entry.entry_id}_dm_output_{output_slot}_input"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dm_matrix")},
            name=DM_DEVICE_NAME,
            manufacturer="Crestron",
            model=DM_DEVICE_MODEL,
        )
        self._sync_route_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._proxy.route_update_signal,
                self._handle_route_update,
            )
        )

    @callback
    def _handle_route_update(self) -> None:
        self._sync_route_state()
        self.async_write_ha_state()

    @callback
    def _sync_route_state(self) -> None:
        input_slot = self._proxy.route_states.get(self._output_slot)
        if input_slot is None:
            self._attr_current_option = None
        elif input_slot == 0:
            self._attr_current_option = "Off"
        else:
            self._attr_current_option = next(
                (label for label, slot in self._input_labels.items() if slot == input_slot),
                None,
            )

    async def async_select_option(self, option: str) -> None:
        input_slot = 0 if option == "Off" else self._input_labels.get(option)
        if input_slot is None:
            raise HomeAssistantError(f"Input option is not enabled for output {self._output_slot}")
        try:
            await self._proxy.async_route_dm_input(input_slot, self._output_slot)
        except (OSError, ValueError) as exc:
            raise HomeAssistantError(str(exc)) from exc

        self._attr_current_option = option
        self.async_write_ha_state()
