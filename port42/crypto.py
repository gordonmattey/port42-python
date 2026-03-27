import base64
import json
import os


def decrypt(blob: str, key_b64: str) -> dict | None:
    """Decrypt a Port42 AES-256-GCM payload blob.
    Format: nonce(12) + ciphertext + tag(16), base64-encoded.
    Returns the decrypted SyncPayload dict, or None on failure.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = base64.b64decode(key_b64)
        combined = base64.b64decode(blob)
        nonce = combined[:12]
        ciphertext_tag = combined[12:]
        plaintext = AESGCM(key).decrypt(nonce, ciphertext_tag, None)
        return json.loads(plaintext)
    except Exception:
        return None


def encrypt(payload: dict, key_b64: str) -> str:
    """Encrypt a SyncPayload dict using AES-256-GCM.
    Returns the base64-encoded combined blob (nonce + ciphertext + tag).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    plaintext = json.dumps(payload).encode()
    ciphertext_tag = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext_tag).decode()
