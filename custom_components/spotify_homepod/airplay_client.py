"""
AirPlay / RAOP client for streaming audio to Apple HomePod.

This module implements a minimal RAOP (Remote Audio Output Protocol) client
that can push an audio stream to an AirPlay-compatible device such as Apple HomePod.

Protocol overview:
  1. Announce the session via RTSP ANNOUNCE
  2. Set up the transport via RTSP SETUP
  3. Start recording via RTSP RECORD
  4. Send RTP audio packets
  5. Control volume via RTSP SET_PARAMETER
  6. Tear down via RTSP TEARDOWN
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import random
import socket
import struct
import subprocess
import time
import threading
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# RAOP defaults
RAOP_SAMPLE_RATE = 44100
RAOP_CHANNELS = 2
RAOP_SAMPLE_SIZE = 16
FRAMES_PER_PACKET = 352
RTP_HEADER_SIZE = 12


class RaopSession:
    """
    Manages a single RAOP streaming session to an AirPlay device (HomePod).

    Usage:
        session = RaopSession(host="192.168.1.50", port=5000)
        await session.connect()
        await session.set_volume(80)
        await session.start_stream(audio_source_url="http://...")
        await session.stop()
    """

    def __init__(
        self,
        host: str,
        port: int = 5000,
        volume: int = 80,
    ) -> None:
        self.host = host
        self.port = port
        self.volume = volume

        self._rtsp_socket: Optional[socket.socket] = None
        self._rtp_socket: Optional[socket.socket] = None
        self._cseq = 0
        self._session_id: Optional[str] = None
        self._active = False
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._stream_thread: Optional[threading.Thread] = None

        # Random SSRC and sequence
        self._ssrc = random.randint(0, 0xFFFFFFFF)
        self._seq = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)

        # Local RTP port
        self._local_rtp_port: Optional[int] = None
        self._remote_rtp_port: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Open RTSP connection and perform ANNOUNCE/SETUP/RECORD handshake."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    async def disconnect(self) -> None:
        """Send TEARDOWN and close all sockets."""
        self._active = False
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._teardown_sync)

    async def set_volume(self, volume_pct: int) -> None:
        """Set playback volume (0–100)."""
        self.volume = max(0, min(100, volume_pct))
        if self._rtsp_socket and self._session_id:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._set_volume_sync, self.volume)

    async def start_stream(self, audio_url: str) -> None:
        """
        Begin streaming audio from `audio_url` to the HomePod.

        ffmpeg is used to decode and encode the source to PCM,
        then RTP packets are built and sent.
        """
        self._active = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._stream_sync, audio_url)

    async def stop_stream(self) -> None:
        """Stop the active stream."""
        self._active = False
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.terminate()
            except Exception:
                pass
        await self.disconnect()

    # ------------------------------------------------------------------
    # Internal sync helpers (run in executor)
    # ------------------------------------------------------------------

    def _connect_sync(self) -> bool:
        try:
            self._rtsp_socket = socket.create_connection(
                (self.host, self.port), timeout=10
            )
            _LOGGER.debug("RTSP connected to %s:%d", self.host, self.port)

            # RTSP ANNOUNCE
            sdp = self._build_sdp()
            resp = self._rtsp_request(
                "ANNOUNCE",
                f"rtsp://{self.host}/stream",
                extra_headers={
                    "Content-Type": "application/sdp",
                    "Content-Length": str(len(sdp)),
                },
                body=sdp,
            )
            if not resp or resp["status"] != 200:
                _LOGGER.error("ANNOUNCE failed: %s", resp)
                return False

            # RTSP SETUP
            self._local_rtp_port = self._find_free_port()
            resp = self._rtsp_request(
                "SETUP",
                f"rtsp://{self.host}/stream",
                extra_headers={
                    "Transport": (
                        f"RTP/AVP/UDP;unicast;"
                        f"interleaved=0-1;"
                        f"mode=record;"
                        f"control_port={self._local_rtp_port + 1};"
                        f"timing_port={self._local_rtp_port + 2};"
                        f"server_port={self._local_rtp_port}"
                    )
                },
            )
            if not resp or resp["status"] != 200:
                _LOGGER.error("SETUP failed: %s", resp)
                return False

            self._session_id = resp.get("headers", {}).get("Session", "0")
            transport = resp.get("headers", {}).get("Transport", "")
            self._remote_rtp_port = self._parse_server_port(transport)

            # Open RTP socket
            self._rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rtp_socket.bind(("", self._local_rtp_port))

            # RTSP RECORD
            resp = self._rtsp_request(
                "RECORD",
                f"rtsp://{self.host}/stream",
                extra_headers={
                    "Range": "npt=0-",
                    "RTP-Info": f"seq={self._seq};rtptime={self._timestamp}",
                },
            )
            if not resp or resp["status"] != 200:
                _LOGGER.error("RECORD failed: %s", resp)
                return False

            # Set initial volume
            self._set_volume_sync(self.volume)
            return True

        except Exception as err:
            _LOGGER.error("Connection to HomePod failed: %s", err)
            return False

    def _teardown_sync(self) -> None:
        try:
            if self._rtsp_socket and self._session_id:
                self._rtsp_request("TEARDOWN", f"rtsp://{self.host}/stream")
        except Exception:
            pass
        finally:
            for sock in (self._rtsp_socket, self._rtp_socket):
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            self._rtsp_socket = None
            self._rtp_socket = None

    def _set_volume_sync(self, volume_pct: int) -> None:
        """Convert 0-100 to RAOP dB scale (-144 to 0) and send."""
        if volume_pct == 0:
            db = -144.0
        else:
            db = (volume_pct / 100.0) * 30.0 - 30.0  # -30 to 0 dB range
        body = f"volume: {db:.6f}\r\n"
        self._rtsp_request(
            "SET_PARAMETER",
            f"rtsp://{self.host}/stream",
            extra_headers={
                "Content-Type": "text/parameters",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

    def _stream_sync(self, audio_url: str) -> None:
        """Use ffmpeg to pull audio and send as RTP packets."""
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-re",                     # Read at native frame rate
            "-i", audio_url,
            "-ar", str(RAOP_SAMPLE_RATE),
            "-ac", str(RAOP_CHANNELS),
            "-f", "s16be",             # Signed 16-bit big-endian PCM
            "pipe:1",
        ]
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            bytes_per_frame = RAOP_CHANNELS * (RAOP_SAMPLE_SIZE // 8)
            chunk_size = FRAMES_PER_PACKET * bytes_per_frame

            while self._active:
                pcm_data = self._ffmpeg_proc.stdout.read(chunk_size)
                if not pcm_data:
                    break
                # Pad last packet if needed
                if len(pcm_data) < chunk_size:
                    pcm_data += b"\x00" * (chunk_size - len(pcm_data))

                packet = self._build_rtp_packet(pcm_data)
                if self._rtp_socket and self._remote_rtp_port:
                    self._rtp_socket.sendto(
                        packet, (self.host, self._remote_rtp_port)
                    )

                self._seq = (self._seq + 1) & 0xFFFF
                self._timestamp = (
                    self._timestamp + FRAMES_PER_PACKET
                ) & 0xFFFFFFFF

                # Pace the stream
                time.sleep(FRAMES_PER_PACKET / RAOP_SAMPLE_RATE * 0.95)

        except Exception as err:
            _LOGGER.error("Streaming error: %s", err)
        finally:
            if self._ffmpeg_proc:
                self._ffmpeg_proc.wait()

    # ------------------------------------------------------------------
    # RTSP helpers
    # ------------------------------------------------------------------

    def _rtsp_request(
        self,
        method: str,
        url: str,
        extra_headers: dict | None = None,
        body: str = "",
    ) -> dict | None:
        self._cseq += 1
        lines = [f"{method} {url} RTSP/1.0", f"CSeq: {self._cseq}"]
        if self._session_id and method not in ("ANNOUNCE", "SETUP"):
            lines.append(f"Session: {self._session_id}")
        if extra_headers:
            for k, v in extra_headers.items():
                lines.append(f"{k}: {v}")
        lines.append("")
        if body:
            lines.append(body)
        else:
            lines.append("")

        request = "\r\n".join(lines)
        try:
            self._rtsp_socket.sendall(request.encode())
            return self._rtsp_read_response()
        except Exception as err:
            _LOGGER.error("RTSP request %s failed: %s", method, err)
            return None

    def _rtsp_read_response(self) -> dict:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self._rtsp_socket.recv(4096)
            if not chunk:
                break
            data += chunk

        lines = data.decode(errors="replace").split("\r\n")
        status_line = lines[0]
        status = int(status_line.split(" ")[1]) if " " in status_line else 0
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k] = v
        return {"status": status, "headers": headers}

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _build_sdp(self) -> str:
        return (
            "v=0\r\n"
            f"o=iTunes {self._session_id or random.randint(1000, 9999)} 0 IN IP4 127.0.0.1\r\n"
            "s=iTunes\r\n"
            f"c=IN IP4 {self.host}\r\n"
            "t=0 0\r\n"
            "m=audio 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 AppleLossless/44100/2\r\n"
            f"a=fmtp:96 {FRAMES_PER_PACKET} 0 {RAOP_SAMPLE_SIZE} 40 10 14 {RAOP_CHANNELS} 255 0 0 {RAOP_SAMPLE_RATE}\r\n"
        )

    def _build_rtp_packet(self, payload: bytes) -> bytes:
        header = struct.pack(
            "!BBHII",
            0x80,            # Version=2, no padding, no extension, CC=0
            0x60,            # Marker=0, payload type=96
            self._seq,
            self._timestamp,
            self._ssrc,
        )
        return header + payload

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    @staticmethod
    def _parse_server_port(transport: str) -> int:
        for part in transport.split(";"):
            part = part.strip()
            if part.startswith("server_port="):
                try:
                    return int(part.split("=")[1])
                except ValueError:
                    pass
        return 6000  # fallback
