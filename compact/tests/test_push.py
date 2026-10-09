"""Push notifications: encrypted for one browser (RFC 8291), signed with the hub's key (VAPID), sent to the devices that want them."""
import json
import struct

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from emaraai_hub.core.errors import InvalidInput
from emaraai_hub.services.push import PushService, _hkdf, _point, b64u, encrypt, unb64u


def _browser():
    """What a browser keeps for one subscription: its key pair and the auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, b64u(_point(key.public_key())), b64u(b"0123456789abcdef")


def _decrypt(body: bytes, key, p256dh: str, auth: str) -> bytes:
    """The browser's side of RFC 8291."""
    salt, rs, idlen = body[:16], struct.unpack("!L", body[16:20])[0], body[20]
    as_public, cipher = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(unb64u(auth), shared, b"WebPush: info\x00" + unb64u(p256dh) + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, cipher, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_only_that_browser_can_read_a_push():
    key, p256dh, auth = _browser()
    body = encrypt(json.dumps({"title": "Decision waiting"}).encode(), p256dh, auth)
    assert json.loads(_decrypt(body, key, p256dh, auth)) == {"title": "Decision waiting"}
    other, _, _ = _browser()
    with pytest.raises(Exception):
        _decrypt(body, other, p256dh, auth)
    with pytest.raises(InvalidInput):
        encrypt(b"x", "bad", auth)


class _Push:
    def __init__(self, status=201):
        self.status, self.calls = status, []

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        return httpx.Response(self.status)


def _sub(endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    key, p256dh, auth = _browser()
    return key, {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _vapid_ok(push: PushService, header: str, endpoint: str) -> dict:
    token = header.split("t=", 1)[1].split(",", 1)[0]
    assert header.endswith("k=" + push.public_key())
    head, claims, sig = token.split(".")
    raw = unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(push.public_key()))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(unb64u(claims))


async def test_a_notice_reaches_the_devices_that_want_its_kind(hub):
    fake = _Push()
    push = PushService(hub, transport=httpx.MockTransport(fake))
    key, sub = _sub()
    _, quiet = _sub("https://updates.push.services.mozilla.com/wpush/v2/xyz")
    push.subscribe(sub, ["decision"], "Android phone · Chrome")
    push.subscribe(quiet, ["project"], "Windows PC · Firefox")
    long_text = "Should the shop use PostgreSQL or SQLite for the orders? " * 6
    out = await push.deliver([{"id": "e1", "ts": 1.0, "cat": "decision", "title": "A decision waits for you", "text": long_text, "project": "shop", "link": "#/decisions"}])
    assert out == {"sent": 1} and len(fake.calls) == 1                        # the Firefox device did not ask for decisions
    req = fake.calls[0]
    assert str(req.url) == sub["endpoint"] and req.headers["content-encoding"] == "aes128gcm" and req.headers["urgency"] == "high"
    assert _vapid_ok(push, req.headers["authorization"], sub["endpoint"])["aud"] == "https://fcm.googleapis.com"
    msg = json.loads(_decrypt(req.content, key, sub["keys"]["p256dh"], sub["keys"]["auth"]))
    assert msg["title"] == "A decision waits for you · shop" and msg["link"] == "#/decisions"
    assert len(msg["body"]) <= 120 and msg["body"].endswith("…")                    # only a short text leaves the PC
    assert set(msg) == {"title", "body", "link", "tag", "cat"}
    assert [d["label"] for d in push.devices()] == ["Android phone · Chrome", "Windows PC · Firefox"]


async def test_several_notices_become_one_and_a_dropped_device_is_forgotten(hub):
    fake = _Push(status=410)
    push = PushService(hub, transport=httpx.MockTransport(fake))
    key, sub = _sub()
    push.subscribe(sub, None, "phone")
    notes = [{"id": f"m{i}", "ts": float(i), "cat": "master", "title": f"Mr. Samir (Master) wrote to you {i}", "text": "hello", "project": "", "link": "#/messages"}
             for i in range(3)]
    assert push.message(notes)["title"].startswith("3 new: ")
    await push.deliver(notes)
    assert len(fake.calls) == 1 and push.devices() == []                      # 410: the browser dropped it, so does the hub


async def test_the_worker_sends_only_what_is_new(hub, master):
    fake = _Push()
    push = PushService(hub, transport=httpx.MockTransport(fake))
    assert await push.tick() == 0                                              # no device: nothing is even looked at
    _, sub = _sub()
    push.subscribe(sub, None, "phone")
    hub.services.repos.kv.set("push.since", "0")
    await master("project_create", name="shop", goal="Build a shop")
    hub.services.company.notices = lambda since: {"notices": [n for n in [{"id": "e9", "ts": 5.0, "cat": "problem", "title": "Work is blocked", "text": "x",
                                                                           "project": "shop", "link": "#/tasks"}] if n["ts"] > since]}
    assert await push.tick() == 1 and len(fake.calls) == 1
    assert await push.tick() == 0 and len(fake.calls) == 1                      # the same notice is not sent twice
    with pytest.raises(InvalidInput):
        push.subscribe({"endpoint": "http://not-https", "keys": {}})
