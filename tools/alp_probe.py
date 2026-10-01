#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Host example for the Alp vendor commands (README "Vendor commands").

  alp_probe.py info
  alp_probe.py i2c ADDR [--write HEX] [--read N]
  alp_probe.py pin list | set NAME MODE | get NAME
  alp_probe.py stream --period-us P --chan ADDR:REG:LEN ... [--marker NAME]
                      --seconds S [--out FILE.csv|FILE.jsonl]
  alp_probe.py --selftest        (no hardware, no pyusb needed)
"""
import argparse
import json
import struct
import sys
import time

MODES = ["release", "low", "high", "pullup", "pulldown"]
I2C_ST = ["ok", "nack", "timeout", "bad length", "i2c not available", "busy (stream running)", "stream not configured"]


# --- encoders / decoders (pure, tested by --selftest) -----------------------
def enc_info():
    return bytes([0x80])


def dec_info(r):
    assert r[0] == 0x80, r
    return dict(version=r[1], i2c=bool(r[2] & 1), gpio=bool(r[2] & 2), stream=bool(r[2] & 4), npins=r[3], i2c_max=r[4])


def enc_i2c(addr, wdata=b"", rlen=0, restart=False):
    return bytes([0x81, addr, 1 if restart else 0, len(wdata), rlen]) + wdata


def dec_i2c(r):
    assert r[0] == 0x81, r
    return r[1], bytes(r[2:])


def enc_i2c_config(hz):
    return b"\x82" + struct.pack("<I", hz)


def dec_i2c_config(r):
    assert r[0] == 0x82, r
    return struct.unpack("<I", bytes(r[1:5]))[0]


def enc_pin_set(idx, mode):
    return bytes([0x83, idx, mode])


def dec_pin_set(r):
    assert r[0] == 0x83, r
    return r[1], r[2]  # status, level


def enc_pin_get(idx):
    return bytes([0x84, idx])


def dec_pin_get(r):
    assert r[0] == 0x84, r
    return r[1], r[2], r[3]  # status, level, mode


def enc_pin_info(idx):
    return bytes([0x85, idx])


def dec_pin_info(r):
    assert r[0] == 0x85, r
    return r[1], r[2], bytes(r[4:4 + r[3]]).decode("ascii")  # status, flags, name


def enc_stream_config(period_us, marker, chans):
    """chans: list of (addr7, reg, len); marker 0xFF = none."""
    out = b"\x86" + struct.pack("<IBB", period_us, marker, len(chans))
    for a, r, n in chans:
        out += bytes([a, r, n])
    return out


def dec_status(r, cmd):
    assert r[0] == cmd, r
    return r[1]


def enc_stream_start():
    return b"\x87"


def enc_stream_stop():
    return b"\x88"


def enc_stream_read():
    return b"\x89"


def dec_stream_read(r):
    """-> (status, recsize, dropped, [(ts_us, flags, data)])"""
    assert r[0] == 0x89, r
    st, n, size, dropped = r[1], r[2], r[3], struct.unpack("<I", bytes(r[4:8]))[0]
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


def selftest():
    assert enc_info() == b"\x80"
    assert dec_info(bytes([0x80, 1, 3, 2, 59])) == dict(version=1, i2c=True, gpio=True, stream=False, npins=2, i2c_max=59)
    assert enc_i2c(0x40, bytes.fromhex("01"), 2, True) == bytes([0x81, 0x40, 1, 1, 2, 0x01])
    assert enc_i2c(0x40, b"", 2) == bytes([0x81, 0x40, 0, 0, 2])
    assert dec_i2c(bytes([0x81, 0, 0xAB, 0xCD])) == (0, b"\xab\xcd")
    assert dec_i2c(bytes([0x81, 1])) == (1, b"")
    assert enc_i2c_config(400000) == bytes([0x82, 0x80, 0x1A, 0x06, 0x00])
    assert dec_i2c_config(bytes([0x82, 0x80, 0x1A, 0x06, 0x00])) == 400000
    assert enc_pin_set(1, 2) == bytes([0x83, 1, 2])
    assert dec_pin_set(bytes([0x83, 0, 1])) == (0, 1)
    assert enc_pin_get(1) == bytes([0x84, 1])
    assert dec_pin_get(bytes([0x84, 0, 1, 2])) == (0, 1, 2)
    assert enc_pin_info(0) == bytes([0x85, 0])
    assert dec_pin_info(bytes([0x85, 0, 1, 6]) + b"DEMO_A") == (0, 1, "DEMO_A")
    assert dec_pin_info(bytes([0x85, 1, 0, 0])) == (1, 0, "")
    assert dec_info(bytes([0x80, 2, 7, 2, 59]))["stream"] is True
    assert enc_stream_config(1000, 0xFF, [(0x4A, 0x04, 2), (0x4A, 0x01, 2)]) == bytes(
        [0x86, 0xE8, 0x03, 0, 0, 0xFF, 2, 0x4A, 4, 2, 0x4A, 1, 2])
    assert enc_stream_config(200, 1, [(0x40, 0, 4)]) == bytes([0x86, 200, 0, 0, 0, 1, 1, 0x40, 0, 4])
    assert (enc_stream_start(), enc_stream_stop(), enc_stream_read()) == (b"\x87", b"\x88", b"\x89")
    assert dec_status(bytes([0x86, 3]), 0x86) == 3
    r = bytes([0x89, 0, 2, 7, 5, 0, 0, 0]) + bytes([0x10, 0x27, 0, 0, 1, 0xAB, 0xCD]) + \
        bytes([0x20, 0x4E, 0, 0, 6, 0x00, 0x00])
    assert dec_stream_read(r) == (0, 7, 5, [(10000, 1, b"\xab\xcd"), (20000, 6, b"\x00\x00")])
    assert dec_stream_read(bytes([0x89, 6, 0, 0, 0, 0, 0, 0])) == (6, 0, 0, [])
    assert unwrap_ts([(0xFFFFFFF0, 0, b""), (0x10, 0, b""), (0x20, 0, b"")]) == [
        (0xFFFFFFF0, 0, b""), (0x100000010, 0, b""), (0x100000020, 0, b"")]
    assert unwrap_ts([(5, 0, b""), (3, 0, b"")])[1][0] == 3  # small backstep is not a wrap
    assert enc_stream_config(10_000_000, 0xFF, [(0x4A, 1, 2)])[1:5] == struct.pack("<I", 10_000_000)
    print("selftest ok")


# --- USB transport (CMSIS-DAP v2 bulk interface) ----------------------------
class Probe:
    def __init__(self):
        import usb.core
        import usb.util
        dev = usb.core.find(idVendor=0x2E8A, idProduct=0x000C)
        if dev is None:
            sys.exit("probe 0x2E8A:0x000c not found")
        for cfg in dev:
            for intf in cfg:
                name = usb.util.get_string(dev, intf.iInterface) or ""
                if "CMSIS-DAP" in name:
                    self.dev, self.intf = dev, intf
                    self.out = usb.util.find_descriptor(
                        intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
                    self.inp = usb.util.find_descriptor(
                        intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
                    usb.util.claim_interface(dev, intf.bInterfaceNumber)
                    return
        sys.exit("no CMSIS-DAP interface on the probe")

    def xfer(self, req):
        self.out.write(req, 1000)
        return bytes(self.inp.read(64, 1000))


def pin_names(p):
    n = dec_info(p.xfer(enc_info()))["npins"]
    out = []
    for i in range(n):
        st, flags, name = dec_pin_info(p.xfer(enc_pin_info(i)))
        out.append((i, name, flags))
    return out


def pin_index(p, name):
    for i, n, _ in pin_names(p):
        if n == name:
            return i
    sys.exit(f"no pin named {name}")


def run_stream(p, a):
    info = dec_info(p.xfer(enc_info()))
    if not info["stream"]:
        sys.exit(f"probe firmware (protocol v{info['version']}) has no stream support; update it")
    chans = []
    for c in a.chan:
        addr, reg, n = (int(x, 0) for x in c.split(":"))
        chans.append((addr, reg, n))
    marker = 0xFF if not a.marker else pin_index(p, a.marker)
    st = dec_status(p.xfer(enc_stream_config(a.period_us, marker, chans)), 0x86)
    if st:
        sys.exit(f"stream config: {I2C_ST[st] if st < len(I2C_ST) else st}")
    st = dec_status(p.xfer(enc_stream_start()), 0x87)
    if st:
        sys.exit(f"stream start: {I2C_ST[st] if st < len(I2C_ST) else st}")
    rows, dropped, end = [], 0, time.monotonic() + a.seconds
    try:
        while time.monotonic() < end:
            st, _, dropped, recs = dec_stream_read(p.xfer(enc_stream_read()))
            rows += recs
    finally:  # never leave the probe sampling (and I2C busy) after a host error
        p.xfer(enc_stream_stop())
    while True:  # drain what the sampler left in the ring
        st, _, dropped, recs = dec_stream_read(p.xfer(enc_stream_read()))
        if not recs:
            break
        rows += recs
    rows = unwrap_ts(rows)
    jsonl = a.out and a.out.endswith(".jsonl")
    f = open(a.out, "w") if a.out else sys.stdout
    if not jsonl:
        f.write("timestamp_us,marker,data_hex,flags\n")
    for ts, flags, data in rows:
        if jsonl:
            f.write(json.dumps(dict(timestamp_us=ts, marker=flags & 1, data=data.hex(), flags=flags)) + "\n")
        else:
            f.write(f"{ts},{flags & 1},{data.hex()},{flags}\n")
    if a.out:
        f.close()
    print(f"{len(rows)} records, {dropped} dropped", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("info")
    i = sub.add_parser("i2c")
    i.add_argument("addr", type=lambda s: int(s, 0))
    i.add_argument("--write", default="")
    i.add_argument("--read", type=int, default=0)
    pn = sub.add_parser("pin")
    ps = pn.add_subparsers(dest="pcmd", required=True)
    ps.add_parser("list")
    s = ps.add_parser("set")
    s.add_argument("name")
    s.add_argument("mode", choices=MODES)
    g = ps.add_parser("get")
    g.add_argument("name")
    sm = sub.add_parser("stream")
    sm.add_argument("--period-us", type=int, required=True)
    sm.add_argument("--chan", action="append", required=True, metavar="ADDR:REG:LEN")
    sm.add_argument("--marker")
    sm.add_argument("--seconds", type=float, required=True)
    sm.add_argument("--out")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.error("command required")
    p = Probe()
    if a.cmd == "info":
        print(dec_info(p.xfer(enc_info())))
    elif a.cmd == "stream":
        run_stream(p, a)
    elif a.cmd == "i2c":
        w = bytes.fromhex(a.write)
        st, data = dec_i2c(p.xfer(enc_i2c(a.addr, w, a.read, restart=bool(w and a.read))))
        print(I2C_ST[st] if st < len(I2C_ST) else st, data.hex())
        sys.exit(st)
    elif a.pcmd == "list":
        for idx, name, flags in pin_names(p):
            st, lvl, mode = dec_pin_get(p.xfer(enc_pin_get(idx)))
            print(f"{idx} {name} level={lvl} mode={MODES[mode]}{' (boot default driven)' if flags & 1 else ''}")
    elif a.pcmd == "set":
        st, lvl = dec_pin_set(p.xfer(enc_pin_set(pin_index(p, a.name), MODES.index(a.mode))))
        print(f"status={st} level={lvl}")
        sys.exit(st)
    else:
        st, lvl, mode = dec_pin_get(p.xfer(enc_pin_get(pin_index(p, a.name))))
        print(f"level={lvl} mode={MODES[mode]}")


if __name__ == "__main__":
    main()
