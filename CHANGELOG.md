# Changelog

## Unreleased

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
