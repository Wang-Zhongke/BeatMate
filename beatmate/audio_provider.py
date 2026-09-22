"""Official instrumental contract and bounded, pinned HTTPS transport (no retries)."""

import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import struct
import wave
import zipfile
from urllib.parse import urlsplit, quote

MODELS = ("mureka-7.6", "mureka-8", "mureka-9", "mureka-9.5")
MAX_AUDIO = 100 * 1024 * 1024
STEM_RESPONSE_TIMEOUT = 600


class AudioError(Exception):
    pass


class BeforeSubmissionError(AudioError):
    """Connection failed before any HTTP request was sent. Never auto-retry."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Rejected(AudioError):
    """A documented HTTP rejection, as opposed to an ambiguous submit."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def public_target(url, allowed):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.fragment
        or p.port not in (None, 443)
        or not p.hostname
        or p.hostname not in allowed
    ):
        raise AudioError("Download/API URL is not an allowed HTTPS target")
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        raise AudioError("Non-public network target rejected")
    return p, addresses[0][4][0]


def https_request(method, url, headers, body, limit, allowed):
    conn = None
    sending = False
    stage = "dns_resolution"
    try:
        p, address = public_target(url, allowed)
        conn = http.client.HTTPSConnection(p.hostname, timeout=40)
        # Resolve once and pin the connection; TLS still checks the official hostname.
        stage = "tcp_connect"
        raw = socket.create_connection((address, 443), timeout=40)
        try:
            stage = "tls_handshake"
            conn.sock = ssl.create_default_context().wrap_socket(
                raw, server_hostname=p.hostname
            )
        except Exception:
            raw.close()
            raise
        sending = True  # From here, partial transmission is possible.
        conn.request(method, p.path + ("?" + p.query if p.query else ""), body, headers)
        # Stem returns finished archives synchronously; allow processing beyond 40s.
        if (
            method == "POST"
            and p.hostname == "api.mureka.ai"
            and p.path == "/v1/song/stem"
        ):
            conn.sock.settimeout(STEM_RESPONSE_TIMEOUT)
        r = conn.getresponse()
        if not 200 <= r.status < 300:
            if r.status in (400, 401, 402, 403, 404, 422, 429):
                raise Rejected(
                    f"Provider HTTP {r.status}; check API access, model and credits",
                    r.status,
                )
            raise AudioError(f"Provider HTTP {r.status}; no automatic resubmission")
        length = r.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > limit):
            raise AudioError("Response size invalid")
        data = r.read(limit + 1)
        if not data or len(data) > limit or (length and len(data) != int(length)):
            raise AudioError("Empty, oversized or incomplete response")
        return data
    except ssl.SSLCertVerificationError:
        if not sending:
            raise BeforeSubmissionError("tls_certificate") from None
        raise AudioError(
            "TLS failure after request started; response not confirmed"
        ) from None
    except TimeoutError:
        if not sending:
            raise BeforeSubmissionError(stage) from None
        raise AudioError(
            "Network timeout after request started; response not confirmed"
        ) from None
    except AudioError:
        if not sending:
            raise BeforeSubmissionError("target_validation") from None
        raise
    except Exception:
        if not sending:
            raise BeforeSubmissionError(stage) from None
        raise AudioError(
            "Network failure after request started; response not confirmed"
        ) from None
    finally:
        if conn is not None:
            conn.close()


class MurekaAdapter:
    provider = "mureka"

    def __init__(self, env=None, transport=None):
        env = os.environ if env is None else env
        self.key = env.get("MUREKA_API_KEY", "")
        self.model = env.get("MUREKA_MODEL", "")
        self.base = env.get("MUREKA_BASE_URL", "https://api.mureka.ai").rstrip("/")
        self.enabled = env.get("BEATMATE_AUDIO_LIVE") == "1"
        self.hosts = tuple(
            x.strip()
            for x in env.get("MUREKA_DOWNLOAD_HOSTS", "cdn.mureka.ai").split(",")
            if x.strip()
        )
        self.transport = transport or https_request

    def errors(self):
        result = []
        if not self.key:
            result.append("MUREKA_API_KEY missing")
        if self.model not in MODELS:
            result.append("MUREKA_MODEL must explicitly name " + ", ".join(MODELS))
        if self.base != "https://api.mureka.ai":
            result.append(
                "Only official international https://api.mureka.ai is enabled"
            )
        if not self.enabled:
            result.append("BEATMATE_AUDIO_LIVE=1 required for paid submissions")
        if any(not re.fullmatch(r"[a-zA-Z0-9.-]+", h) for h in self.hosts):
            result.append("Invalid download host configuration")
        return result

    def request(self, method, path, payload=None):
        if self.errors():
            raise AudioError("; ".join(self.errors()))
        raw = self.transport(
            method,
            self.base + path,
            {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            json.dumps(payload).encode() if payload is not None else None,
            1024 * 1024,
            ("api.mureka.ai",),
        )
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise AudioError("Invalid provider JSON response") from None

    def submit(self, task):
        song = task["brief"].get("creation_mode") == "song_stems"
        payload = {
            "model": task["requested_model"],
            "prompt": task["brief"]["final_prompt"],
            "n": task["n"],
            "stream": False,
        }
        if song:
            payload["lyrics"] = task["brief"]["lyrics"]
        return self.request(
            "POST", "/v1/" + ("song" if song else "instrumental") + "/generate", payload
        )

    def query(self, task):
        kind = (
            "song"
            if task["brief"].get("creation_mode") == "song_stems"
            else "instrumental"
        )
        return self.request(
            "GET", "/v1/" + kind + "/query/" + quote(task["provider_task_id"], safe="")
        )

    def separate(self, choice):
        url = choice.get("url")
        if not isinstance(url, str) or not url:
            raise BeforeSubmissionError("target_validation")
        try:
            public_target(url, self.hosts)
        except Exception:
            raise BeforeSubmissionError("target_validation") from None
        return self.request(
            "POST", "/v1/song/stem", {"url": url, "model": "audio-separation-3"}
        )

    def download_midi(self, job):
        from .audio_midi import MAX_MIDI_ZIP

        return self.transport(
            "GET", job["midi_zip_url"], {}, None, MAX_MIDI_ZIP, self.hosts
        )

    def download_stems(self, job):
        return self.transport("GET", job["zip_url"], {}, None, MAX_AUDIO, self.hosts)

    def download(self, choice):
        url = choice.get("wav_url") or choice.get("flac_url") or choice.get("url")
        if not isinstance(url, str):
            raise AudioError("Candidate has no downloadable original audio")
        # Credentials never accompany a CDN request.
        return self.transport("GET", url, {}, None, MAX_AUDIO, self.hosts)


class MockAudioAdapter:
    provider = "mock"
    model = "synthetic-test-v1"
    base = None

    def errors(self):
        return []

    def submit(self, task):
        return {"id": "mock-" + task["id"], "status": "queued", "model": self.model}

    def query(self, task):
        return {
            "id": task["provider_task_id"],
            "status": "succeeded",
            "model": self.model,
            "choices": [
                {
                    "id": f"{task['id']}-{i}",
                    "index": i,
                    "duration": 4000,
                    "url": "mock://synthetic-test",
                }
                for i in range(task["n"])
            ],
        }

    def download(self, choice):
        # A plain test signal, independent of native MIDI generators. No music-quality claim.
        out = io.BytesIO()
        with wave.open(out, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            frequency = 220 + 55 * choice["index"]
            f.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        int(
                            2200
                            * math.sin(2 * math.pi * frequency * i / 16000)
                            * min(1, i / 1600, (64000 - i) / 1600)
                        ),
                    )
                    for i in range(64000)
                )
            )
        return out.getvalue()

    def separate(self, choice):
        return {
            "zip_url": "mock://stem-archive",
            "midi_zip_url": "mock://midi-archive",
            "expires_at": 0,
        }

    def download_midi(self, job):
        # Independent fixture notes; not a transcription of the audio test signals.
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, pitch in [("instrumental", 60), ("vocals", 67)]:
                track = (
                    b"\x00\x90"
                    + bytes([pitch, 80])
                    + b"\x83\x60\x80"
                    + bytes([pitch, 0])
                    + b"\x00\xff\x2f\x00"
                )
                data = (
                    b"MThd"
                    + struct.pack(">IHHH", 6, 0, 1, 480)
                    + b"MTrk"
                    + struct.pack(">I", len(track))
                    + track
                )
                archive.writestr(name + ".mid", data)
        return out.getvalue()

    def download_stems(self, job):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "instrumental.wav", self.download({"index": job["index"] + 5})
            )
            archive.writestr("vocals.wav", self.download({"index": job["index"] + 10}))
        return out.getvalue()


def create_audio_adapter(env=None):
    env = os.environ if env is None else env
    provider = env.get("BEATMATE_AUDIO_PROVIDER", "mock")
    if provider == "mock":
        return MockAudioAdapter()
    if provider == "mureka":
        return MurekaAdapter(env)
    raise ValueError("BEATMATE_AUDIO_PROVIDER must be mock or mureka")
