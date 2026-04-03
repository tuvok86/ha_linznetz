"""
Custom integration to integrate linznetz with Home Assistant.
"""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .api import LinzNetzApiClient
from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, _config: ConfigType):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up linznetz from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    # Create API client if credentials are provided
    client = None
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    if username and password:
        client = LinzNetzApiClient(username, password)
        _LOGGER.debug("LinzNetz API client created for automatic data fetching")
    else:
        _LOGGER.debug("No credentials configured, only manual CSV import available")

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})
        client = data.get("client")
        if client:
            await client.close()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
