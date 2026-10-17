# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""INA236 / INA228 register decoding. Registers are MSB first on the wire.
Current is computed on the host as Vshunt / Rshunt (CALIBRATION not used).

Sources: TI INA236 datasheet SBOSA81D (Aug 2023), TI INA228 datasheet SLYS021A (May 2022).
INA236A and INA236B differ only in their I2C address range (SBOSA81D Table 7-1:
A = 0x40..0x43, B = 0x48..0x4B); the registers are identical."""

# --- INA236 (SBOSA81D) ---
INA236_CONFIG = 0x00          # Table 7-2
INA236_VSHUNT = 0x01          # Table 7-2, 16-bit two's complement (Table 7-5)
INA236_VBUS = 0x02            # Table 7-2, bits 14-0 (Table 7-6)
INA236_ADCRANGE_BIT = 1 << 12  # CONFIG bit 12 (Table 7-4): 0 = +-81.92 mV, 1 = +-20.48 mV
INA236_VSHUNT_LSB = (2.5e-6, 625e-9)  # V/LSB for ADCRANGE 0 / 1 (Table 8-1)
INA236_VBUS_LSB = 1.6e-3      # V/LSB (Table 8-1)

# --- INA228 (SLYS021A) ---
INA228_CONFIG = 0x00          # Table 7-3
INA228_ADC_CONFIG = 0x01      # Table 7-3
INA228_VSHUNT = 0x04          # Table 7-3, 24-bit, value in bits 23-4 (Table 7-9)
INA228_VBUS = 0x05            # Table 7-3, 24-bit, value in bits 23-4 (Table 7-10)
INA228_DIETEMP = 0x06         # Table 7-3, 16-bit two's complement (Table 7-11)
INA228_ADCRANGE_BIT = 1 << 4  # CONFIG bit 4 (Table 7-5): 0 = +-163.84 mV, 1 = +-40.96 mV
INA228_VSHUNT_LSB = (312.5e-9, 78.125e-9)  # V/LSB for ADCRANGE 0 / 1 (Table 7-9, Table 8-1)
INA228_VBUS_LSB = 195.3125e-6  # V/LSB (Table 7-10, Table 8-1)
INA228_DIETEMP_LSB = 7.8125e-3  # degC/LSB (Table 7-11, Table 8-1)
# ADC_CONFIG fields (Table 7-6): MODE 15-12, VBUSCT 11-9, VSHCT 8-6, VTCT 5-3, AVG 2-0
INA228_ADC_CONFIG_POR = 0xFB68  # reset value (7.6.1.2): MODE Fh, CTs 5h, AVG 0
INA228_MODE_CONT_SHUNT_BUS = 0xB  # continuous shunt and bus voltage (Table 7-6)
INA228_CT_US = (50, 84, 150, 280, 540, 1052, 2074, 4120)  # code 0..7 (Table 7-6)
INA228_AVG = (1, 4, 16, 64, 128, 256, 512, 1024)          # code 0..7 (Table 7-6)

PARTS = ("INA236", "INA228")


def _s(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def be(data):
    return int.from_bytes(bytes(data), "big")


def regs(part):
    """-> (config, vshunt, vbus, nbytes) register addresses and data size."""
    if part == "INA228":
        return INA228_CONFIG, INA228_VSHUNT, INA228_VBUS, 3
    return INA236_CONFIG, INA236_VSHUNT, INA236_VBUS, 2


def adcrange(part, config):
    return int(bool(config & (INA228_ADCRANGE_BIT if part == "INA228" else INA236_ADCRANGE_BIT)))


def vshunt(part, raw, rng=0):
    """raw = register value as an int (16 bit INA236, 24 bit INA228) -> volts."""
    if part == "INA228":
        return (_s(raw, 24) >> 4) * INA228_VSHUNT_LSB[rng]  # bits 3-0 reserved
    return _s(raw, 16) * INA236_VSHUNT_LSB[rng]


def vbus(part, raw):
    if part == "INA228":
        return (_s(raw, 24) >> 4) * INA228_VBUS_LSB
    return (raw & 0x7FFF) * INA236_VBUS_LSB


def dietemp_228(raw):
    return _s(raw, 16) * INA228_DIETEMP_LSB


def adc_config_228(mode, vbusct, vshct, vtct, avg):
    """Codes (not microseconds) -> ADC_CONFIG register value."""
    return (mode << 12) | (vbusct << 9) | (vshct << 6) | (vtct << 3) | avg


def pick_adc_228(period_us, n_inputs=2):
    """Largest conversion time x averaging whose cycle (n_inputs conversions x avg)
    fits in period_us. -> (ct_code, avg_code); the fastest setting if nothing fits."""
    best, best_t = (0, 0), 0
    for a, navg in enumerate(INA228_AVG):
        for c, ct in enumerate(INA228_CT_US):
            t = ct * navg
            if n_inputs * t <= period_us and t > best_t:
                best, best_t = (c, a), t
    return best
