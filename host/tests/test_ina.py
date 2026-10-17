# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""Hand-computed register values from the datasheet LSBs (SBOSA81D, SLYS021A)."""
import pytest

from alp_probe import ina


def test_ina236():
    assert ina.vshunt("INA236", 0x8300) == pytest.approx(-80e-3)  # SBOSA81D 7.6.1.2 worked example
    assert ina.vshunt("INA236", 0x0190) == pytest.approx(1e-3)    # 400 x 2.5 uV
    assert ina.vshunt("INA236", 0x0190, rng=1) == pytest.approx(0.25e-3)  # 400 x 625 nV
    assert ina.vbus("INA236", 0x0C80) == pytest.approx(5.12)      # 3200 x 1.6 mV
    assert ina.vbus("INA236", 0x8C80) == pytest.approx(5.12)      # bit 15 reserved
    assert ina.adcrange("INA236", 0x4127) == 0                    # reset value
    assert ina.adcrange("INA236", 0x5127) == 1


def test_ina228():
    assert ina.vbus("INA228", 0x064000) == pytest.approx(5.0)     # 25600 x 195.3125 uV
    assert ina.vshunt("INA228", 0x00C800) == pytest.approx(1e-3)  # 3200 x 312.5 nV
    assert ina.vshunt("INA228", 0x00C800, rng=1) == pytest.approx(0.25e-3)  # 3200 x 78.125 nV
    assert ina.vshunt("INA228", 0xFFFFF0) == pytest.approx(-312.5e-9)
    assert ina.dietemp_228(0x0C80) == pytest.approx(25.0)         # 3200 x 7.8125 m degC
    assert ina.dietemp_228(0xFF80) == pytest.approx(-1.0)
    assert ina.adcrange("INA228", 0x0010) == 1


def test_ina228_adc_config():
    # reset value FB68h = MODE Fh, VBUSCT/VSHCT/VTCT 5h, AVG 0h (Table 7-6)
    assert ina.adc_config_228(0xF, 5, 5, 5, 0) == 0xFB68
    assert ina.pick_adc_228(1000) == (1, 1)   # 2 x 84 us x 4 = 672 us
    assert ina.pick_adc_228(200) == (1, 0)    # 2 x 84 us = 168 us
    assert ina.pick_adc_228(50) == (0, 0)     # nothing fits: fastest
    assert ina.adc_config_228(0xB, 1, 1, 0, 1) == 0xB241
