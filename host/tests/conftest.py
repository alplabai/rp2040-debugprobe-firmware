# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""A fake probe speaking the v3 vendor protocol (no hardware)."""
import struct

import pytest

EVK_PINS = ["BOOT0", "BOOT1", "BOOT2", "BOOT3", "MCU_RST_RP", "I2C0_LINK_N", "MODULE_EN_RP", "MODULE_STBY_RP",
            "RTC_WAKE_N", "CURR_SEL", "CURR_STATUS", "CURR_ALERT_N", "PD_ACTIVE_N", "PWR_FLT_N", "SWD_SEL", "MARKER",
            "WIFI_LOGGER"]
INPUTS = {"RTC_WAKE_N", "CURR_STATUS", "CURR_ALERT_N", "PD_ACTIVE_N", "PWR_FLT_N", "MARKER", "WIFI_LOGGER"}
PULLUPS = {"I2C0_LINK_N"}  # released level 1 (R304 10k pull-up)
EEPROM_ADDR, EEPROM_SIZE, EEPROM_PAGE = 0x50, 16384, 64
CC35_IDCODE = 0x6BA02477  # arbitrary test values here and in cc35_ap, not read from silicon


class FakeEeprom:
    """N24S128: 2-byte address, page writes wrap in 64 bytes, NACKs `busy_polls` accesses after a write."""
    def __init__(self):
        self.mem, self.ptr, self.busy, self.busy_polls = bytearray(b"\xff" * EEPROM_SIZE), 0, 0, 2

    def xfer(self, w, rlen):
        if self.busy:
            self.busy -= 1
            return None
        if len(w) >= 2:
            self.ptr = (w[0] << 8 | w[1]) & (EEPROM_SIZE - 1)
        if len(w) > 2:
            base = self.ptr & ~(EEPROM_PAGE - 1)
            for i, b in enumerate(w[2:]):
                self.mem[base + (self.ptr - base + i) % EEPROM_PAGE] = b
            self.busy = self.busy_polls
        out = bytes(self.mem[(self.ptr + i) % EEPROM_SIZE] for i in range(rlen))
        self.ptr = (self.ptr + rlen) % EEPROM_SIZE
        return out


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
        self.pins["I2C0_LINK_N"][1] = 1
        self.eeprom = FakeEeprom()
        self.ee_seen = []  # (MCU_RST_RP mode, I2C0_LINK_N mode) at each EEPROM access
        self.cc35_powered, self.cc35_fault = True, None  # fault: AP register that answers FAULT
        self.cc35_ack_delay = 0  # CTRL/STAT reads that still show no power-up ACK
        self.dp_writes = []
        self.cc35_ap = {0x00: 0x0BB7702F, 0x04: 0, 0x08: 0x40000098, 0x10: 0x2A5, 0x14: 0x3, 0x28: 0x11223344,
                        0x2C: 0x55667788, 0xFC: 0x102E0001}
        self.dp = {}
        self.ap_writes = []

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
            return bytes([0x85, 0, 2 if EVK_PINS[req[1]] in INPUTS else 0, len(n)]) + n
        if cmd == 0x83:
            name, mode = EVK_PINS[req[1]], req[2]
            if name in INPUTS and mode in (1, 2):
                return bytes([0x83, 2, 0])
            if name == "CURR_SEL" and mode == 2 and not self.high_current:
                self.pins["CURR_STATUS"][1] = 0
            p = self.pins[name]
            p[0] = mode
            if mode in (1, 2):
                p[1] = mode - 1
            elif mode == 0 and name not in INPUTS:
                p[1] = int(name in PULLUPS)
            return bytes([0x83, 0, p[1]])
        if cmd == 0x84:
            name = EVK_PINS[req[1]]
            mode, lvl = self.pins[name]
            if name == "WIFI_LOGGER":  # LOGGER only reaches GPIO18 while SWD_SEL is high
                lvl = int(self.pins["SWD_SEL"][1] == 1 and self.cc35_powered)
            return bytes([0x84, 0, lvl, mode])
        if cmd == 0x81:
            addr, flags, wlen, rlen = req[1:5]
            w, key = req[5:5 + wlen], (flags >> 1, addr)
            if key == (0, EEPROM_ADDR) and self.pins["I2C0_LINK_N"][1] == 0:
                self.ee_seen.append((self.pins["MCU_RST_RP"][0], self.pins["I2C0_LINK_N"][0]))
                data = self.eeprom.xfer(w, rlen)
                return bytes([0x81, 1]) if data is None else bytes([0x81, 0]) + data
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
        if cmd == 0x02:
            return bytes([0x02, req[1] if req[1] == 1 else 0])
        if cmd in (0x03, 0x04, 0x11, 0x12):
            return bytes([cmd, 0])
        if cmd == 0x05:
            return self.transfer(req)
        return b"\xff"

    def transfer(self, req):
        """DAP_Transfer on SW-DP + Config-AP; posted AP reads are hidden by the probe, as in DAP.c."""
        n, i, done, out = req[2], 3, 0, b""
        target = self.pins["SWD_SEL"][1] == 1 and self.cc35_powered
        for _ in range(n):
            r = req[i]
            i += 1
            val = None
            if not r & 2:
                val = struct.unpack("<I", req[i:i + 4])[0]
                i += 4
            if not target:
                return bytes([0x05, done, 7]) + out  # no ACK from the wire
            a = r & 0xC
            sel = self.dp.get(8, 0)
            if r & 1:
                reg = (sel & 0xF0) | a
                if val is not None:
                    self.ap_writes.append((reg, val))
                elif reg == self.cc35_fault:
                    return bytes([0x05, done, 4]) + out
                else:
                    out += struct.pack("<I", self.cc35_ap.get(reg, 0) if sel >> 24 == 0 else 0)
            elif val is not None:
                self.dp[a] = val
                self.dp_writes.append((a, val))
            else:
                ack = 0
                if a == 4 and self.cc35_ack_delay:
                    self.cc35_ack_delay -= 1
                elif a == 4:
                    ack = self.dp.get(4, 0) << 1 & 0xA0000000
                v = CC35_IDCODE if a == 0 else (self.dp.get(4, 0) | ack if a == 4 else 0)
                out += struct.pack("<I", v)
            done += 1
        return bytes([0x05, done, 1]) + out


@pytest.fixture
def fake():
    return FakeProbe()
