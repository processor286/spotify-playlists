"""
Config flow for Spotify to HomePod — multi-account, multi-HomePod edition.

Setup flow:
  Step 1 (homepods)  — Register all HomePods on the network (name + IP).
                       Skipped on 2nd/3rd account setup (reuses existing registry).
  Step 2 (user)      — Spotify credentials + account display name.
  Step 3 (auth)      — Spotify OAuth.
  Step 4 (presets)   — Up to 3 presets per account, each with a target HomePod.

Options flow lets users update presets and HomePod list at any time.
"""

from __future__ import annotations
import logging
import socket
from typing import Any
from urllib.parse import urlparse, parse_qs

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_SPOTIFY_CLIENT_ID,
    CONF_SPOTIFY_CLIENT_SECRET,
    CONF_SPOTIFY_REDIRECT_URI,
    CONF_ACCOUNT_NAME,
    CONF_HOMEPODS,
    CONF_POLL_INTERVAL,
    CONF_STREAM_QUALITY,
    CONF_AUTO_PLAY,
    CONF_PRESETS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_STREAM_QUALITY,
    SPOTIFY_SCOPES,
    HOMEPOD_REGISTRY_KEY,
)

_LOGGER = logging.getLogger(__name__)

MAX_HOMEPODS = 6
MAX_PRESETS = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _test_host(host: str) -> bool:
    try:
        sock = socket.create_connection((host, 7000), timeout=5)
        sock.close()
        return True
    except (OSError, socket.timeout):
        try:
            socket.getaddrinfo(host, None)
            return True
        except socket.gaierror:
            return False


def _build_auth_url(client_id: str, redirect_uri: str) -> str:
    try:
        import spotipy.oauth2 as oauth2
        sp = oauth2.SpotifyOAuth(
            client_id=client_id, client_secret="x",
            redirect_uri=redirect_uri, scope=" ".join(SPOTIFY_SCOPES),
        )
        return sp.get_authorize_url()
    except Exception:
        scope_str = "%20".join(SPOTIFY_SCOPES)
        return (
            f"https://accounts.spotify.com/authorize"
            f"?client_id={client_id}&response_type=code"
            f"&redirect_uri={redirect_uri}&scope={scope_str}"
        )


def _exchange_code(client_id, client_secret, redirect_uri, auth_code_url) -> str | None:
    try:
        import spotipy.oauth2 as oauth2
        parsed = urlparse(auth_code_url)
        params = parse_qs(parsed.query)
        code = params.get("code", [auth_code_url.strip()])[0]
        sp = oauth2.SpotifyOAuth(
            client_id=client_id, client_secret=client_secret,
            redirect_uri=redirect_uri, scope=" ".join(SPOTIFY_SCOPES),
        )
        token = sp.get_access_token(code, as_dict=True)
        return token.get("refresh_token")
    except Exception as err:
        _LOGGER.error("Token exchange failed: %s", err)
        return None


def _get_homepod_registry(hass: HomeAssistant) -> list[dict]:
    """Return the shared HomePod registry (list of {name, host})."""
    return hass.data.get(HOMEPOD_REGISTRY_KEY, [])


def _homepod_names(hass: HomeAssistant) -> list[str]:
    return [h["name"] for h in _get_homepod_registry(hass)]


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class SpotifyHomePodConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow supporting multiple accounts."""

    VERSION = 2

    def __init__(self) -> None:
        self._homepods: list[dict] = []        # [{name, host}, ...]
        self._user_input: dict[str, Any] = {}
        self._auth_url: str = ""
        self._adding_homepod_index: int = 0    # which HomePod slot we're on

    # ------------------------------------------------------------------
    # Step 1a: How many HomePods? (only on first account setup)
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point — go to HomePod setup or skip if registry exists."""
        existing_homepods = _get_homepod_registry(self.hass)
        if existing_homepods:
            # Registry already populated by a previous account setup
            self._homepods = existing_homepods
            return await self.async_step_account()
        return await self.async_step_homepods()

    async def async_step_homepods(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect HomePod names and IPs."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Parse submitted HomePod slots
            homepods = []
            for i in range(1, MAX_HOMEPODS + 1):
                name = user_input.get(f"homepod_{i}_name", "").strip()
                host = user_input.get(f"homepod_{i}_host", "").strip()
                if name and host:
                    # Validate reachability for non-empty entries
                    reachable = await self.hass.async_add_executor_job(_test_host, host)
                    if not reachable:
                        errors[f"homepod_{i}_host"] = "cannot_connect_homepod"
                    else:
                        homepods.append({"name": name, "host": host})

            if not homepods:
                errors["base"] = "no_homepods"
            elif not errors:
                self._homepods = homepods
                # Persist to shared registry
                self.hass.data[HOMEPOD_REGISTRY_KEY] = homepods
                return await self.async_step_account()

        # Build dynamic schema for up to MAX_HOMEPODS slots
        fields: dict = {}
        for i in range(1, MAX_HOMEPODS + 1):
            fields[vol.Optional(f"homepod_{i}_name", default="")] = str
            fields[vol.Optional(f"homepod_{i}_host", default="")] = str

        return self.async_show_form(
            step_id="homepods",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "hint": "Fill in as many HomePods as you have. Leave unused slots blank."
            },
        )

    # ------------------------------------------------------------------
    # Step 2: Spotify account credentials + display name
    # ------------------------------------------------------------------

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_input = user_input
            self._auth_url = await self.hass.async_add_executor_job(
                _build_auth_url,
                user_input[CONF_SPOTIFY_CLIENT_ID],
                user_input[CONF_SPOTIFY_REDIRECT_URI],
            )
            return await self.async_step_auth()

        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema({
                vol.Required(CONF_ACCOUNT_NAME): str,
                vol.Required(CONF_SPOTIFY_CLIENT_ID): str,
                vol.Required(CONF_SPOTIFY_CLIENT_SECRET): str,
                vol.Required(
                    CONF_SPOTIFY_REDIRECT_URI,
                    default="http://localhost:8888/callback",
                ): str,
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3: Spotify OAuth
    # ------------------------------------------------------------------

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = await self.hass.async_add_executor_job(
                _exchange_code,
                self._user_input[CONF_SPOTIFY_CLIENT_ID],
                self._user_input[CONF_SPOTIFY_CLIENT_SECRET],
                self._user_input[CONF_SPOTIFY_REDIRECT_URI],
                user_input["auth_code_url"],
            )
            if refresh_token:
                self._user_input["spotify_refresh_token"] = refresh_token
                return await self.async_step_presets()
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({vol.Required("auth_code_url"): str}),
            errors=errors,
            description_placeholders={"auth_url": self._auth_url},
        )

    # ------------------------------------------------------------------
    # Step 4: Preset playlists → HomePod assignments
    # ------------------------------------------------------------------

    async def async_step_presets(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        homepod_names = [h["name"] for h in self._homepods]

        if user_input is not None:
            presets = []
            for i in range(1, MAX_PRESETS + 1):
                name = user_input.get(f"preset_{i}_name", "").strip()
                uri = user_input.get(f"preset_{i}_uri", "").strip()
                homepod = user_input.get(f"preset_{i}_homepod", "").strip()
                if name and uri and homepod:
                    presets.append({"name": name, "uri": uri, "homepod_name": homepod})

            account_name = self._user_input[CONF_ACCOUNT_NAME]
            data = {**self._user_input, CONF_HOMEPODS: self._homepods}
            options = {CONF_PRESETS: presets}

            return self.async_create_entry(
                title=f"Spotify — {account_name}",
                data=data,
                options=options,
            )

        # Build schema: 3 preset slots, each with name / URI / HomePod picker
        fields: dict = {}
        for i in range(1, MAX_PRESETS + 1):
            fields[vol.Optional(f"preset_{i}_name", default="")] = str
            fields[vol.Optional(f"preset_{i}_uri", default="")] = str
            fields[vol.Optional(f"preset_{i}_homepod", default=homepod_names[0] if homepod_names else "")] = vol.In(homepod_names)

        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "account": self._user_input.get(CONF_ACCOUNT_NAME, ""),
                "hint": "Each preset becomes a Siri-addressable switch. Leave unused slots blank.",
            },
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SpotifyHomePodOptionsFlow(config_entry)


class SpotifyHomePodOptionsFlow(config_entries.OptionsFlow):
    """Update presets and HomePod list."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        # Resolve HomePod names from shared registry
        homepod_registry: list[dict] = self.config_entry.data.get(CONF_HOMEPODS, [])
        homepod_names = [h["name"] for h in homepod_registry] or ["HomePod"]

        current_presets: list[dict] = self.config_entry.options.get(CONF_PRESETS, [])

        if user_input is not None:
            # Parse flat form back into presets list
            presets = []
            for i in range(1, MAX_PRESETS + 1):
                name = user_input.pop(f"preset_{i}_name", "").strip()
                uri = user_input.pop(f"preset_{i}_uri", "").strip()
                homepod = user_input.pop(f"preset_{i}_homepod", "").strip()
                if name and uri and homepod:
                    presets.append({"name": name, "uri": uri, "homepod_name": homepod})
            user_input[CONF_PRESETS] = presets
            return self.async_create_entry(title="", data=user_input)

        # Pre-fill preset fields from existing options
        preset_defaults: dict = {}
        for i in range(1, MAX_PRESETS + 1):
            p = current_presets[i - 1] if i <= len(current_presets) else {}
            preset_defaults[vol.Optional(f"preset_{i}_name", default=p.get("name", ""))] = str
            preset_defaults[vol.Optional(f"preset_{i}_uri", default=p.get("uri", ""))] = str
            preset_defaults[vol.Optional(f"preset_{i}_homepod", default=p.get("homepod_name", homepod_names[0]))] = vol.In(homepod_names)

        schema = vol.Schema({
            vol.Optional(CONF_POLL_INTERVAL, default=self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)):
                vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_STREAM_QUALITY, default=self.config_entry.options.get(CONF_STREAM_QUALITY, DEFAULT_STREAM_QUALITY)):
                vol.In(["low", "medium", "high"]),
            vol.Optional(CONF_AUTO_PLAY, default=self.config_entry.options.get(CONF_AUTO_PLAY, True)): bool,
            **preset_defaults,
        })

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
