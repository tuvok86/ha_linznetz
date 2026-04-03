"""Adds config flow for linznetz."""
import logging

from homeassistant import config_entries

import voluptuous as vol

from .api import LinzNetzApiClient, LinzNetzAuthError, LinzNetzConnectionError
from .const import (
    CONF_METER_POINT_NUMBER,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_METER_POINT_NUMBER): str,
        vol.Optional(CONF_NAME): str,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)


class LinzNetzFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for linznetz."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        valid = len(user_input[CONF_METER_POINT_NUMBER]) == 33
        if not valid:
            errors["base"] = "invalid_length"
        elif user_input.get(CONF_USERNAME) and user_input.get(CONF_PASSWORD):
            # Validate credentials if provided
            client = LinzNetzApiClient(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            try:
                await client.validate_credentials()
            except LinzNetzAuthError:
                errors["base"] = "invalid_auth"
            except LinzNetzConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during login validation")
                errors["base"] = "unknown"
            finally:
                await client.close()

        if not errors and valid:
            await self.async_set_unique_id(user_input[CONF_METER_POINT_NUMBER])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_METER_POINT_NUMBER], data=user_input
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
