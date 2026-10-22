# -*- coding: utf-8 -*-
"""ULID 生成器（Crockford Base32，26 字符）。

不引第三方依赖：128 位 = 48 位毫秒时间戳 + 80 位随机数。
同一毫秒内靠随机位保证唯一；作为稳定资产 ID，改名不改 id。
"""
from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def ulid() -> str:
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, 10) + _encode(rand, 16)
