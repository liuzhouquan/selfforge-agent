"""Independent checks for max_age validation in itsdangerous."""

import pytest
from itsdangerous.timed import TimedSerializer, TimestampSigner


@pytest.fixture()
def signer() -> TimestampSigner:
    return TimestampSigner("secret-key")


@pytest.fixture()
def signed(signer: TimestampSigner) -> bytes:
    return signer.sign(b"test-payload")


@pytest.fixture()
def serializer() -> TimedSerializer:
    return TimedSerializer("secret-key")


@pytest.fixture()
def dumped(serializer: TimedSerializer) -> str:
    return serializer.dumps({"hello": "world"})


@pytest.mark.parametrize("max_age", [-1, -2, -10])
def test_unsign_negative_max_age_rejected(
    signer: TimestampSigner, signed: bytes, max_age: int
) -> None:
    with pytest.raises(ValueError, match="max_age must be >= 0"):
        signer.unsign(signed, max_age=max_age)


@pytest.mark.parametrize("max_age", [-1, -2, -10])
def test_unsign_invalid_token_negative_max_age_rejected(
    signer: TimestampSigner, max_age: int
) -> None:
    with pytest.raises(ValueError, match="max_age must be >= 0"):
        signer.unsign(b"invalid.token", max_age=max_age)


@pytest.mark.parametrize("max_age", [-1, -2, -10])
def test_validate_negative_max_age_rejected(
    signer: TimestampSigner, signed: bytes, max_age: int
) -> None:
    with pytest.raises(ValueError, match="max_age must be >= 0"):
        signer.validate(signed, max_age=max_age)


@pytest.mark.parametrize("max_age", [-1, -2, -10])
def test_loads_negative_max_age_rejected(
    serializer: TimedSerializer, dumped: str, max_age: int
) -> None:
    with pytest.raises(ValueError, match="max_age must be >= 0"):
        serializer.loads(dumped, max_age=max_age)


def test_positive_and_none_max_age_preserved(
    signer: TimestampSigner,
    signed: bytes,
    serializer: TimedSerializer,
    dumped: str,
) -> None:
    assert signer.unsign(signed, max_age=100) == b"test-payload"
    assert signer.unsign(signed, max_age=None) == b"test-payload"
    assert signer.validate(signed, max_age=100) is True
    assert serializer.loads(dumped, max_age=100) == {"hello": "world"}
    assert serializer.loads(dumped, max_age=None) == {"hello": "world"}
