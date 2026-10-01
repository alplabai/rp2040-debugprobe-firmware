# Changelog

## Unreleased

- Add a second CDC-ACM channel ("Alp App Console", UART1) next to the
  "Alp SE-UART" channel (UART0); enabled per board via `PROBE_UART1_*`.
- Generalise `src/cdc_uart.c` to N instances; autobaud stays on CDC0.
- Fix the CAP_BREAK descriptor patch for multiple CDC blocks.
- Pico build: CDC0 moves to UART0 (GP12/GP13), CDC1 on UART1 (GP8/GP9);
  product string "Alp Lab Debug Probe on Pico (CMSIS-DAP)".
- Fork scaffolding: README, CONTRIBUTING, SECURITY, CI build and release workflow.
