# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Renesas SLG46824 (GreenPAK) NVM programming over I2C.

Register and NVM blocks, erase and protection registers from the SLG46824 data sheet
(I2C serial communications, NVM erase register ERSR 0xE3, NVM protection NPR 0xE1,
PRL lock bit 1824). The image is the "Export NVM" text of GreenPAK Designer:
a header line, then 2048 lines "index value"; bit i is byte i // 8, bit i % 8.
"""
import time

REG, NVM = 0, 2          # block address in the low 3 bits of the I2C address
ERSR, NPR = 0xE3, 0xE1   # erase register, NVM protection register (PRL = 0xE4 bit 0)
SRST = (0xC8, 0x02)      # register bit 1601: soft reset, reloads the NVM into the registers
T_WR = 0.025             # tWR / tER 20 ms max, plus margin
CTRL_CODES = (0x1, 0x0)  # a programmed part answers at its control code (0001 = 0x08); blank = 0000


class SlgError(Exception):
    pass


def load_image(text):
    bits = [int(l.split()[1]) for l in text.splitlines()[1:] if l.strip()]
    if len(bits) != 2048:
        raise SlgError(f"image has {len(bits)} bits, expected 2048")
    return bytes(sum(bits[8 * i + k] << k for k in range(8)) for i in range(256))


class Part:
    """xfer(addr, wr: bytes, rlen) -> bytes; raises nack_exc on a NACK."""

    def __init__(self, xfer, nack_exc, ctrl_codes=CTRL_CODES):
        self.xfer, self.ctrl = xfer, None
        for c in ctrl_codes:
            try:
                self.xfer(c << 3 | REG, b"\0", 1)
                self.ctrl = c
                break
            except nack_exc:
                pass
        if self.ctrl is None:
            raise SlgError("GreenPAK not found at 0x08 or 0x00")

    def addr(self, block):
        return self.ctrl << 3 | block

    def read(self, block, start, n, step=16):
        out = b""
        while len(out) < n:
            k = min(step, n - len(out))
            out += bytes(self.xfer(self.addr(block), bytes([start + len(out)]), k))
        return out

    def write(self, block, start, data):
        self.xfer(self.addr(block), bytes([start, *data]), 0)
        time.sleep(T_WR)


def program(part, img, verify_only=False):
    """Erase only the pages that need a 1-to-0 change, write 16-byte pages, verify. Returns differing pages."""
    prot = part.read(REG, NPR, 4)
    if prot[0] or prot[3] & 1:
        raise SlgError(f"NVM protected (NPR 0x{prot[0]:02X}, 0xE4 0x{prot[3]:02X}): use GreenPAK Designer")
    cur = part.read(NVM, 0, 256)
    todo = [p for p in range(16) if cur[16 * p:16 * p + 16] != img[16 * p:16 * p + 16]]
    if verify_only or not todo:
        return todo
    if 15 in todo and any(cur[240:256]):
        # ponytail: page 15 holds vendor service bytes; a non-blank part is re-programmed in GreenPAK Designer
        raise SlgError("page 15 differs on a non-blank part: re-program with GreenPAK Designer")
    for p in todo:
        old, new = cur[16 * p:16 * p + 16], img[16 * p:16 * p + 16]
        if any(o & ~n for o, n in zip(old, new)):  # a 1 that must become 0 needs a page erase
            part.write(REG, ERSR, [0x80 | p])
        part.write(NVM, 16 * p, new)
    left = [p for p in range(16) if part.read(NVM, 16 * p, 16) != img[16 * p:16 * p + 16]]
    if left:
        raise SlgError(f"verify failed on pages {left}")
    return []


def soft_reset(part, nack_exc):
    v = part.read(REG, SRST[0], 1)[0]
    try:
        part.write(REG, SRST[0], [v | SRST[1]])
    except nack_exc:
        pass  # the part may NACK while it reloads
