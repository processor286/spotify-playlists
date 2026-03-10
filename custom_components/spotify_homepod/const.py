"""Constants for the Spotify to HomePod integration."""

DOMAIN = "spotify_homepod"
PLATFORMS = ["media_player", "switch"]

# Config keys — account level
CONF_SPOTIFY_CLIENT_ID = "spotify_client_id"
CONF_SPOTIFY_CLIENT_SECRET = "spotify_client_secret"
CONF_SPOTIFY_REDIRECT_URI = "spotify_redirect_uri"
CONF_ACCOUNT_NAME = "account_name"          # e.g. "Alice"

# Config keys — HomePod registry (stored in hass.data, shared across entries)
CONF_HOMEPODS = "homepods"                   # list of {name, host}

# Options keys
CONF_POLL_INTERVAL = "poll_interval"
CONF_STREAM_QUALITY = "stream_quality"
CONF_AUTO_PLAY = "auto_play"
# Preset schema per account: list of {name, uri, homepod_name}
CONF_PRESETS = "presets"

# Defaults
DEFAULT_STREAM_PORT = 5050
DEFAULT_POLL_INTERVAL = 3
DEFAULT_STREAM_QUALITY = "high"

# Spotify OAuth scopes
SPOTIFY_SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "streaming",
    "user-read-email",
    "user-read-private",
    "playlist-read-private",
    "playlist-read-collaborative",
]

# AirPlay
AIRPLAY_PORT = 5000

# Quality → bitrate
QUALITY_BITRATE = {
    "low": 96,
    "medium": 160,
    "high": 320,
}

# Shared HomePod registry key in hass.data
HOMEPOD_REGISTRY_KEY = f"{DOMAIN}_homepod_registry"

# Services
SERVICE_TRANSFER = "transfer_to_homepod"

# Attributes
ATTR_ACCOUNT_NAME = "account_name"
ATTR_HOMEPOD_NAME = "homepod_name"
ATTR_HOMEPOD_HOST = "homepod_host"
ATTR_PRESET_NAME = "preset_name"
ATTR_SPOTIFY_URI = "spotify_uri"
