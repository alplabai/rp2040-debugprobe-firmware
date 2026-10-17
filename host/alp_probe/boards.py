# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Board profiles, keyed by the name the probe reports with ALP_BOARD (0x8B)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Monitor:
    ref: str
    part: str      # "INA236" or "INA228" (ina.py)
    addr: int      # 7-bit, I2C bus 0
    rail: str
    shunt_ohm: float


@dataclass(frozen=True)
class Board:
    name: str
    monitors: List[Monitor] = field(default_factory=list)
    pins: Dict[str, str] = field(default_factory=dict)  # pin table name -> meaning
    vio_adc: Optional[int] = None    # ADC channel reading VIO_OUT / vio_div
    vio_div: float = 1.0
    module_ma: Optional[str] = None  # monitor ref, module 5 V in mA range
    module_ua: Optional[str] = None  # monitor ref, module 5 V in uA range
    range_pin: Optional[str] = None  # high = mA range
    range_sel: Optional[str] = None  # pulse high = request uA range
    module_en: Optional[str] = None  # high = module held OFF
    module_stby: Optional[str] = None  # high = module standby asserted
    por: Optional[str] = None        # high = module POR_N held low
    marker: Optional[str] = None     # default `stream --marker` pin
    swd_sel: Optional[str] = None    # high = SWD + DAP nRESET to the CC3501E, release = E8
    wifi_logger: Optional[str] = None  # input, reads 1 when the CC3501E is powered (while swd_sel high)
    i2c0_link: Optional[str] = None  # low = RP I2C bus 0 joined to module I2C0 (EEPROM)
    eeprom_addr: Optional[int] = None  # 7-bit, bus 0 while i2c0_link is low

    def monitor(self, ref):
        return next(m for m in self.monitors if m.ref == ref)


EVK_2641_R3 = Board(
    name="alp-e1m-evk-2641-r3",
    monitors=[
        Monitor("U21", "INA236", 0x40, "+3V3", 0.020),
        Monitor("U31", "INA236", 0x41, "+1V8", 0.020),
        Monitor("U33", "INA236", 0x42, "+VIO_SW", 0.050),
        Monitor("U29", "INA228", 0x43, "module 5 V (mA range, VBUS = +5V_C)", 0.1),
        Monitor("U53", "INA228", 0x47, "module 5 V (uA range, VBUS = VMID)", 10.0),
        Monitor("U34", "INA236", 0x49, "+V_CAM1", 0.050),
        Monitor("U30", "INA236", 0x4A, "+5V (whole board)", 0.020),
        Monitor("U32", "INA236", 0x4B, "+V_CAM0", 0.050),
    ],
    pins={
        "BOOT0": "module BOOT0 (E2.V2) via 10k; not connected on the AEN R3 module",
        "BOOT1": "module BOOT1 (E2.U2) via 10k; not connected on the AEN R3 module",
        "BOOT2": "module BOOT2 (E2.U1) via 10k; not connected on the AEN R3 module",
        "BOOT3": "module BOOT3 (E2.V1) via 10k; not connected on the AEN R3 module",
        "MCU_RST_RP": "FET drive, active high: high pulls MCU_RST = module POR_N low (power-on reset)",
        "I2C0_LINK_N": "Q29 drive, 10k pull-up: release = bus 0 NOT joined to module I2C0, low = joined (EEPROM, TAS2563)",
        "MODULE_EN_RP": "FET drive, active high: high pulls MODULE_EN low = module held OFF",
        "MODULE_STBY_RP": "FET drive, active high: high pulls MODULE_STBY low = standby",
        "RTC_WAKE_N": "input: module RTC_WAKE_N (open drain, low = wake)",
        "CURR_SEL": "FET drive, active high: high then release = request uA range",
        "CURR_STATUS": "input: high = mA range, low = uA range",
        "CURR_ALERT_N": "input: U29/U53 INA228 ALERT, low = alert",
        "PD_ACTIVE_N": "input: low = USB-PD contract supplying the board",
        "PWR_FLT_N": "input: low = eFuse fault",
        "SWD_SEL": "U57 SEL, 100k pull-down: release = SWD + DAP nRESET to the E8, high = to the CC3501E",
        "MARKER": "input: E8 pad E2.AG6 (target toggles it around each inference)",
        "WIFI_LOGGER": "input: SWD_SEL high = CC3501E LOGGER (1 = CC3501E powered), SWD_SEL low = E8 TDO",
    },
    vio_adc=3, vio_div=2.0,  # ADC3 = VIO_OUT / 2 (R230 / R222 100k)
    module_ma="U29", module_ua="U53",
    range_pin="CURR_STATUS", range_sel="CURR_SEL",
    module_en="MODULE_EN_RP", module_stby="MODULE_STBY_RP", por="MCU_RST_RP",
    marker="MARKER", swd_sel="SWD_SEL", wifi_logger="WIFI_LOGGER", i2c0_link="I2C0_LINK_N",
    eeprom_addr=0x50,  # N24S128 (module U18): 1010 + A2..A0 factory default 0 (onsemi N24S128/D Rev. 3, Device Addressing)
)

GENERIC = Board(name="generic")

BOARDS = {b.name: b for b in (EVK_2641_R3, GENERIC)}


def get(name):
    return BOARDS.get(name or "generic", GENERIC)
