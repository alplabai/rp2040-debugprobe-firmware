# rp2040-debugprobe-firmware

RP2040 on-board debug probe firmware for Alp Lab E1M evaluation kits. One USB
device exposes:

- **CMSIS-DAP v2** (bulk) for SWD debug and flashing,
- **CDC-ACM "Alp SE-UART"**, the secure-enclave ISP / recovery serial port,
- **CDC-ACM "Alp App Console"**, the application console.

Derived from [raspberrypi/debugprobe](https://github.com/raspberrypi/debugprobe)
(v2.3.1), Copyright (c) Raspberry Pi (Trading) Ltd. and contributors, MIT
licensed. See [LICENSE](LICENSE) and [NOTICE](NOTICE). New code in this fork is
Copyright (c) Alp Lab AB, also MIT.

SDK-side integration (board documentation, runner configuration, hardware-in-
the-loop tests) lives in [alplabai/alp-sdk](https://github.com/alplabai/alp-sdk),
not here.

## USB contract

Host tooling should identify the serial ports by interface string, not by
enumeration order.

| Interface | Function | String | Endpoints | UART |
|---:|---|---|---|---|
| 0 | CMSIS-DAP v2 (vendor/bulk) | `CMSIS-DAP v2 Interface` | OUT `0x04`, IN `0x85` | n/a (SWD via PIO) |
| 1, 2 | CDC0 (IAD) | `Alp SE-UART` | notify IN `0x81`, data OUT `0x02`, IN `0x83` | UART0 |
| 3, 4 | CDC1 (IAD) | `Alp App Console` | notify IN `0x86`, data OUT `0x07`, IN `0x88` | UART1 |

- CDC0 follows the host line coding (arbitrary baud rate, parity, stop bits),
  including send-break. The upstream autobaud feature applies to CDC0 only.
- CDC1 is optional and compile-time per board (`PROBE_UART1_INTERFACE`,
  `PROBE_UART1_TX`, `PROBE_UART1_RX` in the board config header). Without it
  the build is single-CDC: interfaces 0-2 only, string `CDC-ACM UART Interface`.
- The product string contains `CMSIS-DAP` so OpenOCD, pyOCD and probe-rs detect
  the probe.

### VID/PID (placeholder)

The device uses the upstream Raspberry Pi Debug Probe ID `0x2E8A:0x000c`. This
is a **placeholder** until a dedicated ID is allocated, tracked in
[alplabai/alp-sdk#405](https://github.com/alplabai/alp-sdk/issues/405) and
[alplabai/alp-studio#52](https://github.com/alplabai/alp-studio/issues/52). It
is defined in one place: `desc_device` in `src/usb_descriptors.c`.

## Supported boards

| Board header | Target | Channels |
|---|---|---|
| `include/board_pico_config.h` | Raspberry Pi Pico (`-DDEBUG_ON_PICO=ON`) | CDC0 UART0 (GP12 TX / GP13 RX), CDC1 UART1 (GP8 TX / GP9 RX) |
| `include/board_debug_probe_config.h` | Raspberry Pi Debug Probe hardware | CDC0 only, UART1 (GP4 TX / GP5 RX); channel 1 disabled (single UART connector) |

A `board_alp_e1m_evk_config.h` will be added once the schematic of the E1M EVK
board revision carrying the RP2040 is fixed. Until then there is no Alp Lab EVK
board config and no EVK pin assignment in this repository.

## Build

Requires pico-sdk 2.2.0 (with submodules), the Arm GNU toolchain
(`arm-none-eabi-gcc`), CMake and Ninja. Clone with submodules
(`git submodule update --init --recursive`), then:

```sh
export PICO_SDK_PATH=<path to pico-sdk 2.2.0> PICO_TOOLCHAIN_PATH=<toolchain bin dir>

# Debug Probe hardware -> build/debugprobe.uf2
cmake -G Ninja -S . -B build && ninja -C build

# Raspberry Pi Pico -> build-pico/debugprobe_on_pico.uf2
cmake -G Ninja -S . -B build-pico -DDEBUG_ON_PICO=ON -DPICO_BOARD=pico && ninja -C build-pico
```

Both targets must build warning-free (`-Wall`). Load a `.uf2` through the RP2040
UF2 bootloader.

## Upstream tracking

- `upstream-master` mirrors `raspberrypi/debugprobe` `master` (fast-forward only).
- Upstream changes arrive by merging upstream release tags into `dev`.
- Upstream files keep their original per-file licence headers; do not relicense them.

## Branches and releases

`dev` is the default branch; all pull requests target `dev`. Releases are tags
`vX.Y.Z`; the release workflow attaches the `.uf2` files. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Status

USB enumeration of the two-channel layout is built and descriptor-checked but
has not been verified on RP2040 hardware yet.
