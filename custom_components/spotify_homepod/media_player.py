"""
Media Player entity — multi-account, multi-HomePod edition.

Each Spotify account gets its own media_player entity.
The target HomePod can be switched at runtime via the
`spotify_homepod.set_target_homepod` service (called by preset switches).

Entity ID: media_player.spotify_{account_name}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .airplay_client import RaopSession
from .const import (
    DOMAIN,
    CONF_ACCOUNT_NAME,
    CONF_HOMEPODS,
    CONF_POLL_INTERVAL,
    CONF_AUTO_PLAY,
    DEFAULT_POLL_INTERVAL,
    ATTR_ACCOUNT_NAME,
    ATTR_HOMEPOD_NAME,
    ATTR_HOMEPOD_HOST,
)
from .spotify_client import SpotifyClient, SpotifyTrack, SpotifyClientConfig

_LOGGER = logging.getLogger(__name__)

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.PLAY_MEDIA
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = config_entry.data
    options = config_entry.options

    spotify_cfg = SpotifyClientConfig(
        client_id=data["spotify_client_id"],
        client_secret=data["spotify_client_secret"],
        redirect_uri=data["spotify_redirect_uri"],
        refresh_token=data["spotify_refresh_token"],
    )

    account_name = data.get(CONF_ACCOUNT_NAME, "User")
    homepod_registry: list[dict] = data.get(CONF_HOMEPODS, [])
    poll_interval = options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    auto_play = options.get(CONF_AUTO_PLAY, True)

    # Default to first HomePod in registry
    default_homepod = homepod_registry[0] if homepod_registry else {"name": "HomePod", "host": ""}

    entity = SpotifyHomePodPlayer(
        hass=hass,
        spotify=SpotifyClient(spotify_cfg),
        account_name=account_name,
        homepod_registry=homepod_registry,
        current_homepod_name=default_homepod["name"],
        current_homepod_host=default_homepod["host"],
        poll_interval=poll_interval,
        auto_play=auto_play,
        unique_id=config_entry.entry_id,
    )

    async_add_entities([entity], update_before_add=True)

    # Register the set_target_homepod service (idempotent — safe to register multiple times)
    async def handle_set_target_homepod(call):
        """Service: switch which HomePod this player streams to."""
        entity_id = call.data.get("entity_id")
        if entity_id != entity.entity_id:
            return
        host = call.data.get("homepod_host", "")
        name = call.data.get("homepod_name", "")
        await entity.async_set_target_homepod(host, name)

    hass.services.async_register(
        DOMAIN,
        "set_target_homepod",
        handle_set_target_homepod,
    )


def _safe(s: str) -> str:
    return s.lower().replace(" ", "_").replace("-", "_")


class SpotifyHomePodPlayer(MediaPlayerEntity):
    """One media player per Spotify account. Target HomePod is switchable at runtime."""

    _attr_has_entity_name = False
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(
        self,
        hass: HomeAssistant,
        spotify: SpotifyClient,
        account_name: str,
        homepod_registry: list[dict],
        current_homepod_name: str,
        current_homepod_host: str,
        poll_interval: int,
        auto_play: bool,
        unique_id: str,
    ) -> None:
        self.hass = hass
        self._spotify = spotify
        self._account_name = account_name
        self._homepod_registry = homepod_registry     # all available HomePods
        self._current_homepod_name = current_homepod_name
        self._current_homepod_host = current_homepod_host
        self._poll_interval = poll_interval
        self._auto_play = auto_play
        self._attr_unique_id = unique_id
        self._attr_name = f"Spotify {account_name}"
        self.entity_id = f"media_player.spotify_{_safe(account_name)}"

        self._track: Optional[SpotifyTrack] = None
        self._raop: Optional[RaopSession] = None
        self._is_streaming = False
        self._cancel_poll = None

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        self._cancel_poll = async_track_time_interval(
            self.hass, self._async_poll,
            timedelta(seconds=self._poll_interval),
        )
        _LOGGER.info("[%s] Player started, polling every %ds", self._account_name, self._poll_interval)

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_poll:
            self._cancel_poll()
        await self._stop_airplay()

    # ------------------------------------------------------------------
    # Runtime HomePod switching (called by preset switches)
    # ------------------------------------------------------------------

    async def async_set_target_homepod(self, host: str, name: str) -> None:
        """Switch which HomePod this account streams to."""
        if host == self._current_homepod_host:
            return  # already targeting this one
        _LOGGER.info(
            "[%s] Switching target HomePod: %s → %s (%s)",
            self._account_name, self._current_homepod_name, name, host,
        )
        await self._stop_airplay()
        self._current_homepod_host = host
        self._current_homepod_name = name
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        if not self._track:
            return MediaPlayerState.IDLE
        return MediaPlayerState.PLAYING if self._track.is_playing else MediaPlayerState.PAUSED

    @property
    def media_title(self): return self._track.title if self._track else None
    @property
    def media_artist(self): return self._track.artist if self._track else None
    @property
    def media_album_name(self): return self._track.album if self._track else None
    @property
    def media_image_url(self): return self._track.album_art_url if self._track else None
    @property
    def media_duration(self): return self._track.duration_ms / 1000 if self._track and self._track.duration_ms else None
    @property
    def media_position(self): return self._track.progress_ms / 1000 if self._track else None
    @property
    def volume_level(self): return self._track.volume_pct / 100 if self._track else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_ACCOUNT_NAME: self._account_name,
            ATTR_HOMEPOD_NAME: self._current_homepod_name,
            ATTR_HOMEPOD_HOST: self._current_homepod_host,
            "streaming_active": self._is_streaming,
            "available_homepods": [h["name"] for h in self._homepod_registry],
        }

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    async def async_media_play(self) -> None:
        await self.hass.async_add_executor_job(self._spotify.play)
        await self._maybe_start_airplay()
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        await self.hass.async_add_executor_job(self._spotify.pause)
        await self._stop_airplay()
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        await self.hass.async_add_executor_job(self._spotify.pause)
        await self._stop_airplay()
        self._track = None
        self.async_write_ha_state()

    async def async_media_next_track(self) -> None:
        await self.hass.async_add_executor_job(self._spotify.next_track)

    async def async_media_previous_track(self) -> None:
        await self.hass.async_add_executor_job(self._spotify.previous_track)

    async def async_set_volume_level(self, volume: float) -> None:
        pct = int(volume * 100)
        await self.hass.async_add_executor_job(self._spotify.set_volume, pct)
        if self._raop:
            await self._raop.set_volume(pct)

    async def async_media_seek(self, position: float) -> None:
        await self.hass.async_add_executor_job(self._spotify.seek, int(position * 1000))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self.hass.async_add_executor_job(self._spotify.shuffle, shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        mode_map = {RepeatMode.OFF: "off", RepeatMode.ONE: "track", RepeatMode.ALL: "context"}
        await self.hass.async_add_executor_job(self._spotify.repeat, mode_map.get(repeat, "off"))

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self._spotify.play_uri, media_id)
        await self._maybe_start_airplay()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _async_poll(self, _now=None) -> None:
        track = await self.hass.async_add_executor_job(self._spotify.get_current_playback)
        self._track = track
        if track and track.is_playing:
            if self._auto_play and not self._is_streaming:
                await self._maybe_start_airplay()
        elif self._is_streaming:
            await self._stop_airplay()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        self._track = await self.hass.async_add_executor_job(self._spotify.get_current_playback)

    # ------------------------------------------------------------------
    # AirPlay
    # ------------------------------------------------------------------

    async def _maybe_start_airplay(self) -> None:
        if self._is_streaming or not self._current_homepod_host:
            return
        if not self._track or not self._track.stream_url:
            _LOGGER.debug("[%s] No stream URL — skipping AirPlay", self._account_name)
            return

        _LOGGER.info("[%s] Starting AirPlay → %s (%s)", self._account_name, self._current_homepod_name, self._current_homepod_host)
        self._raop = RaopSession(host=self._current_homepod_host, volume=self._track.volume_pct)
        connected = await self._raop.connect()
        if not connected:
            _LOGGER.error("[%s] Failed to connect to HomePod %s", self._account_name, self._current_homepod_host)
            self._raop = None
            return
        self._is_streaming = True
        self.hass.async_create_task(self._raop.start_stream(self._track.stream_url))

    async def _stop_airplay(self) -> None:
        if self._raop and self._is_streaming:
            _LOGGER.info("[%s] Stopping AirPlay", self._account_name)
            await self._raop.stop_stream()
            self._raop = None
        self._is_streaming = False
