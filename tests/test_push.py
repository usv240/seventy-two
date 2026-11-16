"""Web Push encryption and signing, checked against the RFC's own test vector and by decrypting as a browser would."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

import push  # noqa: E402

# RFC 8291, Appendix A
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}


def key(b64):
    return ec.derive_private_key(int.from_bytes(push.unb64u(b64), "big"), ec.SECP256R1())


def decrypt(body, ua_private, auth):
    """What the browser does with the message."""
    salt, idlen = body[:16], body[20]
    as_public, cipher = body[21:21 + idlen], body[21 + idlen:]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    h = lambda s, k, i, n: HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=i).derive(k)
    ikm = h(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    plain = AESGCM(h(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(h(salt, ikm, b"Content-Encoding: nonce\x00", 12), cipher, None)
    return plain.rstrip(b"\x00")[:-1]


def test_rfc8291_test_vector():
    body = push.encrypt(RFC["plaintext"], RFC["ua_public"], RFC["auth"], salt=push.unb64u(RFC["salt"]), as_private=key(RFC["as_private"]))
    assert push.b64u(body) == RFC["body"]


def test_a_browser_can_read_what_we_send():
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    auth = os.urandom(16)
    msg = json.dumps({"title": "Measles exposure matches your trip", "url": "/watch/?id=x"}).encode()
    assert decrypt(push.encrypt(msg, ua_pub, push.b64u(auth)), ua, auth) == msg


def test_vapid_signature_verifies():
    k = ec.generate_private_key(ec.SECP256R1())
    head = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc", key=k, now=1_800_000_000)
    jwt = head.split("t=")[1].split(",")[0]
    signing, sig = jwt.rsplit(".", 1)
    raw = push.unb64u(sig)
    k.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                          signing.encode(), ec.ECDSA(hashes.SHA256()))
    claims = json.loads(push.unb64u(signing.split(".")[1]))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] == 1_800_000_000 + 43200


def test_only_real_subscriptions_are_kept():
    ua = ec.generate_private_key(ec.SECP256R1())
    p256 = push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": p256, "auth": push.b64u(os.urandom(16))}}
    assert push.valid_subscription(good)
    assert push.valid_subscription({**good, "endpoint": "http://169.254.169.254/latest"}) is None
    assert push.valid_subscription({**good, "endpoint": "https://evil.example/fcm.googleapis.com"}) is None
    assert push.valid_subscription({**good, "endpoint": "https://wns2-by3p.notify.windows.com/w/?token=x"})
    assert push.valid_subscription({"endpoint": "https://x"}) is None
