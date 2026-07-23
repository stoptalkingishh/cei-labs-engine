"""Unit tests for app/crypto.py's CredentialCipher -- the AEAD wrapper
store.py uses to encrypt plan_json (every generated VM/flag credential)
before it reaches SQLite. See app/crypto.py's module docstring."""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.crypto import CredentialCipher


def test_round_trip_returns_the_original_plaintext():
    cipher = CredentialCipher(Fernet.generate_key())
    plaintext = '{"vnc_password": "s3cr3t", "url": "https://team-1.apps.ctf.local"}'

    token = cipher.encrypt(plaintext)

    assert token != plaintext
    assert cipher.decrypt(token) == plaintext


def test_ciphertext_does_not_contain_the_plaintext_secret():
    cipher = CredentialCipher(Fernet.generate_key())
    plaintext = '{"vnc_password": "correct-horse-battery-staple"}'

    token = cipher.encrypt(plaintext)

    assert "correct-horse-battery-staple" not in token


def test_tampering_with_the_token_is_detected_not_silently_decrypted():
    cipher = CredentialCipher(Fernet.generate_key())
    token = cipher.encrypt('{"vnc_password": "s3cr3t"}')

    tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]

    with pytest.raises(InvalidToken):
        cipher.decrypt(tampered)


def test_a_token_encrypted_under_one_key_cannot_be_read_under_another():
    cipher_a = CredentialCipher(Fernet.generate_key())
    cipher_b = CredentialCipher(Fernet.generate_key())
    token = cipher_a.encrypt('{"vnc_password": "s3cr3t"}')

    with pytest.raises(InvalidToken):
        cipher_b.decrypt(token)


def test_from_key_material_with_a_real_key_is_usable_and_deterministic_across_instances():
    key = Fernet.generate_key().decode("utf-8")
    cipher_a = CredentialCipher.from_key_material(key)
    cipher_b = CredentialCipher.from_key_material(key)  # e.g. a second gunicorn worker reading the same secret file

    token = cipher_a.encrypt("hello")
    assert cipher_b.decrypt(token) == "hello"


@pytest.mark.parametrize("bad_material", [None, "", "   ", "not-a-valid-fernet-key", "CHANGE_ME"])
def test_from_key_material_falls_back_to_a_working_ephemeral_key_rather_than_raising(bad_material):
    cipher = CredentialCipher.from_key_material(bad_material)
    token = cipher.encrypt("still works")
    assert cipher.decrypt(token) == "still works"


def test_two_ephemeral_fallback_ciphers_do_not_share_a_key():
    cipher_a = CredentialCipher.from_key_material(None)
    cipher_b = CredentialCipher.from_key_material(None)
    token = cipher_a.encrypt("secret")

    with pytest.raises(InvalidToken):
        cipher_b.decrypt(token)
