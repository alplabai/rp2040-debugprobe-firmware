# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""CLI against the fake probe, plus stream decoding and energy integration."""
import json
import struct

import pytest

from alp_probe import boards, cli
from alp_probe import protocol as P
from conftest import EVK_PINS


def run(fake, capsys, *argv):
    rc = cli.main(list(argv) + ["--json"], open_probe=lambda s: fake)
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # exactly one JSON object
    return rc, json.loads(out)


def add_monitors(fake):
    for m in boards.EVK_2641_R3.monitors:
        if m.part == "INA228":
            fake.regs[(0, m.addr)] = {0x00: b"\x00\x00", 0x04: bytes.fromhex("00C800"), 0x05: bytes.fromhex("064000"),
                                      0x06: bytes.fromhex("0C80")}
        else:
            fake.regs[(0, m.addr)] = {0x00: b"\x41\x27", 0x01: bytes.fromhex("0190"), 0x02: bytes.fromhex("080F")}


def test_info(fake, capsys):
    rc, d = run(fake, capsys, "info")
    assert rc == 0 and d["ok"]
    assert d["info"]["board"] == "alp-e1m-evk-2641-r3" and d["info"]["version"] == 3
    assert d["info"]["product"] == "Alp Lab E1M EVK Probe (CMSIS-DAP)" and d["info"]["firmware"] == "2.2.3"


def test_pins_and_module(fake, capsys):
    rc, d = run(fake, capsys, "pin", "list")
    assert [p["name"] for p in d["pins"]][:2] == ["BOOT0", "BOOT1"]
    run(fake, capsys, "module", "off")
    assert fake.pins["MODULE_EN_RP"] == [2, 1]
    run(fake, capsys, "module", "on")
    assert fake.pins["MODULE_EN_RP"][0] == 0
    run(fake, capsys, "module", "standby")
    assert fake.pins["MODULE_STBY_RP"] == [2, 1]
    run(fake, capsys, "por", "pulse", "--ms", "0")
    assert fake.pins["MCU_RST_RP"][0] == 0
    rc, d = run(fake, capsys, "pin", "get", "NOPE")
    assert rc == 1 and not d["ok"] and "NOPE" in d["error"]


def test_reset(fake, capsys):
    run(fake, capsys, "reset", "assert")
    assert fake.log[-1] == bytes([0x10, 0x00, 0x80, 0, 0, 0, 0]) and fake.nreset is False
    run(fake, capsys, "reset", "release")
    assert fake.nreset is True


def test_i2c(fake, capsys):
    fake.regs[(1, 0x50)] = {0x00: b"\xAA"}
    rc, d = run(fake, capsys, "i2c", "0x50", "--bus", "1", "--write", "00", "--read", "1", "--restart")
    assert rc == 0 and d["data"] == "aa"
    assert fake.log[-1] == bytes([0x81, 0x50, 3, 1, 1, 0])
    rc, d = run(fake, capsys, "i2c", "0x51", "--read", "1")
    assert rc == 1 and d["status"] == 1
    n = len(fake.log)
    for argv in (["--write", "00" * 60], ["--read", "60"]):  # > i2c_max 59 would spill into a 2nd packet
        rc, d = run(fake, capsys, "i2c", "0x50", *argv)
        assert rc == 1 and "too long" in d["error"]
    assert all(len(r) <= 64 for r in fake.log[n:])


def test_garbled_reply_is_one_json_object(fake, capsys, monkeypatch):
    real = fake.xfer
    monkeypatch.setattr(fake, "xfer", lambda req: real(req)[:2] if req[0] == 0x80 else real(req))
    rc, d = run(fake, capsys, "info")
    assert rc == 1 and not d["ok"]


def test_vio_and_range(fake, capsys):
    rc, d = run(fake, capsys, "vio")
    assert d["vio_v"] == pytest.approx(2048 * 3.3 / 4096 * 2, abs=1e-4)
    assert run(fake, capsys, "range", "status")[1]["range"] == "mA"
    rc, d = run(fake, capsys, "range", "ua")
    assert d["range"] == "uA" and "no software path" in d["note"]
    assert fake.pins["CURR_SEL"][0] == 0  # released after the pulse


def test_power(fake, capsys):
    add_monitors(fake)
    rc, d = run(fake, capsys, "power")
    rows = {r["ref"]: r for r in d["monitors"]}
    assert rows["U21"]["current_a"] == pytest.approx(1e-3 / 0.020)
    assert rows["U21"]["vbus_v"] == pytest.approx(2063 * 1.6e-3)
    assert rows["U29"]["current_a"] == pytest.approx(10e-3) and rows["U29"]["dietemp_c"] == pytest.approx(25.0)
    assert d["module_5v"]["monitor"] == "U29" and "hint" not in d
    fake.pins["CURR_STATUS"][1] = 0
    del fake.regs[(0, 0x4B)]
    rc, d = run(fake, capsys, "power")
    assert d["module_5v"]["monitor"] == "U53" and d["module_5v"]["current_a"] == pytest.approx(100e-6)
    assert "S6" in d["hint"]


def test_generic_board(capsys):
    from conftest import FakeProbe
    rc, d = run(FakeProbe(board="generic"), capsys, "power")
    assert rc == 1 and "no power monitors" in d["error"]


def test_usage_error_json(capsys):
    rc = cli.main(["bogus", "--json"])
    assert rc == 2 and json.loads(capsys.readouterr().out)["ok"] is False


def rec(ts, flags, vb, vs_ma, vs_ua):
    return (ts, flags, bytes.fromhex(vb + vs_ma + vs_ua))


def test_decode_sample():
    ev = boards.EVK_2641_R3
    ma, ua = ev.monitor("U29"), ev.monitor("U53")
    s = cli.decode_sample(*rec(0, P.F_AUX, "064000", "00C800", "000000"), ma, ua, 0, 0)
    assert s["range"] == "mA" and s["current_a"] == pytest.approx(10e-3) and s["power_w"] == pytest.approx(50e-3)
    s = cli.decode_sample(*rec(0, P.F_MARKER, "064000", "000000", "00C800"), ma, ua, 0, 0)
    assert s["range"] == "uA" and s["current_a"] == pytest.approx(100e-6) and s["marker"] == 1


def test_summarize():
    def smp(t, p, m=0, f=0):
        return dict(timestamp_us=t, power_w=p, current_a=p / 5, marker=m, flags=f | m)
    s = cli.summarize([smp(0, 1.0), smp(1000, 2.0, 1), smp(2000, 1.0, 1), smp(2500, 9.0, f=P.F_I2C_ERR),
                       smp(3000, 1.0), smp(4000, 1.0)])
    # 1 W x 1 ms + 2 W x 1 ms + 1 W x 1 ms (error sample skipped) + 1 W x 1 ms
    assert s["energy_j"] == pytest.approx(5e-3)
    assert s["samples"] == 5 and s["errors"] == 1 and s["duration_s"] == pytest.approx(4e-3)
    assert s["peak_power_w"] == 2.0
    assert len(s["windows"]) == 1
    w = s["windows"][0]
    assert w["energy_j"] == pytest.approx(3e-3) and w["duration_s"] == pytest.approx(2e-3)
    assert w["start_s"] == pytest.approx(1e-3)


def test_stream_cli(fake, capsys, tmp_path):
    add_monitors(fake)
    fake.records = [rec(1000 * i, P.F_AUX | (P.F_MARKER if i in (2, 3) else 0), "064000", "00C800", "000000")
                    for i in range(10)]
    out = tmp_path / "s.csv"
    rc, d = run(fake, capsys, "stream", "--seconds", "0", "--marker", "RTC_WAKE_N", "--out", str(out))
    s = d["summary"]
    assert rc == 0 and s["samples"] == 10
    assert s["energy_j"] == pytest.approx(9 * 1e-3 * 50e-3)
    assert s["windows"][0]["energy_j"] == pytest.approx(2 * 1e-3 * 50e-3)
    assert bytes([0x81, 0x43, 0, 3, 0, 0x01, 0xB2, 0x41]) in fake.log  # ADC_CONFIG for 1000 us
    assert fake.regs[(0, 0x43)][0x01] == fake.regs[(0, 0x47)][0x01] == bytes([0xFB, 0x68])  # restored to reset
    cfg = next(r for r in fake.log if r[0] == 0x86)
    assert cfg == bytes([0x86, 0xE8, 0x03, 0, 0, 8, 3, 0x43, 5, 3, 0x43, 4, 3, 0x47, 4, 3])
    assert out.read_text().splitlines()[0] == "timestamp_us,range,vbus_v,current_a,power_w,marker,flags"


def test_stream_marker_default(fake, capsys):
    add_monitors(fake)
    rc, d = run(fake, capsys, "stream", "--seconds", "0")
    assert rc == 0
    cfg = next(r for r in fake.log if r[0] == 0x86)
    assert cfg[5] == EVK_PINS.index("MARKER")


def test_swd_target(fake, capsys):
    rc, d = run(fake, capsys, "swd-target", "cc35")
    assert rc == 0 and d["swd_target"] == "cc35" and d["logger"] == 1 and fake.pins["SWD_SEL"] == [2, 1]
    rc, d = run(fake, capsys, "swd-target", "e8")
    assert d["swd_target"] == "e8" and fake.pins["SWD_SEL"][0] == 0
    fake.cc35_powered = False
    rc, d = run(fake, capsys, "swd-target", "cc35")
    assert rc == 1 and d["error"] == "CC3501E not powered (LOGGER low)" and fake.pins["SWD_SEL"] == [0, 0]
    assert run(fake, capsys, "swd-target", "status")[1]["swd_target"] == "e8"


def test_eeprom_write_read_dump(fake, capsys, tmp_path):
    data = bytes(range(200))
    rc, d = run(fake, capsys, "eeprom", "write", "0x3F0", "--hex", data.hex())
    assert rc == 0 and d["verified"] and fake.eeprom.mem[0x3F0:0x3F0 + 200] == data
    writes = [r for r in fake.log if r[0] == 0x81 and r[1] == 0x50 and r[3] > 2]
    for r in writes:  # each write stays inside one 64-byte page and one probe packet
        a, n = r[5] << 8 | r[6], r[3] - 2
        assert a // 64 == (a + n - 1) // 64 and n <= 57
    assert set(fake.ee_seen) == {(2, 1)}  # POR held and bus linked for every access
    assert fake.pins["MCU_RST_RP"][0] == 0 and fake.pins["I2C0_LINK_N"] == [0, 1]
    rc, d = run(fake, capsys, "eeprom", "read", "0x3F0", "4")
    assert d["data"] == "00010203"
    out = tmp_path / "ee.bin"
    rc, d = run(fake, capsys, "eeprom", "dump", "--out", str(out))
    assert rc == 0 and out.read_bytes() == bytes(fake.eeprom.mem)
    rc, d = run(fake, capsys, "eeprom", "read", "0x3FFF", "2")
    assert rc == 1 and "outside" in d["error"]


def test_eeprom_releases_on_error(fake, capsys):
    fake.eeprom.busy_polls = 10 ** 6  # never finishes its write cycle
    rc, d = run(fake, capsys, "eeprom", "write", "0", "--hex", "AA")
    assert rc == 1 and "busy" in d["error"]
    assert fake.pins["MCU_RST_RP"][0] == 0 and fake.pins["I2C0_LINK_N"] == [0, 1]
    fake.eeprom.busy_polls = fake.eeprom.busy = 0
    real = fake.eeprom.xfer
    fake.eeprom.xfer = lambda w, n: real(w[:2] + bytes([0x55]) + w[3:], n) if len(w) > 2 else real(w, n)
    rc, d = run(fake, capsys, "eeprom", "write", "0", "--hex", "AA")
    assert rc == 1 and "verify failed at 0x0000" in d["error"]
    assert fake.pins["MCU_RST_RP"][0] == 0 and fake.pins["I2C0_LINK_N"] == [0, 1]


def test_cc35_info(fake, capsys):
    rc, d = run(fake, capsys, "cc35", "info")
    assert rc == 0 and d["idcode"] == 0x6BA02477 and d["udid"] == 0x5566778811223344
    regs = d["config_ap"]
    assert regs["CFGAPIDR"]["value"] == 0x102E0001 and regs["CFGAPIDR"]["fields"]["JEPIDS"] == 0x17
    assert regs["CFGAPDEVID"]["fields"]["MAN"] == 0x17
    assert regs["CFGAPBOOT"]["fields"]["PG_Version"] == 5 and regs["CFGAPBOOT"]["fields"]["Metal_Version"] == 4
    assert regs["CFGAPBOOT"]["fields"]["Memory_Stacking"] == 2
    assert fake.ap_writes == []
    sels = {struct.unpack("<I", r[i + 1:i + 5])[0] for r in fake.log if r[0] == 0x05
            for i in range(3, len(r) - 4) if r[i] == 0x08}
    assert {0x00, 0x10, 0x20, 0xF0} <= sels
    assert fake.log[-2][0] == 0x03 and fake.pins["SWD_SEL"][0] == 0  # disconnected, back to e8
    # ABORT, SELECT 0 before the banked CTRL/STAT power-up request; never a CTRL/STAT power-down
    assert fake.dp_writes[:3] == [(0x0, 0x1E), (0x8, 0), (0x4, 0x50000000)]
    assert [w for w in fake.dp_writes if w[0] == 0x4] == [(0x4, 0x50000000)]
    assert bytes([0x12, 136]) + b"\xff" * 7 + b"\x9e\xe7" + b"\xff" * 7 + b"\x00" in fake.log


def test_cc35_abort_paths(fake, capsys):
    fake.cc35_powered = False
    rc, d = run(fake, capsys, "cc35", "info")
    assert rc == 1 and "LOGGER low" in d["error"] and fake.pins["SWD_SEL"][0] == 0
    assert not any(r[0] == 0x02 for r in fake.log)  # never connected
    fake.cc35_powered, fake.cc35_fault = True, 0x14
    rc, d = run(fake, capsys, "cc35", "info")
    assert rc == 1 and "FAULT" in d["error"]
    assert any(r[0] == 0x03 for r in fake.log) and fake.pins["SWD_SEL"][0] == 0


def test_cc35_pwrup_ack_polled(fake, capsys):
    fake.cc35_ack_delay = 3
    rc, d = run(fake, capsys, "cc35", "info")
    assert rc == 0 and fake.cc35_ack_delay == 0
    fake.cc35_ack_delay = 10 ** 9
    rc, d = run(fake, capsys, "cc35", "info")
    assert rc == 1 and "not acknowledged" in d["error"] and fake.pins["SWD_SEL"][0] == 0
