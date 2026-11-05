"""落盘加密：AES-256-GCM。密钥 DATA_KEY 只在部署机 .env，不进代码库、不下发前端。"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(data_key: str) -> bytes:
    """DATA_KEY 原文派生为 32 字节 AES 密钥。"""
    return hashlib.sha256(data_key.encode("utf-8")).digest()


def encrypt(plaintext: str, data_key: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(data_key)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str, data_key: str) -> str:
    raw = base64.b64decode(token.encode("ascii"))
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(_key(data_key)).decrypt(nonce, ciphertext, None).decode("utf-8")
