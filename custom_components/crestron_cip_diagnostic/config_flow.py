"""Configuration for Crestron DM and CIP control."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DEFAULT_BIND,
    DEFAULT_LISTEN_PORT,
    DEFAULT_MATRIX_CONSOLE_PORT,
    DEFAULT_MATRIX_HOST,
    DEFAULT_TARGET,
    DEFAULT_TARGET_PORT,
    DEFAULT_INPUT_NAMES,
    DM_INPUT_SLOTS,
    DM_OUTPUT_NAMES,
    DM_OUTPUT_SLOTS,
    DOMAIN,
)


def _routing_options_schema(settings: dict) -> vol.Schema:
    """Build input labels and per-output input availability controls."""
    schema: dict = {
        vol.Required("bind_host", default=settings.get("bind_host", DEFAULT_BIND)): str,
        vol.Required("listen_port", default=settings.get("listen_port", DEFAULT_LISTEN_PORT)):
            vol.All(vol.Coerce(int), vol.Range(min=1025, max=65535)),
        vol.Required("target_host", default=settings.get("target_host", DEFAULT_TARGET)): str,
        vol.Required("target_port", default=settings.get("target_port", DEFAULT_TARGET_PORT)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required(
            "matrix_host", default=settings.get("matrix_host", DEFAULT_MATRIX_HOST)
        ): str,
        vol.Required(
            "matrix_console_port",
            default=settings.get("matrix_console_port", DEFAULT_MATRIX_CONSOLE_PORT),
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required("standalone_mode", default=settings.get("standalone_mode", True)): bool,
    }

    for slot in DM_INPUT_SLOTS:
        key = f"input_{slot}_name"
        schema[vol.Required(key, default=settings.get(key, DEFAULT_INPUT_NAMES[slot]))] = str

    for output_slot in DM_OUTPUT_SLOTS:
        name_key = f"output_{output_slot}_name"
        schema[vol.Required(name_key, default=settings.get(name_key, DM_OUTPUT_NAMES[output_slot]))] = str
        key = f"output_{output_slot}_inputs"
        default_inputs = settings.get(key, [str(slot) for slot in DM_INPUT_SLOTS])
        labels = [
            {
                "value": str(slot),
                "label": (
                    f"Input {slot} — "
                    f"{settings.get(f'input_{slot}_name', DEFAULT_INPUT_NAMES[slot])}"
                ),
            }
            for slot in DM_INPUT_SLOTS
        ]
        schema[vol.Required(key, default=[str(value) for value in default_inputs])] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=labels,
                    multiple=True,
                )
            )
        )

    return vol.Schema(schema)


class CrestronCipDiagnosticFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up the CIP listener and DM matrix endpoint."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="Crestron DM Control",
                data=user_input,
            )
        schema = vol.Schema(
            {
                vol.Required("bind_host", default=DEFAULT_BIND): str,
                vol.Required("listen_port", default=DEFAULT_LISTEN_PORT):
                    vol.All(vol.Coerce(int), vol.Range(min=1025, max=65535)),
                vol.Required("target_host", default=DEFAULT_TARGET): str,
                vol.Required("target_port", default=DEFAULT_TARGET_PORT):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required("matrix_host", default=DEFAULT_MATRIX_HOST): str,
                vol.Required("matrix_console_port", default=DEFAULT_MATRIX_CONSOLE_PORT):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry):
        return CrestronCipDiagnosticOptionsFlow()


class CrestronCipDiagnosticOptionsFlow(config_entries.OptionsFlow):
    """Configure network endpoints, names, and per-output input choices."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Keep selected slot IDs as strings, matching the selector values.
            for output_slot in DM_OUTPUT_SLOTS:
                key = f"output_{output_slot}_inputs"
                user_input[key] = [str(value) for value in user_input.get(key, [])]
            for input_slot in DM_INPUT_SLOTS:
                key = f"input_{input_slot}_name"
                user_input[key] = user_input[key].strip() or DEFAULT_INPUT_NAMES[input_slot]
            for output_slot in DM_OUTPUT_SLOTS:
                key = f"output_{output_slot}_name"
                user_input[key] = user_input[key].strip() or DM_OUTPUT_NAMES[output_slot]
            return self.async_create_entry(title="", data=user_input)

        settings = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_routing_options_schema(settings),
        )
