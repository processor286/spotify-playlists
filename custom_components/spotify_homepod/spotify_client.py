"""
Spotify client wrapper for the Spotify to HomePod integration.

Handles token refresh, playback state polling, and control commands.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpotifyTrack:
    """Current Spotify track info."""
    title: str = ""
    artist: str = ""
    album: str = ""
    album_art_url: str = ""
    duration_ms: int = 0
    progress_ms: int = 0
    uri: str = ""
    is_playing: bool = False
    volume_pct: int = 50
    device_id: str = ""
    device_name: str = ""
    stream_url: str = ""  # Preview URL (30-sec) or full stream via ffmpeg


@dataclass
class SpotifyClientConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    refresh_token: str


class SpotifyClient:
    """Thin wrapper around the Spotipy library for HA usage."""

    def __init__(self, config: SpotifyClientConfig) -> None:
        self._config = config
        self._sp = None
        self._last_refresh = 0.0
        self._token_info: dict[str, Any] = {}

    def _ensure_client(self) -> None:
        """Lazily initialise and refresh the Spotipy client."""
        try:
            import spotipy
            import spotipy.oauth2 as oauth2

            oauth = oauth2.SpotifyOAuth(
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
                redirect_uri=self._config.redirect_uri,
                scope="user-read-playback-state user-modify-playback-state streaming",
            )

            # Refresh if token is stale (> 50 min)
            if time.time() - self._last_refresh > 3000 or self._sp is None:
                token_info = oauth.refresh_access_token(self._config.refresh_token)
                self._token_info = token_info
                self._last_refresh = time.time()
                self._sp = spotipy.Spotify(auth=token_info["access_token"])

        except ImportError:
            _LOGGER.error(
                "spotipy is not installed. Run: pip install spotipy"
            )
            raise
        except Exception as err:
            _LOGGER.error("Failed to initialise Spotify client: %s", err)
            raise

    def get_current_playback(self) -> Optional[SpotifyTrack]:
        """Fetch current playback state from Spotify API."""
        try:
            self._ensure_client()
            playback = self._sp.current_playback()
            if not playback:
                return None

            item = playback.get("item") or {}
            artists = item.get("artists", [])
            artist_name = ", ".join(a["name"] for a in artists)
            images = item.get("album", {}).get("images", [])
            art_url = images[0]["url"] if images else ""

            device = playback.get("device") or {}
            return SpotifyTrack(
                title=item.get("name", ""),
                artist=artist_name,
                album=item.get("album", {}).get("name", ""),
                album_art_url=art_url,
                duration_ms=item.get("duration_ms", 0),
                progress_ms=playback.get("progress_ms", 0),
                uri=item.get("uri", ""),
                is_playing=playback.get("is_playing", False),
                volume_pct=device.get("volume_percent", 50),
                device_id=device.get("id", ""),
                device_name=device.get("name", ""),
                stream_url=item.get("preview_url") or "",
            )
        except Exception as err:
            _LOGGER.warning("get_current_playback error: %s", err)
            return None

    def play(self, device_id: str | None = None) -> bool:
        """Resume playback."""
        try:
            self._ensure_client()
            self._sp.start_playback(device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("play() error: %s", err)
            return False

    def pause(self, device_id: str | None = None) -> bool:
        """Pause playback."""
        try:
            self._ensure_client()
            self._sp.pause_playback(device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("pause() error: %s", err)
            return False

    def next_track(self, device_id: str | None = None) -> bool:
        try:
            self._ensure_client()
            self._sp.next_track(device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("next_track() error: %s", err)
            return False

    def previous_track(self, device_id: str | None = None) -> bool:
        try:
            self._ensure_client()
            self._sp.previous_track(device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("previous_track() error: %s", err)
            return False

    def set_volume(self, volume_pct: int, device_id: str | None = None) -> bool:
        try:
            self._ensure_client()
            self._sp.volume(max(0, min(100, volume_pct)), device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("set_volume() error: %s", err)
            return False

    def seek(self, position_ms: int, device_id: str | None = None) -> bool:
        try:
            self._ensure_client()
            self._sp.seek_track(position_ms, device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("seek() error: %s", err)
            return False

    def play_uri(self, uri: str, device_id: str | None = None) -> bool:
        """Play a specific Spotify URI (track/album/playlist)."""
        try:
            self._ensure_client()
            if uri.startswith("spotify:track:"):
                self._sp.start_playback(device_id=device_id, uris=[uri])
            else:
                self._sp.start_playback(device_id=device_id, context_uri=uri)
            return True
        except Exception as err:
            _LOGGER.error("play_uri() error: %s", err)
            return False

    def get_devices(self) -> list[dict]:
        """Return list of available Spotify Connect devices."""
        try:
            self._ensure_client()
            result = self._sp.devices()
            return result.get("devices", [])
        except Exception as err:
            _LOGGER.error("get_devices() error: %s", err)
            return []

    def shuffle(self, state: bool, device_id: str | None = None) -> bool:
        try:
            self._ensure_client()
            self._sp.shuffle(state, device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("shuffle() error: %s", err)
            return False

    def repeat(self, mode: str, device_id: str | None = None) -> bool:
        """mode: 'off', 'context', 'track'"""
        try:
            self._ensure_client()
            self._sp.repeat(mode, device_id=device_id)
            return True
        except Exception as err:
            _LOGGER.error("repeat() error: %s", err)
            return False
