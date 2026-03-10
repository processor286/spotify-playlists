"""
HomeKit bridge — multi-account, multi-HomePod edition.

Each preset switch encodes both a Spotify playlist AND a target HomePod,
so a single Siri command selects both simultaneously.

Entity naming convention:
  switch.spotify_{account}_{preset_name}_{homepod_name}

Example Siri commands:
  "Hey Siri, turn on Alice Morning Vibes Kitchen"
  "Hey Siri, turn on Bob Workout Living Room"
  "Hey Siri, turn on Charlie Chill Bedroom"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_ACCOUNT_NAME,
    CONF_HOMEPODS,
    CONF_PRESETS,
    HOMEPOD_REGISTRY_KEY,
    ATTR_ACCOUNT_NAME,
    ATTR_HOMEPOD_NAME,
    ATTR_PRESET_NAME,
    ATTR_SPOTIFY_URI,
)

_LOGGER = logging.getLogger(__name__)
HOMEKIT_PACKAGE_FILENAME = "spotify_homepod_homekit.yaml"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one switch per preset (each encodes account + playlist + HomePod)."""
    account_name = config_entry.data.get(CONF_ACCOUNT_NAME, "User")
    homepod_registry: list[dict] = config_entry.data.get(CONF_HOMEPODS, [])
    homepod_map = {h["name"]: h["host"] for h in homepod_registry}
    presets: list[dict] = config_entry.options.get(CONF_PRESETS, [])

    switches = []
    for preset in presets:
        homepod_name = preset.get("homepod_name", "")
        homepod_host = homepod_map.get(homepod_name, "")
        if not homepod_host:
            _LOGGER.warning(
                "Preset '%s' references unknown HomePod '%s' — skipping.",
                preset.get("name"), homepod_name,
            )
            continue
        switches.append(
            SpotifyPresetSwitch(
                hass=hass,
                config_entry=config_entry,
                account_name=account_name,
                preset_name=preset["name"],
                spotify_uri=preset["uri"],
                homepod_name=homepod_name,
                homepod_host=homepod_host,
            )
        )

    async_add_entities(switches, update_before_add=False)

    # Regenerate the shared HomeKit package file to include all accounts
    all_entries = hass.config_entries.async_entries(DOMAIN)
    await hass.async_add_executor_job(
        _write_homekit_package, hass, all_entries
    )


def _write_homekit_package(hass: HomeAssistant, entries) -> None:
    """
    Write packages/spotify_homepod_homekit.yaml covering all accounts.

    Includes:
      - One media_player per account
      - All preset switches across all accounts
    """
    include_entities: list[str] = []
    entity_config: dict = {}

    for entry in entries:
        account_name = entry.data.get(CONF_ACCOUNT_NAME, "user")
        safe_account = _safe(account_name)

        mp_id = f"media_player.spotify_{safe_account}"
        include_entities.append(mp_id)
        entity_config[mp_id] = {
            "name": f"Spotify {account_name}",
            "feature_list": [
                {"feature": "play_pause"},
                {"feature": "play_stop"},
                {"feature": "toggle_mute"},
            ],
        }

        for preset in entry.options.get(CONF_PRESETS, []):
            sw_id = _switch_entity_id(account_name, preset["name"], preset.get("homepod_name", ""))
            include_entities.append(sw_id)
            entity_config[sw_id] = {
                "name": f"{account_name} {preset['name']} {preset.get('homepod_name', '')}".strip()
            }

    package = {
        "homekit": {
            "filter": {"include_entities": include_entities},
            "entity_config": entity_config,
        }
    }

    packages_dir = Path(hass.config.config_dir) / "packages"
    packages_dir.mkdir(exist_ok=True)
    out = packages_dir / HOMEKIT_PACKAGE_FILENAME
    with open(out, "w") as f:
        yaml.dump(package, f, default_flow_style=False, allow_unicode=True)

    _LOGGER.info("HomeKit package updated at %s (%d entities)", out, len(include_entities))


class SpotifyPresetSwitch(SwitchEntity):
    """
    A HomeKit switch that routes a specific Spotify account's playlist
    to a specific HomePod.

    Siri sees this as a single named accessory, e.g.:
      "Alice Morning Vibes Kitchen"
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        account_name: str,
        preset_name: str,
        spotify_uri: str,
        homepod_name: str,
        homepod_host: str,
    ) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._account_name = account_name
        self._preset_name = preset_name
        self._spotify_uri = spotify_uri
        self._homepod_name = homepod_name
        self._homepod_host = homepod_host
        self._is_on = False

        self._attr_unique_id = (
            f"{config_entry.entry_id}"
            f"_{_safe(preset_name)}"
            f"_{_safe(homepod_name)}"
        )
        # Human-readable name Siri will use
        self._attr_name = f"{account_name} {preset_name} {homepod_name}"
        self.entity_id = _switch_entity_id(account_name, preset_name, homepod_name)

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def icon(self) -> str:
        return "mdi:spotify"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_ACCOUNT_NAME: self._account_name,
            ATTR_PRESET_NAME: self._preset_name,
            ATTR_SPOTIFY_URI: self._spotify_uri,
            ATTR_HOMEPOD_NAME: self._homepod_name,
            "homepod_host": self._homepod_host,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Play preset playlist on the target HomePod."""
        _LOGGER.info(
            "[%s] Siri triggered '%s' → HomePod '%s' (%s)",
            self._account_name, self._preset_name,
            self._homepod_name, self._homepod_host,
        )
        media_player_id = f"media_player.spotify_{_safe(self._account_name)}"

        # 1. Tell the media player which HomePod to target
        await self.hass.services.async_call(
            DOMAIN,
            "set_target_homepod",
            {
                "entity_id": media_player_id,
                "homepod_host": self._homepod_host,
                "homepod_name": self._homepod_name,
            },
            blocking=True,
        )

        # 2. Enable shuffle before starting playback
        await self.hass.services.async_call(
            "media_player",
            "shuffle_set",
            {
                "entity_id": media_player_id,
                "shuffle": True,
            },
            blocking=True,
        )

        # 3. Play the playlist (shuffle is now active)
        await self.hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": media_player_id,
                "media_content_id": self._spotify_uri,
                "media_content_type": "music",
            },
            blocking=False,
        )

        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause this account's Spotify playback."""
        media_player_id = f"media_player.spotify_{_safe(self._account_name)}"
        await self.hass.services.async_call(
            "media_player", "media_pause",
            {"entity_id": media_player_id},
            blocking=False,
        )
        self._is_on = False
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(s: str) -> str:
    """Convert a display name to a safe entity_id fragment."""
    return s.lower().replace(" ", "_").replace("-", "_")


def _switch_entity_id(account: str, preset: str, homepod: str) -> str:
    return f"switch.spotify_{_safe(account)}_{_safe(preset)}_{_safe(homepod)}"
