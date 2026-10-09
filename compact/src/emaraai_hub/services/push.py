"""Push notifications: what the bell shows reaches the owner's devices even when the Control Center is closed (Web Push).

The browser of each device (Chrome on the PC, Chrome on the phone through the Tailscale address ...) subscribes once from the
bell's settings. The hub then sends every new notice to the push service of that browser (Google, Mozilla, Apple), which
wakes the device. The hub's service worker (/sw.js) shows it; a click opens the Control Center on the right page.

What leaves the PC: only the notice's title and a short text (PUSH_TEXT characters), end-to-end encrypted for that one
browser (RFC 8291, aes128gcm) and signed with this hub's own key (VAPID, RFC 8292). The push service cannot read it.
Nothing here needs a library beyond `cryptography`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..core.errors import InvalidInput
from ..infra.logging import get_logger

log = get_logger("services")

PUSH_TEXT = 120          # the short text: the owner chose "title and short text" to leave the PC
URGENT = ("decision", "approval", "problem")
CATS = ("decision", "approval", "master", "assistant", "maintainer", "problem", "project")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    text = str(text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _point(key) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key=None) -> bytes:
    """RFC 8291: the message body for one browser subscription (one record, aes128gcm)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_public) != 65 or len(auth_secret) < 16:
        raise InvalidInput("The browser's push keys are not valid.", fix="Switch phone notifications off and on again on that device.")
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!L", 4096) + bytes([len(as_public)]) + as_public + body


class PushService:
    def __init__(self, hub, transport=None):
        self.hub = hub
        self.transport = transport
        self._key = None

    @property
    def db(self):
        return self.hub.services.db

    @property
    def kv(self):
        return self.hub.services.repos.kv

    # ------------------------------------------------------------------ this hub's own key (VAPID)
    def _private(self):
        if self._key is None:
            pem = self.kv.get("push.vapid_private")
            if pem:
                self._key = serialization.load_pem_private_key(pem.encode(), password=None)
            else:
                self._key = ec.generate_private_key(ec.SECP256R1())
                self.kv.set("push.vapid_private", self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                         serialization.NoEncryption()).decode())
        return self._key

    def public_key(self) -> str:
        return b64u(_point(self._private().public_key()))

    def _vapid(self, endpoint: str) -> str:
        u = urlparse(endpoint)
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                                  "sub": "mailto:owner@emaraai.local"}, separators=(",", ":")).encode())
        r, s = decode_dss_signature(self._private().sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        token = f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        return f"vapid t={token}, k={self.public_key()}"

    # ------------------------------------------------------------------ devices
    def subscribe(self, sub: dict, cats: list | None = None, label: str = "") -> dict:
        endpoint = str((sub or {}).get("endpoint") or "")
        keys = (sub or {}).get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise InvalidInput("That is not a browser push subscription.", fix="Switch it on again from the bell's settings.")
        encrypt(b"check", keys["p256dh"], keys["auth"])          # refuses keys that cannot work, now rather than at the first notice
        chosen = [c for c in (cats if cats is not None else CATS) if c in CATS]
        now = time.time()
        self.db.exec("INSERT INTO push_subs(endpoint, p256dh, auth, cats, label, created_at, last_ok, fails) VALUES (?,?,?,?,?,?,NULL,0) "
                      "ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, cats = excluded.cats, label = excluded.label, fails = 0",
                      (endpoint, keys["p256dh"], keys["auth"], json.dumps(chosen), (label or "A device")[:80], now))
        if not self.kv.get("push.since"):
            self.kv.set("push.since", str(now))                  # the first device hears only what happens from now on
        return {"subscribed": True, "cats": chosen}

    def unsubscribe(self, endpoint: str) -> dict:
        self.db.exec("DELETE FROM push_subs WHERE endpoint = ?", (str(endpoint or ""),))
        return {"subscribed": False}

    def devices(self) -> list[dict]:
        return [{"label": d["label"], "cats": json.loads(d["cats"] or "[]"), "since": d["created_at"], "last_ok": d["last_ok"], "fails": d["fails"],
                 "service": urlparse(d["endpoint"]).netloc} for d in self.db.all("SELECT * FROM push_subs ORDER BY created_at")]

    # ------------------------------------------------------------------ sending
    async def _send(self, client: httpx.AsyncClient, dev: dict, message: dict) -> str:
        body = encrypt(json.dumps(message, ensure_ascii=False).encode(), dev["p256dh"], dev["auth"])
        headers = {"Authorization": self._vapid(dev["endpoint"]), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                   "TTL": "86400", "Urgency": "high" if message.get("cat") in URGENT else "normal"}
        try:
            r = await client.post(dev["endpoint"], content=body, headers=headers)
        except httpx.HTTPError as e:
            self.db.exec("UPDATE push_subs SET fails = fails + 1 WHERE endpoint = ?", (dev["endpoint"],))
            log.warning("push not delivered", service=urlparse(dev["endpoint"]).netloc, error=str(e)[:200])
            return "error"
        if r.status_code in (200, 201, 202):
            self.db.exec("UPDATE push_subs SET last_ok = ?, fails = 0 WHERE endpoint = ?", (time.time(), dev["endpoint"]))
            return "ok"
        if r.status_code in (404, 410):                           # the browser dropped the subscription: so does the hub
            self.unsubscribe(dev["endpoint"])
            log.info("push subscription gone", service=urlparse(dev["endpoint"]).netloc)
            return "gone"
        self.db.exec("UPDATE push_subs SET fails = fails + 1 WHERE endpoint = ?", (dev["endpoint"],))
        self.db.exec("DELETE FROM push_subs WHERE fails >= 20")
        log.warning("push refused", service=urlparse(dev["endpoint"]).netloc, status=r.status_code, body=r.text[:200])
        return f"http_{r.status_code}"

    @staticmethod
    def message(notices: list[dict]) -> dict:
        """Title and short text only. Several notices at once become one notification that says how many."""
        last = notices[-1]
        title = last["title"] + (f" · {last['project']}" if last.get("project") else "")
        text = " ".join(str(last.get("text") or "").split())
        text = text[:PUSH_TEXT - 1] + "…" if len(text) > PUSH_TEXT else text
        if len(notices) > 1:
            title = f"{len(notices)} new: {title}"
        cat = next((n["cat"] for n in notices if n["cat"] in URGENT), last["cat"])
        return {"title": title[:110], "body": text, "link": last.get("link") or "#/", "tag": "emara-" + str(last["id"]), "cat": cat}

    async def deliver(self, notices: list[dict]) -> dict:
        """Send new notices to every device that wants their kind."""
        devs = self.db.all("SELECT * FROM push_subs")
        if not devs or not notices:
            return {"sent": 0}
        sent = 0
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            for dev in devs:
                want = set(json.loads(dev["cats"] or "[]"))
                mine = [n for n in notices if n["cat"] in want]
                if mine and await self._send(client, dev, self.message(mine)) == "ok":
                    sent += 1
        return {"sent": sent}

    async def test(self, endpoint: str = "") -> dict:
        devs = [d for d in self.db.all("SELECT * FROM push_subs") if not endpoint or d["endpoint"] == endpoint]
        if not devs:
            raise InvalidInput("No device gets notifications yet.", fix="Switch them on in the bell's settings on that device first.")
        out = []
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            for d in devs:
                out.append({"label": d["label"], "result": await self._send(client, d, {"title": "EmaraAI notifications work",
                                                                                        "body": "This is how a decision or a message will reach you.",
                                                                                        "link": "#/", "tag": "emara-test", "cat": "project"})})
        return {"devices": out}

    async def tick(self) -> int:
        """One round of the worker: what is new since the last round goes out."""
        if not self.db.one("SELECT 1 AS x FROM push_subs LIMIT 1"):
            return 0
        since = float(self.kv.get("push.since") or time.time())
        notices = self.hub.services.company.notices(since)["notices"]
        if not notices:
            return 0
        self.kv.set("push.since", str(max(n["ts"] for n in notices)))
        await self.deliver(notices)
        return len(notices)


async def push_worker(hub, interval: float = 10.0) -> None:
    while True:
        try:
            await hub.push.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("push round failed")
        await asyncio.sleep(interval)
