"""
Spotify to HomePod — Multi-account, multi-HomePod Home Assistant Integration.

Run setup once per Spotify account. All accounts share a HomePod registry
defined during the first setup and reused automatically thereafter.
"""

from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, PLATFORMS, CONF_HOMEPODS, HOMEPOD_REGISTRY_KEY

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Populate shared HomePod registry from this entry if not already set
    if HOMEPOD_REGISTRY_KEY not in hass.data:
        homepods = entry.data.get(CONF_HOMEPODS, [])
        if homepods:
            hass.data[HOMEPOD_REGISTRY_KEY] = homepods
            _LOGGER.info("HomePod registry initialised with %d device(s)", len(homepods))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
