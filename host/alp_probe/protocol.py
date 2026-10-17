# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Pure encoders/decoders for the Alp vendor commands (protocol v3) and the
few standard CMSIS-DAP commands the tool uses. No I/O here."""
import struct

INFO, I2C_XFER, I2C_CONFIG, PIN_SET, PIN_GET, PIN_INFO = 0x80, 0x81, 0x82, 0x83, 0x84, 0x85
STREAM_CONFIG, STREAM_START, STREAM_STOP, STREAM_READ, ADC_READ, BOARD = 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B
DAP_INFO, DAP_SWJ_PINS = 0x00, 0x10
DAP_ID_PRODUCT, DAP_ID_SER_NUM, DAP_ID_FW_VER = 2, 3, 4
DAP_INVALID = 0xFF
NRESET_BIT = 0x80  # DAP_SWJ_nRESET = bit 7
MARKER_NONE = 0xFF

PIN_MODES = ["release", "low", "high", "pullup", "pulldown"]
I2C_STATUS = ["ok", "nack", "timeout", "bad length/address/flags", "i2c not available",
              "busy (stream running)", "stream not configured"]
PIN_STATUS = ["ok", "bad index", "bad mode (or drive on an input-only pin)"]
ADC_STATUS = ["ok", "channel not available"]

# stream record flags
F_MARKER, F_I2C_ERR, F_DROPPED, F_AUX = 1, 2, 4, 8


class ProtocolError(Exception):
    pass


def status_name(table, st):
    return table[st] if st < len(table) else f"status {st}"


def _check(r, cmd):
    if not r:
        raise ProtocolError(f"empty response to 0x{cmd:02X}")
    if r[0] == DAP_INVALID and cmd != DAP_INVALID:
        raise ProtocolError(f"command 0x{cmd:02X} not supported by this probe firmware")
    if r[0] != cmd:
        raise ProtocolError(f"response id 0x{r[0]:02X} for request 0x{cmd:02X}")


def enc_info():
    return bytes([INFO])


def dec_info(r):
    _check(r, INFO)
    f = r[2]
    return dict(version=r[1], i2c=bool(f & 1), gpio=bool(f & 2), stream=bool(f & 4), i2c1=bool(f & 8),
                adc=bool(f & 16), board=bool(f & 32), npins=r[3], i2c_max=r[4])


def enc_i2c(addr, wdata=b"", rlen=0, restart=False, bus=0):
    return bytes([I2C_XFER, addr, (1 if restart else 0) | (bus << 1), len(wdata), rlen]) + bytes(wdata)


def dec_i2c(r):
    """-> (status, data)"""
    _check(r, I2C_XFER)
    return r[1], bytes(r[2:])


def enc_i2c_config(hz, bus=0):
    return bytes([I2C_CONFIG]) + struct.pack("<I", (hz & 0x7FFFFFFF) | (bus << 31))


def dec_i2c_config(r):
    _check(r, I2C_CONFIG)
    return struct.unpack("<I", bytes(r[1:5]))[0]


def enc_pin_set(idx, mode):
    return bytes([PIN_SET, idx, mode])


def dec_pin_set(r):
    """-> (status, level)"""
    _check(r, PIN_SET)
    return r[1], r[2]


def enc_pin_get(idx):
    return bytes([PIN_GET, idx])


def dec_pin_get(r):
    """-> (status, level, mode)"""
    _check(r, PIN_GET)
    return r[1], r[2], r[3]


def enc_pin_info(idx):
    return bytes([PIN_INFO, idx])


def dec_pin_info(r):
    """-> (status, flags, name)"""
    _check(r, PIN_INFO)
    return r[1], r[2], bytes(r[4:4 + r[3]]).decode("ascii")


def enc_stream_config(period_us, marker, chans):
    """chans: list of (addr7, reg, len); marker = pin index or MARKER_NONE."""
    out = bytes([STREAM_CONFIG]) + struct.pack("<IBB", period_us, marker, len(chans))
    for a, reg, n in chans:
        out += bytes([a, reg, n])
    return out


def enc_stream_start():
    return bytes([STREAM_START])


def enc_stream_stop():
    return bytes([STREAM_STOP])


def enc_stream_read():
    return bytes([STREAM_READ])


def dec_status(r, cmd):
    _check(r, cmd)
    return r[1]


def dec_stream_read(r):
    """-> (status, recsize, dropped, [(ts_us, flags, data)])"""
    _check(r, STREAM_READ)
    st, n, size = r[1], r[2], r[3]
    dropped = struct.unpack("<I", bytes(r[4:8]))[0]
    recs = []
    for i in range(n):
        b = bytes(r[8 + i * size:8 + (i + 1) * size])
        recs.append((struct.unpack("<I", b[:4])[0], b[4], b[5:]))
    return st, size, dropped, recs


def unwrap_ts(rows):
    """Extend the u32 microsecond timestamps to 64 bit (running offset)."""
    out, off, prev = [], 0, None
    for ts, flags, data in rows:
        if prev is not None and ts < prev and prev - ts > 1 << 31:
            off += 1 << 32
        prev = ts
        out.append((ts + off, flags, data))
    return out


def enc_adc_read(chan):
    return bytes([ADC_READ, chan])


def dec_adc_read(r):
    """-> (status, raw, vref_mv)"""
    _check(r, ADC_READ)
    raw, vref = struct.unpack("<HH", bytes(r[2:6]))
    return r[1], raw, vref


def enc_board():
    return bytes([BOARD])


def dec_board(r):
    _check(r, BOARD)
    return bytes(r[2:2 + r[1]]).decode("ascii")


def enc_dap_info(info_id):
    return bytes([DAP_INFO, info_id])


def dec_dap_info(r):
    """String DAP_Info reply: len u8 then the string (len counts a trailing NUL)."""
    _check(r, DAP_INFO)
    return bytes(r[2:2 + r[1]]).split(b"\0")[0].decode("ascii", "replace")


def enc_swj_pins(output, select, wait_us=0):
    return bytes([DAP_SWJ_PINS, output, select]) + struct.pack("<I", wait_us)


def dec_swj_pins(r):
    """-> pin input byte (bit7 = nRESET level)."""
    _check(r, DAP_SWJ_PINS)
    return r[1]
