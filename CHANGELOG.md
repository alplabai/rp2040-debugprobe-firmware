# Changelog

## Unreleased

- Add the Alp Lab E1M-EVK 2641-R3 target (`-DALP_E1M_EVK=ON -DPICO_BOARD=alp_e1m_evk`,
  `debugprobe_alp_e1m_evk.uf2`): pico-sdk board file (MX25U1632F, generic_03h boot2),
  1.8 V pad voltage select, active-high DAP reset through an N-FET, 4 MHz SWCLK cap,
  CDC0 SE-UART on UART1 and CDC1 console on UART0, two I2C buses, ADC3 VIO sense,
  17-entry pin table for the 2641-R3 carrier with the provisioning additions
  (SWD mux to the CC3501E, I2C0 link to the module EEPROM, inference marker),
  and a USB serial number kept in the last flash sector (`PROBE_SERIAL_IN_FLASH`).
- Vendor protocol v3: I2C bus 1 (`ALP_I2C_XFER` flags bit1, `ALP_I2C_CONFIG` hz
  bit31), `ALP_ADC_READ` (`0x8A`), `ALP_BOARD` (`0x8B`), stream record flags bit3
  (aux GPIO), input-only pins (`ALP_PIN_IN`, PIN_INFO flags bit1, PIN_SET status 2
  on drive). Unknown IDs are now `0x8C`..`0x9F`.
- Replace `tools/alp_probe.py` with the `host/` package (`alp-probe` CLI, `--json`,
  INA236/INA228 decoding, EVK board profile, power snapshot and energy stream,
  pytest suite run in CI); `eeprom`, `swd-target` and read-only `cc35 info`
  commands. `tools/99-alp-probe.rules` udev rule; README flashing guide.
- Add the power/energy stream, vendor commands `0x86`..`0x89` (`ALP_STREAM_*`):
  a core-0 sampler task reads configured I2C monitor registers every
  `period_us` into a 12 KiB ring with timestamps and a marker-pin level, drained
  by the host. `ALP_PROTO_VERSION` is now 2 (feature bit2 STREAM); `0x81`/`0x82`
  answer busy (status 5) while a stream runs. `tools/alp_probe.py stream`.

- Add CMSIS-DAP vendor commands `0x80`..`0x85` (`src/alp_vendor.c`): probe info,
  raw I2C transfer/config, and named GPIO control from a per-board pin table;
  replaces the Arm `DAP_vendor.c` template in the build. Host example in
  `tools/alp_probe.py`; see README "Vendor commands".
- Pico build: I2C0 on GP16/GP17 and demo pins GP20/GP21 (development only).
- Add a second CDC-ACM channel ("Alp App Console", UART1) next to the
  "Alp SE-UART" channel (UART0); enabled per board via `PROBE_UART1_*`.
- Generalise `src/cdc_uart.c` to N instances; autobaud stays on CDC0.
- Fix the CAP_BREAK descriptor patch for multiple CDC blocks.
- Pico build: CDC0 moves to UART0 (GP12/GP13), CDC1 on UART1 (GP8/GP9);
  product string "Alp Lab Debug Probe on Pico (CMSIS-DAP)".
- Fork scaffolding: README, CONTRIBUTING, SECURITY, CI build and release workflow.
