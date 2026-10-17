# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Byte layouts against the README "Vendor commands" tables and the v3 spec."""
import struct

import pytest

from alp_probe import protocol as P


def test_info_v3_features():
    d = P.dec_info(bytes([0x80, 3, 0x3F, 14, 59]))
    assert d == dict(version=3, i2c=True, gpio=True, stream=True, i2c1=True, adc=True, board=True, npins=14, i2c_max=59)
    assert P.dec_info(bytes([0x80, 2, 7, 2, 59]))["board"] is False


def test_i2c_xfer():
    assert P.enc_i2c(0x40, b"\x01", 2, True) == bytes([0x81, 0x40, 1, 1, 2, 0x01])
    assert P.enc_i2c(0x40, b"", 2) == bytes([0x81, 0x40, 0, 0, 2])
    assert P.enc_i2c(0x50, b"\x00", 1, True, bus=1) == bytes([0x81, 0x50, 3, 1, 1, 0])  # flags bit1 = bus 1
    assert P.dec_i2c(bytes([0x81, 0, 0xAB, 0xCD])) == (0, b"\xab\xcd")
    assert P.dec_i2c(bytes([0x81, 1])) == (1, b"")


def test_i2c_config_bus_bit31():
    assert P.enc_i2c_config(400000) == bytes([0x82, 0x80, 0x1A, 0x06, 0x00])
    assert P.enc_i2c_config(400000, bus=1) == bytes([0x82, 0x80, 0x1A, 0x06, 0x80])
    assert P.dec_i2c_config(bytes([0x82, 0x80, 0x1A, 0x06, 0x00])) == 400000


def test_pins():
    assert P.enc_pin_set(1, 2) == bytes([0x83, 1, 2])
    assert P.dec_pin_set(bytes([0x83, 0, 1])) == (0, 1)
    assert P.enc_pin_get(1) == bytes([0x84, 1])
    assert P.dec_pin_get(bytes([0x84, 0, 1, 2])) == (0, 1, 2)
    assert P.dec_pin_info(bytes([0x85, 0, 1, 6]) + b"DEMO_A") == (0, 1, "DEMO_A")
    assert P.dec_pin_info(bytes([0x85, 1, 0, 0])) == (1, 0, "")
    assert P.PIN_MODES.index("pulldown") == 4


def test_stream():
    assert P.enc_stream_config(1000, 0xFF, [(0x4A, 0x04, 2), (0x4A, 0x01, 2)]) == bytes(
        [0x86, 0xE8, 0x03, 0, 0, 0xFF, 2, 0x4A, 4, 2, 0x4A, 1, 2])
    assert (P.enc_stream_start(), P.enc_stream_stop(), P.enc_stream_read()) == (b"\x87", b"\x88", b"\x89")
    r = bytes([0x89, 0, 2, 7, 5, 0, 0, 0]) + bytes([0x10, 0x27, 0, 0, 9, 0xAB, 0xCD]) + \
        bytes([0x20, 0x4E, 0, 0, 6, 0x00, 0x00])
    assert P.dec_stream_read(r) == (0, 7, 5, [(10000, 9, b"\xab\xcd"), (20000, 6, b"\x00\x00")])
    assert P.dec_stream_read(bytes([0x89, 6, 0, 0, 0, 0, 0, 0])) == (6, 0, 0, [])
    assert P.unwrap_ts([(0xFFFFFFF0, 0, b""), (0x10, 0, b"")])[1][0] == 0x100000010
    assert P.unwrap_ts([(5, 0, b""), (3, 0, b"")])[1][0] == 3


def test_adc_and_board():
    assert P.enc_adc_read(3) == bytes([0x8A, 3])
    assert P.dec_adc_read(bytes([0x8A, 0]) + struct.pack("<HH", 2048, 3300)) == (0, 2048, 3300)
    assert P.dec_adc_read(bytes([0x8A, 1, 0, 0, 0, 0])) == (1, 0, 0)
    assert P.enc_board() == b"\x8b"
    assert P.dec_board(bytes([0x8B, 7]) + b"generic") == "generic"


def test_unsupported_command():
    with pytest.raises(P.ProtocolError, match="not supported"):
        P.dec_board(b"\xff")


def test_dap_standard():
    assert P.enc_dap_info(2) == bytes([0x00, 0x02])
    assert P.dec_dap_info(bytes([0x00, 4]) + b"abc\0") == "abc"
    assert P.enc_swj_pins(0x00, 0x80) == bytes([0x10, 0x00, 0x80, 0, 0, 0, 0])
    assert P.dec_swj_pins(bytes([0x10, 0x80])) == 0x80
