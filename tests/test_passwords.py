from pvscaler.passwords import hash_password, verify_password


def test_hash_roundtrip():
    h = hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret!", h)
    assert not verify_password("wrong", h)


def test_plain_password():
    assert verify_password("plainpw", "plainpw")
    assert not verify_password("plainpw", "other")
