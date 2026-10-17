# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""alp-probe command line. Every command prints human-readable text, or exactly
one JSON object on stdout with --json (errors: {"ok": false, "error": ...})."""
import argparse
import contextlib
import json
import struct
import sys
import time

from . import boards, ina
from . import protocol as P
from .transport import PID, VID, Probe, TransportError

RANGE_NOTE = ("returning to the mA range happens only by the hardware trip (R278 drop above about 40 mV, "
              "about 4 mA); there is no software path")
S6_HINT = "NACK from a power monitor: switch S6 may be set to the module instead of the RP2040"
RESET_WARN = "WARNING: driving this line resets the module."
POWER_WARN = "WARNING: these lines reset, power off or put the module into standby."
BUS1_HELP = ("I2C bus: 0 = power monitors (CURR_I2C, needs S6 on the RP2040), 1 = the module board I2C bus "
             "(GreenPAK, RTC, EEPROM), shared with the module SoC: use bus 1 only while the SoC is held in "
             "reset or is idle on that bus")
XDS_WARN = "WARNING: never select cc35 while an XDS110 is connected to P19."
CC35_UNPOWERED = "CC3501E not powered (LOGGER low)"

# onsemi N24S128 (module U18 N24S128C4DYT3G), data sheet N24S128/D Rev. 3 (June 2025)
N24S128_SIZE = 16384    # "Page Write": 16,384 bytes, 256 pages of 64 bytes, two byte address (Table 8)
N24S128_PAGE = 64       # writes wrap within the 64-byte page
N24S128_TWR_S = 0.005   # Table 6, tWR Write Cycle Time max 5 ms; "Acknowledge Polling": NACK while busy


class CliError(Exception):
    def __init__(self, msg, **extra):
        super().__init__(msg)
        self.extra = extra


class I2cError(CliError):
    def __init__(self, st, addr):
        super().__init__(f"I2C 0x{addr:02X}: {P.status_name(P.I2C_STATUS, st)}", status=st)
        self.status = st


class Ctx:
    def __init__(self, serial, open_probe):
        self._serial, self._open, self._probe = serial, open_probe, None
        self._info = self._board = self._pins = None

    def x(self, req):
        if self._probe is None:
            self._probe = self._open(self._serial)
        return self._probe.xfer(req)

    @property
    def info(self):
        if self._info is None:
            self._info = P.dec_info(self.x(P.enc_info()))
        return self._info

    @property
    def board(self):
        if self._board is None:
            self._board = boards.get(P.dec_board(self.x(P.enc_board())) if self.info["board"] else None)
        return self._board

    def need(self, attr, what):
        v = getattr(self.board, attr)
        if v is None:
            raise CliError(f"board {self.board.name} has no {what}")
        return v

    @property
    def pins(self):
        if self._pins is None:
            self._pins = {}
            for i in range(self.info["npins"]):
                st, flags, name = P.dec_pin_info(self.x(P.enc_pin_info(i)))
                if st == 0:
                    self._pins[name] = (i, flags)
        return self._pins

    def pin_index(self, name):
        if name not in self.pins:
            raise CliError(f"no pin named {name} (probe pins: {', '.join(self.pins) or 'none'})")
        return self.pins[name][0]

    def pin_set(self, name, mode):
        st, lvl = P.dec_pin_set(self.x(P.enc_pin_set(self.pin_index(name), P.PIN_MODES.index(mode))))
        if st:
            raise CliError(f"pin {name}: {P.status_name(P.PIN_STATUS, st)}", status=st)
        return lvl

    def pin_get(self, name):
        st, lvl, mode = P.dec_pin_get(self.x(P.enc_pin_get(self.pin_index(name))))
        if st:
            raise CliError(f"pin {name}: {P.status_name(P.PIN_STATUS, st)}", status=st)
        return lvl, P.PIN_MODES[mode] if mode < len(P.PIN_MODES) else str(mode)

    def i2c(self, addr, wdata=b"", rlen=0, restart=False, bus=0):
        st, data = P.dec_i2c(self.x(P.enc_i2c(addr, wdata, rlen, restart, bus)))
        if st:
            raise I2cError(st, addr)
        return data

    def reg_read(self, addr, reg, n):
        return ina.be(self.i2c(addr, bytes([reg]), n, restart=True))

    def reg_write16(self, addr, reg, value):
        self.i2c(addr, bytes([reg, value >> 8, value & 0xFF]))


# --- commands: each returns (json dict, human text) --------------------------
def cmd_info(c, a):
    d = dict(c.info)
    d["board"] = c.board.name
    for key, iid in (("product", P.DAP_ID_PRODUCT), ("serial", P.DAP_ID_SER_NUM), ("firmware", P.DAP_ID_FW_VER)):
        d[key] = P.dec_dap_info(c.x(P.enc_dap_info(iid)))
    feats = [k for k in ("i2c", "gpio", "stream", "i2c1", "adc", "board") if d[k]]
    text = (f"{d['product']} serial {d['serial']} firmware {d['firmware']}\n"
            f"board {d['board']}, protocol v{d['version']}, features {' '.join(feats) or 'none'}, "
            f"{d['npins']} pins, i2c_max {d['i2c_max']}")
    return dict(info=d), text


def cmd_ports(c, a):
    try:
        from serial.tools import list_ports
    except ImportError:
        raise CliError("pyserial is not installed (pip install pyserial)")
    found = {"se_uart": None, "app_console": None}
    for p in list_ports.comports():
        if p.vid != VID or p.pid != PID or (a.serial and p.serial_number != a.serial):
            continue
        iface = p.interface or ""
        if "Alp SE-UART" in iface:
            found["se_uart"] = p.device
        elif "Alp App Console" in iface:
            found["app_console"] = p.device
    text = "\n".join(f"{k}: {v or 'not found'}" for k, v in found.items())
    return dict(ports=found), text


def cmd_pin(c, a):
    if a.pcmd == "list":
        rows = []
        for name, (idx, flags) in c.pins.items():
            lvl, mode = c.pin_get(name)
            rows.append(dict(index=idx, name=name, level=lvl, mode=mode, boot_driven=bool(flags & 1), input_only=bool(flags & 2),
                             meaning=c.board.pins.get(name, "")))
        text = "\n".join(f"{r['index']:2} {r['name']:15} level={r['level']} mode={r['mode']:8}{' in-only' if r['input_only'] else '        '} {r['meaning']}"
                         for r in rows) or "no pins"
        return dict(pins=rows), text
    if a.pcmd == "set":
        lvl = c.pin_set(a.name, a.mode)
        return dict(name=a.name, mode=a.mode, level=lvl), f"{a.name} {a.mode}, level={lvl}"
    lvl, mode = c.pin_get(a.name)
    return dict(name=a.name, level=lvl, mode=mode), f"{a.name} level={lvl} mode={mode}"


def _pulse(do, ms):
    do(True)
    time.sleep(ms / 1000)
    do(False)


def cmd_reset(c, a):
    def do(assert_):
        P.dec_swj_pins(c.x(P.enc_swj_pins(0 if assert_ else P.NRESET_BIT, P.NRESET_BIT)))
    if a.action == "pulse":
        _pulse(do, a.ms)
    else:
        do(a.action == "assert")
    return dict(reset=a.action), f"nRESET {a.action}"


def cmd_por(c, a):
    pin = c.need("por", "POR line")
    def do(assert_):
        c.pin_set(pin, "high" if assert_ else "release")
    if a.action == "pulse":
        _pulse(do, a.ms)
    else:
        do(a.action == "assert")
    return dict(por=a.action, pin=pin), f"POR {a.action} ({pin})"


def cmd_module(c, a):
    attr, mode = {"off": ("module_en", "high"), "on": ("module_en", "release"),
                  "standby": ("module_stby", "high"), "wake": ("module_stby", "release")}[a.action]
    pin = c.need(attr, "module power control")
    lvl = c.pin_set(pin, mode)
    return dict(module=a.action, pin=pin, level=lvl), f"module {a.action} ({pin} {mode})"


def cmd_i2c(c, a):
    w = bytes.fromhex(a.write)
    n = c.info["i2c_max"]
    if len(w) > n or a.read > n:
        raise CliError(f"I2C transfer too long: write {len(w)}, read {a.read}, probe maximum {n} bytes each")
    data = c.i2c(a.addr, w, a.read, a.restart, a.bus)
    return dict(bus=a.bus, addr=a.addr, data=data.hex()), data.hex() or "ok"


def cmd_vio(c, a):
    ch = c.need("vio_adc", "VIO ADC channel")
    st, raw, vref = P.dec_adc_read(c.x(P.enc_adc_read(ch)))
    if st:
        raise CliError(f"ADC{ch}: {P.status_name(P.ADC_STATUS, st)}", status=st)
    v = raw * vref / 1000 / 4096 * c.board.vio_div
    return dict(vio_v=round(v, 4), raw=raw, vref_mv=vref), f"VIO_OUT {v:.3f} V (ADC{ch} raw {raw})"


def _range(c):
    return "mA" if c.pin_get(c.need("range_pin", "current range"))[0] else "uA"


def cmd_range(c, a):
    if a.action == "ua":
        sel = c.need("range_sel", "current range")
        c.pin_set(sel, "high")
        c.pin_set(sel, "release")  # external pull-down: falling gate after the rising CLK
    r = _range(c)
    text = f"{r} range"
    if a.action == "ua" and r == "mA":
        text += " (uA request had no effect: current too high, the trip holds the mA range)"
    return dict(range=r, note=RANGE_NOTE), f"{text}\nnote: {RANGE_NOTE}"


def read_monitor(c, m):
    reg_cfg, reg_vs, reg_vb, n = ina.regs(m.part)
    rng = ina.adcrange(m.part, c.reg_read(m.addr, reg_cfg, 2))
    vs = ina.vshunt(m.part, c.reg_read(m.addr, reg_vs, n), rng)
    vb = ina.vbus(m.part, c.reg_read(m.addr, reg_vb, n))
    i = vs / m.shunt_ohm
    row = dict(ref=m.ref, part=m.part, addr=m.addr, rail=m.rail, vbus_v=vb, vshunt_v=vs, current_a=i, power_w=vb * i)
    if m.part == "INA228":
        row["dietemp_c"] = ina.dietemp_228(c.reg_read(m.addr, ina.INA228_DIETEMP, 2))
    return row


def cmd_power(c, a):
    b = c.board
    if not b.monitors:
        raise CliError(f"board {b.name} has no power monitors")
    rows, nack = [], False
    for m in b.monitors:
        try:
            rows.append(read_monitor(c, m))
        except I2cError as e:
            nack |= e.status == 1
            rows.append(dict(ref=m.ref, part=m.part, addr=m.addr, rail=m.rail, error=str(e)))
    out = dict(board=b.name, monitors=rows)
    lines = []
    for r in rows:
        head = f"{r['ref']} {r['part']} 0x{r['addr']:02X} {r['rail']}: "
        lines.append(head + (r["error"] if "error" in r else
                             f"{r['vbus_v']:.4f} V {r['current_a'] * 1e3:.4f} mA {r['power_w'] * 1e3:.4f} mW"))
    if b.module_ma:
        rng = _range(c)
        sel = next(r for r in rows if r["ref"] == (b.module_ua if rng == "uA" else b.module_ma))
        out["module_5v"] = dict(range=rng, monitor=sel["ref"], **{k: sel[k] for k in ("current_a", "power_w", "error")
                                                                 if k in sel})
        lines.append(f"module 5 V ({rng} range, {sel['ref']}): " + (sel["error"] if "error" in sel else
                     f"{sel['current_a'] * 1e3:.4f} mA {sel['power_w'] * 1e3:.4f} mW"))
    if nack:
        out["hint"] = S6_HINT
        lines.append("hint: " + S6_HINT)
    return out, "\n".join(lines)


# --- stream ------------------------------------------------------------------
def decode_sample(ts, flags, data, ma, ua, rng_ma, rng_ua):
    """Record of the default module 5 V stream (U29 VBUS, U29 VSHUNT, U53 VSHUNT, 3 bytes each)."""
    vb = ina.vbus("INA228", ina.be(data[0:3]))
    if flags & P.F_AUX:  # aux GPIO = CURR_STATUS: 1 = mA range
        rng, i = "mA", ina.vshunt("INA228", ina.be(data[3:6]), rng_ma) / ma.shunt_ohm
    else:
        rng, i = "uA", ina.vshunt("INA228", ina.be(data[6:9]), rng_ua) / ua.shunt_ohm
    return dict(timestamp_us=ts, range=rng, vbus_v=vb, current_a=i, power_w=vb * i,
                marker=flags & P.F_MARKER, flags=flags)


def summarize(samples):
    """Left Riemann sum: each valid sample's power holds until the next valid one."""
    s = [x for x in samples if not x["flags"] & P.F_I2C_ERR]
    if not s:
        return dict(samples=0)
    energy, windows, win = 0.0, [], None
    for cur, nxt in zip(s, s[1:]):
        e = cur["power_w"] * (nxt["timestamp_us"] - cur["timestamp_us"]) / 1e6
        energy += e
        if cur["marker"]:
            if win is None:
                win = dict(start_s=(cur["timestamp_us"] - s[0]["timestamp_us"]) / 1e6, t0=cur["timestamp_us"],
                           energy_j=0.0)
            win["energy_j"] += e
            win["end"] = nxt["timestamp_us"]
        elif win is not None:
            windows.append(win)
            win = None
    if win is not None:
        windows.append(win)
    for w in windows:
        w["duration_s"] = (w.pop("end") - w.pop("t0")) / 1e6
    n = len(s)
    return dict(samples=n, errors=len(samples) - n,
                duration_s=(s[-1]["timestamp_us"] - s[0]["timestamp_us"]) / 1e6,
                mean_current_a=sum(x["current_a"] for x in s) / n, peak_current_a=max(x["current_a"] for x in s),
                mean_power_w=sum(x["power_w"] for x in s) / n, peak_power_w=max(x["power_w"] for x in s),
                energy_j=energy, windows=windows)


def _write_samples(path, samples):
    keys = ("timestamp_us", "range", "vbus_v", "current_a", "power_w", "marker", "flags")
    with open(path, "w") as f:
        if path.endswith(".jsonl"):
            for x in samples:
                f.write(json.dumps({k: x[k] for k in keys}) + "\n")
        else:
            f.write(",".join(keys) + "\n")
            for x in samples:
                f.write(",".join(str(x[k]) for k in keys) + "\n")


def cmd_stream(c, a):
    b = c.board
    if not (b.module_ma and b.module_ua):
        raise CliError(f"board {b.name} has no module 5 V monitors to stream")
    if not c.info["stream"] or c.info["version"] < 3:
        raise CliError(f"probe firmware (protocol v{c.info['version']}) has no v3 stream support; update it")
    ma, ua = b.monitor(b.module_ma), b.monitor(b.module_ua)
    marker, a.marker = P.MARKER_NONE, a.marker or (b.marker if b.marker in c.pins else None)
    if a.marker:
        marker = c.pin_index(a.marker)
        c.pin_set(a.marker, "release")  # the sampler only reads the marker pin
    P.dec_status(c.x(P.enc_stream_stop()), P.STREAM_STOP)  # the bus must be idle for I2C_CONFIG
    hz = P.dec_i2c_config(c.x(P.enc_i2c_config(a.i2c_hz)))
    if not hz:
        raise CliError("I2C bus 0 not available (I2C_CONFIG replied 0)")
    ct, avg = ina.pick_adc_228(a.period_us)
    adc = ina.adc_config_228(ina.INA228_MODE_CONT_SHUNT_BUS, ct, ct, 0, avg)
    for m in (ma, ua):
        c.reg_write16(m.addr, ina.INA228_ADC_CONFIG, adc)
    rng_ma = ina.adcrange("INA228", c.reg_read(ma.addr, ina.INA228_CONFIG, 2))
    rng_ua = ina.adcrange("INA228", c.reg_read(ua.addr, ina.INA228_CONFIG, 2))
    chans = [(ma.addr, ina.INA228_VBUS, 3), (ma.addr, ina.INA228_VSHUNT, 3), (ua.addr, ina.INA228_VSHUNT, 3)]
    st = P.dec_status(c.x(P.enc_stream_config(a.period_us, marker, chans)), P.STREAM_CONFIG)
    if st:
        raise CliError(f"stream config: {P.status_name(P.I2C_STATUS, st)}", status=st)
    st = P.dec_status(c.x(P.enc_stream_start()), P.STREAM_START)
    if st:
        raise CliError(f"stream start: {P.status_name(P.I2C_STATUS, st)}", status=st)
    rows, dropped, end = [], 0, time.monotonic() + a.seconds
    try:
        while time.monotonic() < end:
            _, _, dropped, recs = P.dec_stream_read(c.x(P.enc_stream_read()))
            rows += recs
    except KeyboardInterrupt:  # Ctrl-C / SIGINT ends the stream early; keep and summarise what we have
        pass
    finally:  # never leave the probe sampling (and bus 0 busy) after a host error
        c.x(P.enc_stream_stop())
        for m in (ma, ua):  # restore the power-on ADC_CONFIG so `power` sees a live DIETEMP again
            c.reg_write16(m.addr, ina.INA228_ADC_CONFIG, ina.INA228_ADC_CONFIG_POR)
    while True:  # drain what the sampler left in the ring
        _, _, dropped, recs = P.dec_stream_read(c.x(P.enc_stream_read()))
        if not recs:
            break
        rows += recs
    samples = [decode_sample(ts, fl, d, ma, ua, rng_ma, rng_ua) for ts, fl, d in P.unwrap_ts(rows)]
    if a.out:
        _write_samples(a.out, samples)
    s = summarize(samples)
    s.update(dropped=dropped, i2c_hz=hz, ina228_conv_us=ina.INA228_CT_US[ct], ina228_avg=ina.INA228_AVG[avg],
             out=a.out)
    if not s["samples"]:
        return dict(summary=s), f"no samples ({dropped} dropped)"
    text = (f"{s['samples']} samples ({s['errors']} I2C errors, {dropped} dropped) over {s['duration_s']:.6f} s, "
            f"I2C {hz} Hz, INA228 {s['ina228_conv_us']} us x {s['ina228_avg']}\n"
            f"current mean {s['mean_current_a'] * 1e3:.6f} mA peak {s['peak_current_a'] * 1e3:.6f} mA\n"
            f"power mean {s['mean_power_w'] * 1e3:.6f} mW peak {s['peak_power_w'] * 1e3:.6f} mW\n"
            f"energy {s['energy_j']:.9f} J")
    if a.marker:
        text += "".join(f"\n{a.marker} window {i}: start {w['start_s']:.6f} s, {w['duration_s']:.6f} s, "
                        f"{w['energy_j']:.9f} J" for i, w in enumerate(s["windows"]))
    return dict(summary=s), text


# --- SWD target switch (U57 TMUX1574) -----------------------------------------
def swd_select(c, target):
    """target "e8" = release SWD_SEL, "cc35" = drive it high, then require LOGGER high.
    Never leaves cc35 selected on failure."""
    sel = c.need("swd_sel", "SWD target switch")
    if target == "e8":
        c.pin_set(sel, "release")
        return None
    try:
        c.pin_set(sel, "high")
        logger = c.pin_get(c.need("wifi_logger", "CC3501E LOGGER input"))[0]
    except BaseException:
        c.pin_set(sel, "release")
        raise
    if not logger:
        c.pin_set(sel, "release")
        raise CliError(CC35_UNPOWERED)
    return logger


def cmd_swd_target(c, a):
    sel = c.need("swd_sel", "SWD target switch")
    if a.target != "status":
        swd_select(c, a.target)
    lvl, mode = c.pin_get(sel)
    out = dict(swd_target="cc35" if lvl else "e8", pin=sel, mode=mode)
    if lvl:  # LOGGER is only routed to the probe while SWD_SEL is high
        out["logger"] = c.pin_get(c.need("wifi_logger", "CC3501E LOGGER input"))[0]
    return out, f"SWD target {out['swd_target']} ({sel} {mode})"


# --- EEPROM on module I2C0 ------------------------------------------------------
@contextlib.contextmanager
def eeprom_bus(c):
    """POR held (module SoC off the bus), bus 0 joined to module I2C0; both undone on any exit."""
    por, link = c.need("por", "POR line"), c.need("i2c0_link", "module I2C0 link")
    addr = c.need("eeprom_addr", "EEPROM")
    c.pin_set(por, "high")
    try:
        c.pin_set(link, "low")
        yield addr
    finally:
        try:
            c.pin_set(link, "release")
        finally:
            c.pin_set(por, "release")


def _ee_range(start, n):
    if start < 0 or n < 0 or start + n > N24S128_SIZE:
        raise CliError(f"EEPROM range 0x{start:04X}+{n} outside 0..0x{N24S128_SIZE:04X}")


def ee_read(c, dev, start, n):
    step, out = c.info["i2c_max"], b""
    while len(out) < n:
        a = start + len(out)
        out += c.i2c(dev, struct.pack(">H", a), min(step, n - len(out)), restart=True)
    return out


def ee_write(c, dev, start, data):
    step = c.info["i2c_max"] - 2  # 2 address bytes per transfer
    done = 0
    while done < len(data):
        a = start + done
        n = min(N24S128_PAGE - a % N24S128_PAGE, step, len(data) - done)
        c.i2c(dev, struct.pack(">H", a) + data[done:done + n])
        done += n
        end = time.monotonic() + 4 * N24S128_TWR_S  # ACK polling, bounded by a few tWR
        while True:
            try:
                c.i2c(dev, b"", 1)
                break
            except I2cError as e:
                if e.status != 1 or time.monotonic() > end:
                    raise CliError(f"EEPROM 0x{dev:02X} busy after write at 0x{a:04X}: {e}", status=e.status)
    back = ee_read(c, dev, start, len(data))
    bad = next((i for i in range(len(data)) if back[i] != data[i]), None)
    if bad is not None:
        raise CliError(f"EEPROM verify failed at 0x{start + bad:04X}: wrote {data[bad]:02X}, read {back[bad]:02X}")


def cmd_eeprom(c, a):
    if a.ecmd == "write":
        data = bytes.fromhex(a.hex) if a.hex is not None else open(a.file, "rb").read()
        _ee_range(a.addr, len(data))
        with eeprom_bus(c) as dev:
            ee_write(c, dev, a.addr, data)
        return dict(addr=a.addr, len=len(data), verified=True), f"wrote and verified {len(data)} bytes at 0x{a.addr:04X}"
    start, n = (0, N24S128_SIZE) if a.ecmd == "dump" else (a.addr, a.len)
    _ee_range(start, n)
    with eeprom_bus(c) as dev:
        data = ee_read(c, dev, start, n)
    out = dict(addr=start, len=n, out=a.out)
    if a.out:
        with open(a.out, "wb") as f:
            f.write(data)
        return out, f"read {n} bytes at 0x{start:04X} to {a.out}"
    out["data"] = data.hex()
    return out, data.hex()


# --- CC3501E Config-AP (TI SWRU626 6.5 Table 6-1, 6.9 Table 6-3) ---------------
CFG_APSEL = 0x0
CFG_REGS = [("CFGAPDEVID", 0x00), ("CFGAPDEVUC", 0x04), ("DBGSSVER", 0x08), ("CFGAPBOOT", 0x10),
            ("CFGAPLCST", 0x14), ("CFGAPUDID0", 0x28), ("CFGAPUDID1", 0x2C), ("CFGAPIDR", 0xFC)]
# (field, hi, lo), from the SWRU626 6.9.x field tables
FIELDS = {
    "CFGAPDEVID": [("VER", 31, 28), ("PARTNUM", 27, 12), ("MAN", 11, 1), ("ALWAYSONE", 0, 0)],
    "DBGSSVER": [("REVMAJ", 31, 28), ("REVMIN", 27, 24), ("PWRAP", 7, 7), ("SYSTEMAP", 6, 6), ("APBAP", 5, 5),
                 ("SECAP", 4, 4), ("ETAP", 3, 3), ("ICEPICKM", 2, 2), ("TRIG", 1, 1), ("TRACE", 0, 0)],
    # 6.9.4 DIAGVAL; 6.9.5 LCSVAL lists the same layout after "Life cycle state" (kept as printed)
    "CFGAPBOOT": [("PG_Version", 3, 0), ("Metal_Version", 5, 3), ("Memory_Stacking", 8, 6), ("Package_type", 11, 9),
                  ("Temperature", 13, 12), ("Device_PartNumber", 17, 14), ("Disable_5GHz", 18, 18),
                  ("Disable_6GHz", 19, 19), ("Disable_BLE", 20, 20), ("Disable_BLE_M0+", 21, 21),
                  ("Disable_CAN_FD", 22, 22), ("Boot_ROM_Version_FMU", 25, 23), ("Launch_pad_Mode", 26, 26),
                  ("SDIO_Product_ID", 28, 27), ("TI_Fuse_ROM_Structure_Version", 31, 29)],
    "CFGAPIDR": [("REV", 31, 28), ("JEPIDS", 27, 17), ("APCLASS", 16, 16), ("APVAR", 7, 4), ("APTYPE", 3, 0)],
}
FIELDS["CFGAPLCST"] = FIELDS["CFGAPBOOT"]
CTRL_PWRUP_REQ, CTRL_PWRUP_ACK = 0x50000000, 0xA0000000  # ADIv5 CTRL/STAT C{SYS,DBG}PWRUP{REQ,ACK}
TRANSFER_ACK = {P.T_WAIT: "WAIT", P.T_FAULT: "FAULT"}


def decode_fields(name, v):
    return {f: (v >> lo) & ((1 << (hi - lo + 1)) - 1) for f, hi, lo in FIELDS.get(name, [])}


def _transfer(c, ops, what):
    n, ack, vals = P.dec_transfer(c.x(P.enc_transfer(ops)))
    if n != len(ops) or ack != P.T_OK:
        raise CliError(f"SWD {what}: {n}/{len(ops)} done, ACK {TRANSFER_ACK.get(ack, f'0x{ack:X}')}", ack=ack)
    return vals


def _dap_ok(c, req, cmd, what):
    if P.dec_status(c.x(req), cmd) != P.DAP_OK:
        raise CliError(f"{what} failed")


def cmd_cc35(c, a):
    swd_select(c, "cc35")
    sel = c.board.swd_sel
    connected = False
    try:
        if P.dec_dap_connect(c.x(P.enc_dap_connect())) != P.DAP_PORT_SWD:
            raise CliError("DAP_Connect: SWD port not available")
        connected = True
        _dap_ok(c, P.enc_swj_clock(1000000), P.DAP_SWJ_CLOCK, "DAP_SWJ_Clock")
        # line reset (56 x 1), JTAG-to-SWD 0xE79E (LSB first), line reset, 8 idle cycles
        _dap_ok(c, P.enc_swj_sequence(136, b"\xff" * 7 + b"\x9e\xe7" + b"\xff" * 7 + b"\x00"), P.DAP_SWJ_SEQUENCE,
                "DAP_SWJ_Sequence")
        _dap_ok(c, P.enc_transfer_configure(), P.DAP_TRANSFER_CONFIGURE, "DAP_TransferConfigure")
        idcode = _transfer(c, [P.dp_read(P.DP_IDCODE)], "DP IDCODE")[0]
        # clear sticky errors, SELECT 0 (CTRL/STAT is banked by DPBANKSEL, not reset by a line reset),
        # request debug + system power-up (DP writes only), then poll for the ACKs (ADIv5: not immediate)
        _transfer(c, [P.dp_write(P.DP_ABORT, 0x1E), P.dp_write(P.DP_SELECT, 0),
                      P.dp_write(P.DP_CTRL_STAT, CTRL_PWRUP_REQ)], "DP power-up")
        end = time.monotonic() + 0.1
        while (ctrl := _transfer(c, [P.dp_read(P.DP_CTRL_STAT)], "DP CTRL/STAT")[0]) & CTRL_PWRUP_ACK != CTRL_PWRUP_ACK:
            if time.monotonic() > end:
                raise CliError(f"DP power-up not acknowledged (CTRL/STAT 0x{ctrl:08X})")
        ops, bank = [], None
        for _, reg in CFG_REGS:
            if reg >> 4 != bank:
                bank = reg >> 4
                ops.append(P.dp_write(P.DP_SELECT, CFG_APSEL << 24 | bank << 4))
            ops.append(P.ap_read(reg))
        vals = _transfer(c, ops, "Config-AP read")
    finally:
        try:
            if connected:
                c.x(P.enc_dap_disconnect())  # power-up request left as is: another agent may hold it
        finally:
            c.pin_set(sel, "release")
    regs = {name: dict(value=v, **({"fields": decode_fields(name, v)} if name in FIELDS else {}))
            for (name, _), v in zip(CFG_REGS, vals)}
    udid = regs["CFGAPUDID1"]["value"] << 32 | regs["CFGAPUDID0"]["value"]
    lines = [f"DP IDCODE 0x{idcode:08X}", f"UDID 0x{udid:016X}"]
    for name, r in regs.items():
        f = r.get("fields")
        lines.append(f"{name:10} 0x{r['value']:08X}" + (" " + " ".join(f"{k}={v}" for k, v in f.items()) if f else ""))
    return dict(idcode=idcode, udid=udid, config_ap=regs), "\n".join(lines)


# --- argument parsing ----------------------------------------------------------
class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(f"{self.prog}: {message}", usage=True)


def build_parser():
    common = Parser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print exactly one JSON object on stdout")
    common.add_argument("--serial", default=argparse.SUPPRESS, help="USB serial number of the probe to use")
    ap = Parser(prog="alp-probe", description=__doc__, parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(subs, name, help, **kw):
        return subs.add_parser(name, help=help, description=help, parents=[common], **kw)

    add(sub, "info", "probe, board, protocol and firmware information")
    add(sub, "ports", "find the Alp SE-UART and Alp App Console serial ports")
    pn = add(sub, "pin", "named board pins")
    ps = pn.add_subparsers(dest="pcmd", required=True, metavar="ACTION")
    add(ps, "list", "all pins with level, mode and meaning")
    g = add(ps, "get", "read one pin")
    g.add_argument("name")
    s = add(ps, "set", "set one pin mode")
    s.add_argument("name")
    s.add_argument("mode", choices=P.PIN_MODES)
    r = add(sub, "reset", "DAP nRESET (DAP_SWJ_Pins bit 7). " + RESET_WARN)
    r.add_argument("action", choices=["assert", "release", "pulse"])
    r.add_argument("--ms", type=float, default=100, help="pulse length (default 100 ms)")
    r = add(sub, "por", "module power-on reset (MCU_RST_RP drives POR_N low). " + RESET_WARN)
    r.add_argument("action", choices=["assert", "release", "pulse"])
    r.add_argument("--ms", type=float, default=100, help="pulse length (default 100 ms)")
    m = add(sub, "module", "module power: off = MODULE_EN_RP high (module held off), on = release; "
                           "standby = MODULE_STBY_RP high, wake = release. " + POWER_WARN)
    m.add_argument("action", choices=["on", "off", "standby", "wake"])
    i = add(sub, "i2c", "raw I2C transfer")
    i.add_argument("addr", type=lambda v: int(v, 0), help="7-bit address, e.g. 0x40")
    i.add_argument("--bus", type=int, choices=[0, 1], default=0, help=BUS1_HELP)
    i.add_argument("--write", default="", metavar="HEX", help="bytes to write, e.g. 01")
    i.add_argument("--read", type=int, default=0, metavar="N", help="bytes to read")
    i.add_argument("--restart", action="store_true", help="write then read with a repeated start")
    add(sub, "vio", "VIO_OUT voltage (ADC)")
    rg = add(sub, "range", "module 5 V current range. ua = request the uA range; " + RANGE_NOTE)
    rg.add_argument("action", choices=["status", "ua"])
    add(sub, "power", "one snapshot of every power monitor on the board")
    st = add(sub, "stream", "stream module 5 V (U29 VBUS, U29 VSHUNT, U53 VSHUNT) and integrate energy. "
                            "Reconfigures the INA228 ADC_CONFIG and the bus 0 I2C speed.")
    st.add_argument("--period-us", type=int, default=1000, help="sample period, 200..10000000 (default 1000)")
    st.add_argument("--seconds", type=float, default=1.0, help="duration (default 1)")
    st.add_argument("--marker", metavar="NAME", help="pin whose high level marks an energy window (default: the board's MARKER pin, if any)")
    st.add_argument("--out", metavar="FILE", help="per-sample output, .csv or .jsonl")
    st.add_argument("--i2c-hz", type=int, default=400000, help="bus 0 speed while streaming (default 400000)")
    sw = add(sub, "swd-target", "route SWD + DAP nRESET (SWD_SEL): e8 = release, cc35 = high, fails and returns "
                                "to e8 when the CC3501E LOGGER reads low. " + XDS_WARN)
    sw.add_argument("target", choices=["e8", "cc35", "status"])
    ee = add(sub, "eeprom", f"module EEPROM (N24S128, {N24S128_SIZE} bytes, raw bytes). Holds POR (MCU_RST_RP high) "
                            "and joins bus 0 to module I2C0 (I2C0_LINK_N low) for the access, then releases both. "
                            + RESET_WARN)
    es = ee.add_subparsers(dest="ecmd", required=True, metavar="ACTION")
    num = lambda v: int(v, 0)
    er = add(es, "read", "read LEN bytes at ADDR")
    er.add_argument("addr", type=num)
    er.add_argument("len", type=num)
    er.add_argument("--out", metavar="FILE", help="write the bytes to FILE instead of printing hex")
    ew = add(es, "write", "write bytes at ADDR (page-aligned, ACK polling, read-back verify)")
    ew.add_argument("addr", type=num)
    src = ew.add_mutually_exclusive_group(required=True)
    src.add_argument("--hex", metavar="HEX")
    src.add_argument("--file", metavar="FILE")
    ed = add(es, "dump", "read the whole EEPROM")
    ed.add_argument("--out", metavar="FILE", required=True)
    cc = add(sub, "cc35", "CC3501E over SWD (read only). " + XDS_WARN)
    cs = cc.add_subparsers(dest="ccmd", required=True, metavar="ACTION")
    add(cs, "info", "select cc35, read DP IDCODE and the Config-AP (APSEL 0) registers, disconnect, select e8")
    return ap


HANDLERS = dict(info=cmd_info, ports=cmd_ports, pin=cmd_pin, reset=cmd_reset, por=cmd_por, module=cmd_module,
                i2c=cmd_i2c, vio=cmd_vio, range=cmd_range, power=cmd_power, stream=cmd_stream,
                **{"swd-target": cmd_swd_target, "eeprom": cmd_eeprom, "cc35": cmd_cc35})


def main(argv=None, open_probe=None):
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    try:
        a = build_parser().parse_args(argv)
        a.serial = getattr(a, "serial", None)
        c = Ctx(a.serial, open_probe or (lambda s: Probe(serial=s)))
        data, text = HANDLERS[a.cmd](c, a)
    except (CliError, TransportError, P.ProtocolError, ValueError, OSError) as e:
        if as_json:
            print(json.dumps(dict(ok=False, error=str(e), **getattr(e, "extra", {}))))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 2 if getattr(e, "extra", {}).get("usage") else 1
    except Exception as e:  # malformed reply (IndexError, struct.error, ...): --json still prints one object
        if not as_json:
            raise
        print(json.dumps(dict(ok=False, error=f"{type(e).__name__}: {e}")))
        return 1
    if as_json:
        print(json.dumps(dict(ok=True, **data)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
