# 🎵 Spotify to HomePod — Home Assistant Integration

Stream Spotify directly to your Apple HomePod from Home Assistant, with full playback controls and real-time state sync.

---

## How it works

```
Spotify Cloud API
      │
      │  (playback state + stream URL)
      ▼
Home Assistant
      │
      │  RAOP/AirPlay over local network
      ▼
Apple HomePod
```

1. The integration polls the Spotify API every few seconds for the current playback state.
2. When playback is active, it opens a RAOP (Remote Audio Output Protocol / AirPlay) session to your HomePod.
3. `ffmpeg` is used to transcode the Spotify audio stream to PCM audio, which is packaged into RTP packets and sent directly to the HomePod over UDP.
4. Volume, play/pause, skip, and seek controls are kept in sync between Spotify and the HomePod.

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Home Assistant** | 2023.x or newer |
| **Spotify Premium** | Required for full-track streaming |
| **Spotify Developer App** | Free — create at https://developer.spotify.com/dashboard |
| **Apple HomePod** | Any model, on the same local network as HA |
| **ffmpeg** | Usually pre-installed on HA OS / Supervised. Check with `ffmpeg -version` |
| **Python packages** | `spotipy>=2.23.0` — installed automatically |

---

## Installation

### Option A — HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom Repositories
2. Add `https://github.com/your-repo/spotify-homepod` as an **Integration**
3. Install **Spotify to HomePod**
4. Restart Home Assistant

### Option B — Manual

```bash
# From your Home Assistant config directory:
mkdir -p custom_components/spotify_homepod
cp -r /path/to/download/custom_components/spotify_homepod/* \
       custom_components/spotify_homepod/
```

Restart Home Assistant.

---

## Spotify Developer Setup

1. Go to https://developer.spotify.com/dashboard and **Create an App**
2. Note your **Client ID** and **Client Secret**
3. Click **Edit Settings** → add a Redirect URI:
   ```
   http://localhost:8888/callback
   ```
   (Or use your HA URL, e.g. `http://homeassistant.local:8123/callback`)
4. Save

---

## Finding your HomePod's IP Address

**Option 1 — Home app:**  
Home → tap & hold the HomePod tile → Settings → scroll to find the IP

**Option 2 — Router admin page:**  
Look for a device named "HomePod" in your DHCP lease list.

**Option 3 — Terminal (macOS):**
```bash
dns-sd -q HomePod.local
```

> 💡 **Tip:** Set a static IP or DHCP reservation for your HomePod so it never changes.

---

## Configuration

1. Go to **Settings → Integrations → + Add Integration**
2. Search for **"Spotify to HomePod"**
3. Fill in the form:

| Field | Description |
|---|---|
| Spotify Client ID | From your Developer app |
| Spotify Client Secret | From your Developer app |
| Spotify Redirect URI | Must match what you set in the Developer dashboard |
| HomePod Device Name | Display name (e.g. `Living Room HomePod`) |
| HomePod IP Address | Static IP of your HomePod |
| Local Stream Port | Port for the internal stream server (default: `5050`) |

4. You'll be directed to **authorize Spotify** — click the link, log in, then paste the redirect URL back into HA.

---

## Options (after setup)

Go to the integration's **Configure** button to adjust:

| Option | Default | Description |
|---|---|---|
| Poll interval | 3 sec | How often to check Spotify for state changes |
| Stream quality | high | `low` (96 kbps), `medium` (160 kbps), `high` (320 kbps) |
| Auto-play | on | Automatically start streaming when Spotify plays |

---

## Media Player Controls

The integration creates a **media_player** entity (e.g. `media_player.spotify_living_room_homepod`) with:

- ▶️ Play / ⏸ Pause / ⏹ Stop  
- ⏭ Next track / ⏮ Previous track  
- 🔊 Volume control (synced to both Spotify and HomePod)  
- ⏩ Seek / scrub  
- 🔀 Shuffle toggle  
- 🔁 Repeat (off / track / all)  
- 🎵 Play media (accepts Spotify URIs: `spotify:track:...`, `spotify:playlist:...`, etc.)

---

## Lovelace Example Card

```yaml
type: media-control
entity: media_player.spotify_living_room_homepod
```

Or with the mini-media-player card (HACS):

```yaml
type: custom:mini-media-player
entity: media_player.spotify_living_room_homepod
artwork: cover
hide:
  power: true
```

---

## Automation Example

Play a morning playlist on your HomePod every weekday:

```yaml
alias: Morning Music
trigger:
  - platform: time
    at: "07:30:00"
condition:
  - condition: time
    weekday: [mon, tue, wed, thu, fri]
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.spotify_living_room_homepod
    data:
      media_content_id: "spotify:playlist:37i9dQZF1DX0XUsuxWHRQd"
      media_content_type: music
```

---

## Troubleshooting

**HomePod not reachable during setup**
- Confirm the IP address is correct
- Try using the hostname: `HomePod.local`
- Make sure HA and HomePod are on the same subnet/VLAN

**No audio on HomePod**
- Verify ffmpeg is installed: run `ffmpeg -version` in the HA terminal
- Check the HA logs: Settings → System → Logs, filter by `spotify_homepod`
- Make sure Spotify is actively playing on *some* device (the integration bridges it to HomePod)

**Authorization fails**
- Double-check that the Redirect URI in the Spotify Developer dashboard exactly matches what you entered in HA (including `http://` vs `https://`)
- Make sure your Spotify account has Premium

**Stream cuts out**
- Increase the poll interval to reduce API rate limiting
- Check your local network for packet loss between HA and the HomePod

---

## Technical Notes

- **AirPlay version:** This integration uses RAOP (AirPlay 1), which is broadly compatible with all HomePod models.
- **Audio path:** Spotify → Spotify Web API (stream URL) → ffmpeg (decode + re-encode PCM) → RTP/UDP → HomePod RAOP receiver
- **No Apple Music required:** This uses the Spotify API only; no Apple account needed.
- **Spotify Connect:** The integration does not use Spotify Connect (which would require Spotify's proprietary SDK). Instead it uses the public Web API.

---

## License

MIT License — see LICENSE file.
