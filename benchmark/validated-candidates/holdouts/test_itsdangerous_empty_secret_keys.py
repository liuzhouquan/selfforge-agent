"""Independent holdout checks for empty secret_key validation in itsdangerous."""

import pytest
from itsdangerous import (
    Serializer,
    Signer,
    TimedSerializer,
    TimestampSigner,
    URLSafeSerializer,
    URLSafeTimedSerializer,
)


@pytest.mark.parametrize(
    "factory",
    [
        Signer,
        Serializer,
        TimestampSigner,
        TimedSerializer,
        URLSafeSerializer,
        URLSafeTimedSerializer,
    ],
)
@pytest.mark.parametrize("empty_keys", [[], (), iter([])])
def test_empty_secret_keys_rejected(factory, empty_keys):
    with pytest.raises(ValueError, match="secret_key|secret key"):
        factory(empty_keys)


def test_single_string_and_bytes_keys_preserved():
    signer_str = Signer("secret-key")
    signed_str = signer_str.sign("payload")
    assert signer_str.unsign(signed_str) == b"payload"

    signer_bytes = Signer(b"secret-key")
    signed_bytes = signer_bytes.sign("payload")
    assert signer_bytes.unsign(signed_bytes) == b"payload"


def test_multi_key_rotation_preserved():
    old_signer = Signer("old-key")
    rotated_signer = Signer(["old-key", "new-key"])

    # Signed with old key must still unsign with rotated signer
    old_signed = old_signer.sign("message")
    assert rotated_signer.unsign(old_signed) == b"message"

    # New signatures use newest key
    new_signed = rotated_signer.sign("message")
    assert rotated_signer.unsign(new_signed) == b"message"
    assert rotated_signer.secret_key == b"new-key"


def test_serializers_positive_control():
    ser = Serializer("secret-key")
    data = {"user_id": 42, "role": "admin"}
    assert ser.loads(ser.dumps(data)) == data

    ts_ser = TimedSerializer("secret-key")
    assert ts_ser.loads(ts_ser.dumps(data)) == data

    url_ser = URLSafeSerializer("secret-key")
    assert url_ser.loads(url_ser.dumps(data)) == data

    url_ts_ser = URLSafeTimedSerializer("secret-key")
    assert url_ts_ser.loads(url_ts_ser.dumps(data)) == data
