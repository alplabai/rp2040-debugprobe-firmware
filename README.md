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

## Vendor commands

Besides SWD and the UART channels the probe offers raw I2C and named-GPIO
control as CMSIS-DAP vendor commands (request IDs `0x80`..`0x9F`, implemented
in `src/alp_vendor.c`). They travel over the existing CMSIS-DAP v2 bulk
interface (interface 0); there is no extra USB interface. The firmware is
chip-agnostic: the host decodes whatever current-sense chip is on the I2C bus.
`tools/alp_probe.py` is a host example (`--selftest` checks the encoders against
the tables below).

All multi-byte values are little-endian. Every response starts with the request
ID. A request is one DAP packet (`DAP_PACKET_SIZE` = 64 bytes), sent standalone.
Unknown IDs in `0x8A`..`0x9F` reply with the single byte `0xFF` (`ID_DAP_Invalid`).

| ID | Name | Request (after ID) | Response (after ID) |
|---:|---|---|---|
| `0x80` | `ALP_INFO` | none | `version` u8 (= 2), `features` u8 (bit0 I2C, bit1 GPIO, bit2 STREAM), `npins` u8, `i2c_max` u8 |
| `0x81` | `ALP_I2C_XFER` | `addr7` u8, `flags` u8, `wlen` u8, `rlen` u8, `wdata[wlen]` | `status` u8, then `rdata[rlen]` only when `status` = 0 |
| `0x82` | `ALP_I2C_CONFIG` | `hz` u32 | `actual_hz` u32 |
| `0x83` | `ALP_PIN_SET` | `index` u8, `mode` u8 | `status` u8, `level` u8 |
| `0x84` | `ALP_PIN_GET` | `index` u8 | `status` u8, `level` u8, `mode` u8 |
| `0x85` | `ALP_PIN_INFO` | `index` u8 | `status` u8, `flags` u8, `namelen` u8, `name[namelen]` ASCII |
| `0x86` | `ALP_STREAM_CONFIG` | `period_us` u32, `marker` u8, `n` u8, then `n` x (`addr7` u8, `reg` u8, `len` u8) | `status` u8 |
| `0x87` | `ALP_STREAM_START` | none | `status` u8 |
| `0x88` | `ALP_STREAM_STOP` | none | `status` u8 (always 0) |
| `0x89` | `ALP_STREAM_READ` | none | `status` u8, `count` u8, `recsize` u8, `dropped` u32, then `count` x record |

**`ALP_INFO`**: `npins` counts the pins that survived the boot-time check
(below). `i2c_max` is the largest `wlen` and `rlen` accepted (`DAP_PACKET_SIZE`
- 5 = 59), 0 when the board has no I2C. A board with neither I2C nor pins reports
`features` = 0.

**`ALP_I2C_XFER`**: `flags` bit0 = write-then-read with a repeated start; all
other bits must be 0. Without bit0 a write and a read are separate transactions
(each ends with STOP); `wlen` = 0 gives a read only, `rlen` = 0 a write only.
`wlen` and `rlen` both 0 is rejected, as is bit0 with either 0.

| `status` | Meaning |
|---:|---|
| 0 | OK |
| 1 | NACK or bus error (`PICO_ERROR_GENERIC`) |
| 2 | timeout (about 3 ms per byte, so a stuck bus never hangs the DAP thread) |
| 3 | bad length or address: `addr7` < `0x08` or > `0x77`, `wlen`/`rlen` > `i2c_max`, bad flags |
| 4 | I2C not available on this board |
| 5 | busy: a stream is running (see below) |

**`ALP_I2C_CONFIG`**: `hz` is clamped to 10000..1000000; the reply is the baud
rate the hardware actually got (`i2c_set_baudrate`), or 0 when I2C is not
available or a stream is running (this reply has no status byte). The boot speed is the board's `PROBE_I2C_BAUDRATE` (default 100000).

### Power/energy stream (`0x86`..`0x89`)

Single `ALP_I2C_XFER` round trips (USB poll, host scheduling) are too slow and
jittery to resolve one inference, so the probe samples the I2C monitors itself
and the host drains a timestamped ring. The firmware does no unit conversion:
the host decodes the monitor registers (INA2xx-class or any other I2C chip).

`ALP_STREAM_CONFIG` stops a running stream, then sets: `period_us` (200..;
rounded up to the 50 us FreeRTOS tick), `marker` (pin table index, `0xFF` =
none) and `n` channels (1..8). Per sample, for each channel in order: write `reg`
(1 byte, repeated start), read `len` bytes (1..4). `addr7` is checked as in
`ALP_I2C_XFER`. A rejected config leaves the stream unconfigured. `status`: 0
OK, 3 bad period, `n`, address, `len`, marker index, or record too large, 4 no
I2C. The ring is not cleared by CONFIG; START clears it.

`ALP_STREAM_START`: status 0 (also when already running; nothing is reset), 6 not
configured. `ALP_STREAM_STOP` returns after the sampler has parked; the ring keeps
its records so the host can drain them.

`ALP_STREAM_READ` returns as many whole records as fit in one packet
(`(64 - 8) / recsize`), oldest first, and works while running or stopped.
`status` 6 = not configured (`count`, `recsize`, `dropped` = 0). `dropped` counts
samples discarded since START because the ring was full (wraps at 2^32).
`recsize` = 5 + sum of the channel `len`s. Record:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | `timestamp_us` u32, `time_us_32()` at the start of the sample (wraps every 71.6 min) |
| 4 | 1 | `flags`: bit0 marker level (`gpio_get` of the marker pin, 0 without a marker), bit1 an I2C error occurred in this sample (that channel's bytes are 0), bit2 one or more samples were dropped immediately before this record |
| 5 | `recsize` - 5 | channel bytes, concatenated in configuration order, as read from the chip |

**Busy rule:** from START until STOP completes, `ALP_I2C_XFER` answers status 5
and `ALP_I2C_CONFIG` answers `actual_hz` = 0, so the DAP thread never touches the
I2C peripheral while the sampler does. Set the I2C speed with `ALP_I2C_CONFIG`
before starting; set the marker pin to input with `ALP_PIN_SET` first (the sampler
only reads it, never changes its mode).

**Arrangement:** a FreeRTOS task `ALPS` (priority idle+1, pinned to core 0)
sleeps with `xTaskDelayUntil`. The DAP thread runs on core 1, so a busy bus never
delays the drain. It sits below the USB and UART tasks: those preempt a sample
(the timestamp records it) and a saturated bus cannot starve USB. If the sampler
falls behind it resyncs instead of bursting. The ring is 12288 bytes of
fixed-size records (`12288 / recsize` slots), single producer (sampler) and
single consumer (DAP thread), each side writing only its own counter with
memory fences around the hand-off; a full ring drops the new sample and counts it.

**Timing limits.** Bus time per channel is about `30 + 9 * len` SCL clocks
(address+W, reg, repeated start + address+R, `len` data bytes, start/stop), plus
roughly 20 us of driver overhead per channel:

| Setup | Per sample | Realistic max rate |
|---|---:|---:|
| 1 channel x 2 bytes, 400 kHz | 120 + 20 = 140 us | about 7 kHz |
| 2 channels x 2 bytes (e.g. INA236 bus voltage + current), 400 kHz | 240 + 40 = 280 us | about 3.5 kHz |
| 2 channels x 2 bytes, 1 MHz | 96 + 40 = 136 us | about 7 kHz |
| 2 channels x 2 bytes, 100 kHz (boot default) | 960 + 40 = 1000 us | 1 kHz |

The firmware enforces only `period_us` >= 200 (5 kHz); a period shorter than the
sample time just makes the sampler run back to back at that table rate. A 9-byte
record (2 x 2-byte channels) is 6 per packet, so draining 3.5 kHz needs about
600 packets/s (unmeasured on hardware; the 12 KiB ring buffers about 390 ms at
that rate). The monitor's own conversion
time also bounds useful rates (INA236 default 1.1 ms per conversion at 1 average:
set the monitor's ADC config via `ALP_I2C_XFER` first).

**Intended use, per-inference energy:** the target toggles a GPIO around each
inference, wired to a probe table pin; the host sets that pin to input, streams,
and integrates power between marker edges using the timestamps.

**Pins**: `index` is the position in the board pin table (0..`npins`-1), not a
GPIO number. Names are discovered with `ALP_PIN_INFO`; `flags` bit0 = the pin's
boot default is not "released". `status`: 0 OK, 1 bad index (every index on a
board without a table), 2 bad mode (`ALP_PIN_SET` only). On a non-zero status the
remaining response bytes are 0 (`ALP_PIN_INFO`: `flags` = 0, `namelen` = 0).
`level` is the pad level read back after the change.

| `mode` | Meaning |
|---:|---|
| 0 | release: input, hi-Z, no pull |
| 1 | drive low |
| 2 | drive high |
| 3 | input, pull-up |
| 4 | input, pull-down |

Pins boot released (mode 0) unless the table gives a safe default, so the probe
never drives a board strap until the host asks.

### Per-board configuration

I2C is enabled by `PROBE_I2C_INTERFACE`, `PROBE_I2C_SDA`, `PROBE_I2C_SCL` and
(optionally, default 100000) `PROBE_I2C_BAUDRATE` in the board header;
`PROBE_I2C_INTERNAL_PULLUP` additionally enables the RP2040 internal pull-ups for
bare dev boards. The pin table is
`#define PROBE_PIN_TABLE { {gpio, "NAME", default_mode}, ... }`. Names are
1..32 ASCII characters. A board defining neither still builds; I2C commands then
answer status 4 and pin commands status 1.

The I2C pins are checked against the probe's own SWD, reset, UART and LED pins at
build time. Table entries are checked at boot, and an entry on any of those pins,
the I2C pins, a duplicate GPIO, a bad GPIO number, a bad default mode or a bad
name is dropped, so host tooling can never drive the probe's own interfaces.

## Supported boards

| Board header | Target | Channels |
|---|---|---|
| `include/board_pico_config.h` | Raspberry Pi Pico (`-DDEBUG_ON_PICO=ON`) | CDC0 UART0 (GP12 TX / GP13 RX), CDC1 UART1 (GP8 TX / GP9 RX); vendor I2C0 (GP16 SDA / GP17 SCL), pins `DEMO_A` GP20, `DEMO_B` GP21 (dev only) |
| `include/board_debug_probe_config.h` | Raspberry Pi Debug Probe hardware | CDC0 only, UART1 (GP4 TX / GP5 RX); channel 1 disabled (single UART connector); no vendor I2C or pins |

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
