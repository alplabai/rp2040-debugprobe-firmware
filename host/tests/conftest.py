# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""A fake probe speaking the v3 vendor protocol (no hardware)."""
import struct

import pytest

EVK_PINS = ["BOOT0", "BOOT1", "BOOT2", "BOOT3", "MCU_RST_RP", "MCU_RST_SENSE", "MODULE_EN_RP", "MODULE_STBY_RP",
            "RTC_WAKE_N", "CURR_SEL", "CURR_STATUS", "CURR_ALERT_N", "PD_ACTIVE_N", "PWR_FLT_N"]


class FakeProbe:
    def __init__(self, board="alp-e1m-evk-2641-r3", version=3):
        self.board, self.version = board, version
        self.pins = {n: [0, 0] for n in EVK_PINS}  # name -> [mode, level]
        self.pins["CURR_STATUS"][1] = 1  # latch powers up in mA range
        self.high_current = False
        self.regs = {}   # (bus, addr) -> {reg: bytes}
        self.ptr = {}
        self.adc = {3: 2048}
        self.records = []  # (ts, flags, data)
        self.log = []

    def xfer(self, req):
        req = bytes(req)
        self.log.append(req)
        cmd = req[0]
        if cmd == 0x00:
            s = {2: b"Alp Lab E1M EVK Probe (CMSIS-DAP)", 3: b"E66138935F4A2B2C", 4: b"2.2.3"}[req[1]] + b"\0"
            return bytes([0, len(s)]) + s
        if cmd == 0x10:
            self.nreset = bool(req[1] & 0x80)
            return bytes([0x10, req[1] & 0x80])
        if cmd == 0x80:
            return bytes([0x80, self.version, 0x3F, len(EVK_PINS), 59])
        if cmd == 0x8B:
            return bytes([0x8B, len(self.board)]) + self.board.encode()
        if cmd == 0x85:
            n = EVK_PINS[req[1]].encode()
            return bytes([0x85, 0, 0, len(n)]) + n
        if cmd == 0x83:
            name, mode = EVK_PINS[req[1]], req[2]
            if name == "CURR_SEL" and mode == 2 and not self.high_current:
                self.pins["CURR_STATUS"][1] = 0
            p = self.pins[name]
            p[0] = mode
            if mode in (1, 2):
                p[1] = mode - 1
            return bytes([0x83, 0, p[1]])
        if cmd == 0x84:
            mode, lvl = self.pins[EVK_PINS[req[1]]]
            return bytes([0x84, 0, lvl, mode])
        if cmd == 0x81:
            addr, flags, wlen, rlen = req[1:5]
            w, key = req[5:5 + wlen], (flags >> 1, addr)
            if key not in self.regs:
                return bytes([0x81, 1])
            if wlen == 1:
                self.ptr[key] = w[0]
            elif wlen > 1:
                self.regs[key][w[0]] = w[1:]
            data = self.regs[key].get(self.ptr.get(key), b"\0" * rlen)[:rlen] if rlen else b""
            return bytes([0x81, 0]) + data
        if cmd == 0x82:
            return b"\x82" + struct.pack("<I", struct.unpack("<I", req[1:5])[0] & 0x7FFFFFFF)
        if cmd == 0x8A:
            if req[1] not in self.adc:
                return bytes([0x8A, 1, 0, 0, 0, 0])
            return bytes([0x8A, 0]) + struct.pack("<HH", self.adc[req[1]], 3300)
        if cmd in (0x86, 0x87, 0x88):
            return bytes([cmd, 0])
        if cmd == 0x89:
            size = 5 + 9
            take, self.records = self.records[:56 // size], self.records[56 // size:]
            out = bytes([0x89, 0, len(take), size]) + struct.pack("<I", 0)
            for ts, fl, d in take:
                out += struct.pack("<IB", ts, fl) + d
            return out
        return b"\xff"


@pytest.fixture
def fake():
    return FakeProbe()
