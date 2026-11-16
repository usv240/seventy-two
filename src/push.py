"""Web Push: a notification on the visitor's own phone or computer, with no account, no email and no app.

The browser gives us a subscription (an endpoint at its push service, a public key and a secret). We encrypt
each message to that key (RFC 8291, aes128gcm) and sign a short claim with our own key (RFC 8292, VAPID), so
the push service cannot read the message and only this site can send to the subscription. Standard library
plus `cryptography`. The private key lives in SSM Parameter Store, never in code or in the template.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_PARAM = os.environ.get("VAPID_KEY_PARAM", "")
SUBJECT = os.environ.get("VAPID_SUBJECT", "")
_key = None


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _point(pub):
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def private_key():
    global _key
    if _key is None:
        import boto3
        raw = boto3.client("ssm").get_parameter(Name=KEY_PARAM, WithDecryption=True)["Parameter"]["Value"]
        _key = ec.derive_private_key(int.from_bytes(unb64u(raw), "big"), ec.SECP256R1())
    return _key


def public_key_b64(key=None):
    """The applicationServerKey the browser needs to subscribe."""
    return b64u(_point((key or private_key()).public_key()))


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload, ua_public_b64, auth_b64, salt=None, as_private=None, rs=4096):
    """RFC 8291 message encryption. Returns the request body (the aes128gcm header, then the ciphertext)."""
    ua_public = unb64u(ua_public_b64)
    auth = unb64u(auth_b64)
    salt = salt or os.urandom(16)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02: the last (and only) record
    return salt + struct.pack("!IB", rs, len(as_public)) + as_public + cipher


def vapid_header(endpoint, key=None, now=None, subject=""):
    """RFC 8292: a signed claim that this site sent the push, valid for 12 hours. The contact is the site's own address."""
    key = key or private_key()
    u = urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600),
              "sub": subject or SUBJECT or "https://github.com/"}
    signing = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." + \
        b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={public_key_b64(key)}"


PUSH_SERVICES = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
                 "notify.windows.com", "push.apple.com")


def known_service(endpoint):
    """Pushes only ever go to a browser vendor's push service, so a forged subscription cannot aim us anywhere else."""
    u = urlsplit(endpoint)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == s or host.endswith("." + s) for s in PUSH_SERVICES)


def valid_subscription(sub):
    """Only a browser push service's https endpoint with both keys is kept."""
    try:
        ok = (isinstance(sub, dict) and known_service(str(sub.get("endpoint", "")))
              and len(unb64u(sub["keys"]["p256dh"])) == 65 and len(unb64u(sub["keys"]["auth"])) == 16)
    except Exception:
        return None
    return {"endpoint": sub["endpoint"][:1000], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}} if ok else None


def send(sub, title, body, url, ttl=86400):
    """Returns the push service's HTTP status. 404 and 410 mean the subscription is gone and should be forgotten."""
    site = "{0.scheme}://{0.netloc}".format(urlsplit(url)) if url.startswith("https://") else ""
    data = json.dumps({"title": title, "body": body, "url": url}).encode()
    req = urllib.request.Request(sub["endpoint"], method="POST", data=encrypt(data, sub["keys"]["p256dh"], sub["keys"]["auth"]),
                                 headers={"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                          "TTL": str(ttl), "Urgency": "high", "Authorization": vapid_header(sub["endpoint"], subject=site)})
    import obs
    try:
        with obs.remote(urlsplit(sub["endpoint"]).netloc), urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # a push is a convenience; the page and the calendar still show everything
        print(json.dumps({"level": "warn", "push_failed": str(e)[:200]}))
        return 0


def send_all(subs, title, body, url):
    """Sends to every subscription; returns the ones still alive."""
    import obs
    alive, sent = [], 0
    for sub in subs or []:
        status = send(sub, title, body, url)
        print(json.dumps({"push": urlsplit(sub["endpoint"]).netloc, "status": status}))
        sent += 200 <= status < 300
        if status not in (404, 410):
            alive.append(sub)
    if subs:
        obs.metric(PushSent=sent, PushFailed=len(subs) - sent)
    return alive
