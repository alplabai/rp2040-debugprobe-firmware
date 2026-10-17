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
The host tool in [`host/`](#host-tool) implements every command below.

All multi-byte values are little-endian. Every response starts with the request
ID. A request is one DAP packet (`DAP_PACKET_SIZE` = 64 bytes), sent standalone.
Unknown IDs in `0x8C`..`0x9F` reply with the single byte `0xFF` (`ID_DAP_Invalid`).

| ID | Name | Request (after ID) | Response (after ID) |
|---:|---|---|---|
| `0x80` | `ALP_INFO` | none | `version` u8 (= 3), `features` u8 (bit0 I2C bus 0, bit1 GPIO, bit2 STREAM, bit3 I2C bus 1, bit4 ADC, bit5 BOARD), `npins` u8, `i2c_max` u8 |
| `0x81` | `ALP_I2C_XFER` | `addr7` u8, `flags` u8, `wlen` u8, `rlen` u8, `wdata[wlen]` | `status` u8, then `rdata[rlen]` only when `status` = 0 |
| `0x82` | `ALP_I2C_CONFIG` | `hz` u32 | `actual_hz` u32 |
| `0x83` | `ALP_PIN_SET` | `index` u8, `mode` u8 | `status` u8, `level` u8 |
| `0x84` | `ALP_PIN_GET` | `index` u8 | `status` u8, `level` u8, `mode` u8 |
| `0x85` | `ALP_PIN_INFO` | `index` u8 | `status` u8, `flags` u8, `namelen` u8, `name[namelen]` ASCII |
| `0x86` | `ALP_STREAM_CONFIG` | `period_us` u32, `marker` u8, `n` u8, then `n` x (`addr7` u8, `reg` u8, `len` u8) | `status` u8 |
| `0x87` | `ALP_STREAM_START` | none | `status` u8 |
| `0x88` | `ALP_STREAM_STOP` | none | `status` u8 (always 0) |
| `0x89` | `ALP_STREAM_READ` | none | `status` u8, `count` u8, `recsize` u8, `dropped` u32, then `count` x record |
| `0x8A` | `ALP_ADC_READ` | `chan` u8 (0..3 = ADC0..ADC3 = GPIO26..29) | `status` u8, `raw` u16, `vref_mv` u16 |
| `0x8B` | `ALP_BOARD` | none | `len` u8, `name[len]` ASCII |

**`ALP_INFO`**: `npins` counts the pins that survived the boot-time check
(below). `i2c_max` is the largest `wlen` and `rlen` accepted (`DAP_PACKET_SIZE`
- 5 = 59), 0 when the board has no I2C. A board with neither I2C nor pins reports
`features` = 0.

**`ALP_I2C_XFER`**: `flags` bit0 = write-then-read with a repeated start; bit1
selects I2C bus 1 (status 4 when the board has none); all other bits must be 0. Without bit0 a write and a read are separate transactions
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

**`ALP_ADC_READ`**: `raw` is the 12-bit mean of 16 conversions, `vref_mv` the
board's ADC reference (`PROBE_ADC_VREF_MV`). `status` 1 = channel not available on
this board (`raw` and `vref_mv` 0). Volts at the pin = `raw` / 4095 x `vref_mv`.

**`ALP_BOARD`**: the board name (`PROBE_BOARD_NAME`, default `generic`). The host
selects its board profile (monitors, pin meanings) from it.

**`ALP_I2C_CONFIG`**: `hz` is clamped to 10000..1000000; the reply is the baud
rate the hardware actually got (`i2c_set_baudrate`), or 0 when I2C is not
available or a stream is running (this reply has no status byte). `hz` bit31
selects bus 1 (bits 0..30 are the frequency); the busy rule applies to bus 0 only. The boot speed is the board's `PROBE_I2C_BAUDRATE` (default 100000).

### Power/energy stream (`0x86`..`0x89`)

Single `ALP_I2C_XFER` round trips (USB poll, host scheduling) are too slow and
jittery to resolve one inference, so the probe samples the I2C monitors itself
and the host drains a timestamped ring. The firmware does no unit conversion:
the host decodes the monitor registers (INA2xx-class or any other I2C chip).

`ALP_STREAM_CONFIG` stops a running stream, then sets: `period_us` (200..10000000,
i.e. 200 us to 10 s; rounded up to the 50 us FreeRTOS tick), `marker` (pin table index, `0xFF` =
none) and `n` channels (1..8). Per sample, for each channel in order: write `reg`
(1 byte, repeated start), read `len` bytes (1..4). `addr7` is checked as in
`ALP_I2C_XFER`. A rejected config leaves the stream unconfigured. `status`: 0
OK, 3 period outside 200..10000000, bad `n`, address, `len`, marker index, or record too large, 4 no
I2C or the sampler task could not be created (INFO then omits the STREAM bit). A
successful CONFIG empties the ring (record size may change); START clears it too.

`ALP_STREAM_START`: status 0 (also when already running; nothing is reset), 6 not
configured. `ALP_STREAM_STOP` wakes the sampler and returns once it has parked (within one
sample, not a full period); a USB unmount also stops the stream; the ring keeps
its records so the host can drain them.

`ALP_STREAM_READ` returns as many whole records as fit in one packet
(`(64 - 8) / recsize`), oldest first, and works while running or stopped.
`status` 6 = not configured (`count`, `recsize`, `dropped` = 0). `dropped` counts
samples discarded since START because the ring was full (wraps at 2^32).
`recsize` = 5 + sum of the channel `len`s. Record:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | `timestamp_us` u32, `time_us_32()` at the start of the sample (wraps every 71.6 min) |
| 4 | 1 | `flags`: bit0 marker level (`gpio_get` of the marker pin, 0 without a marker), bit1 an I2C error occurred in this sample (that channel's bytes are 0), bit2 one or more samples were dropped immediately before this record, bit3 level of the board's `PROBE_STREAM_AUX_GPIO` (0 without one; EVK: current range, 1 = mA) |
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
single consumer (DAP thread), each side writing only its own counter (counters wrap at 2 x slots, so
full and empty stay distinct) with memory fences around the hand-off; a full ring drops the new sample and counts it.

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
boot default is not "released", bit1 = input only (the far end drives the line).
`status`: 0 OK, 1 bad index (every index on a board without a table), 2 bad mode
(`ALP_PIN_SET` only; also drive low/high on an input-only pin). On a non-zero status the
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
`#define PROBE_PIN_TABLE { {gpio, "NAME", default_mode}, ... }`; `default_mode`
`ALP_PIN_IN` marks an input-only pin (boots released, never driven). Names are
1..32 ASCII characters. A board defining neither still builds; I2C commands then
answer status 4 and pin commands status 1.

The I2C pins are checked against the probe's own SWD, reset, UART and LED pins at
build time. Table entries are checked at boot, and an entry on any of those pins,
the I2C pins, a duplicate GPIO, a bad GPIO number, a bad default mode or a bad
name is dropped, so host tooling can never drive the probe's own interfaces.

Other board options: `PROBE_I2C1_INTERFACE`/`_SDA`/`_SCL`/`_BAUDRATE` (bus 1),
`PROBE_ADC_CHANNELS` (bitmask) + `PROBE_ADC_VREF_MV`, `PROBE_STREAM_AUX_GPIO`,
`PROBE_BOARD_NAME`, `PROBE_PADS_1V8` (pad VOLTAGE_SELECT = 1.8 V for IOVDD = 1.8 V),
`PROBE_RELEASE_GPIO_MASK` (inputs with pulls off at boot), `PROBE_RESET_ACTIVE_HIGH`
(reset through an N-FET: GPIO high = nRESET asserted) and `PROBE_SWCLK_MAX_KHZ`.

## Supported boards

| Board header | Target | Channels |
|---|---|---|
| `include/board_pico_config.h` | Raspberry Pi Pico (`-DDEBUG_ON_PICO=ON`) | CDC0 UART0 (GP12 TX / GP13 RX), CDC1 UART1 (GP8 TX / GP9 RX); vendor I2C0 (GP16 SDA / GP17 SCL), pins `DEMO_A` GP20, `DEMO_B` GP21 (dev only) |
| `include/board_debug_probe_config.h` | Raspberry Pi Debug Probe hardware | CDC0 only, UART1 (GP4 TX / GP5 RX); channel 1 disabled (single UART connector); no vendor I2C or pins |

| `include/board_alp_e1m_evk_config.h` | Alp Lab E1M-EVK 2641-R3 (`-DALP_E1M_EVK=ON -DPICO_BOARD=alp_e1m_evk`) | CDC0 SE-UART UART1 (GPIO4 TX / GPIO5 RX), CDC1 console UART0 (GPIO0 TX / GPIO1 RX); I2C bus 0 power monitors, I2C bus 1 module board bus; ADC3 VIO sense; pin table below |

### E1M-EVK 2641-R3

RP2040 U55, IOVDD = 1.8 V (GPIO are not 3.3 V tolerant), flash U54 MX25U1632F
(1.8 V, boot2 `generic_03h`), 12 MHz crystal. SWD, UARTs and `RTC_WAKE_N` reach
the module through U3 TXS0108E (1.8 V to +VIO_SW, enabled by VIO_PG), so SWCLK is
capped at 4 MHz. Board name `alp-e1m-evk-2641-r3`.

| GPIO | Function |
|---:|---|
| 16 / 17 | SWDIO / SWCLK (shared with J2), to the E8 or the CC3501E per `SWD_SEL` |
| 20 | DAP nRESET: Q16 pulls `JTAG_RST` (module E.NSRST, J2.10), or `WIFI_RST` when `SWD_SEL` is high, low when high |
| 4 / 5 | UART1 TX / RX = CDC0 SE-UART (module SEUART on E2.AE3 / E2.AD3) |
| 0 / 1 | UART0 TX / RX = CDC1 app console (module UART0 pads via S7) |
| 24 / 25 | I2C bus 0 SDA / SCL: power monitors on CURR_I2C (switch S6 to the RP2040) |
| 2 / 3 | I2C bus 1 SDA / SCL: module board bus (GreenPAK, RTC, EEPROM) via U14 |
| 29 | ADC3 = VIO_OUT / 2 |
| 21 | status LED (USB connected) |
| 27 | `SWD_SEL`: U57 TMUX1574 routes SWD and DAP nRESET to the E8 (low, default) or the CC3501E (high) |
| 9 | `I2C0_LINK_N`: low joins I2C bus 0 to module I2C0 through U58 (module EEPROM U18 at 0x50) |
| 19 | `MARKER`: inference marker from the E8 (E2.AG6) |
| 18 | `WIFI_LOGGER`: E8 TDO (SWD_SEL low) or CC3501E LOGGER (SWD_SEL high, reads 1 when the CC3501E is powered) |

Pin table (index, name, GPIO, meaning). All boot released; every FET gate has an
external pull-down, so released = FET off.

| # | Name | GPIO | Meaning |
|---:|---|---:|---|
| 0..3 | `BOOT0`..`BOOT3` | 13, 12, 11, 10 | module boot straps via 10k (not connected on AEN R3) |
| 4 | `MCU_RST_RP` | 8 | high = Q17 pulls `MCU_RST` (E.POR_N) low |
| 5 | `I2C0_LINK_N` | 9 | low = RP I2C bus 0 joined to module I2C0 (released = apart, R304 pull-up) |
| 6 | `MODULE_EN_RP` | 23 | high = Q18 pulls `MODULE_EN` low: module OFF |
| 7 | `MODULE_STBY_RP` | 22 | high = Q15 pulls `MODULE_STBY` low |
| 8 | `RTC_WAKE_N` | 7 | input only: module RTC wake (open-drain) |
| 9 | `CURR_SEL` | 6 | high then release = select the 5 V module uA range |
| 10 | `CURR_STATUS` | 26 | input only: 1 = mA range, 0 = uA range (also stream flag bit3) |
| 11 | `CURR_ALERT_N` | 28 | input only: U29/U53 INA228 ALERT |
| 12 | `PD_ACTIVE_N` | 14 | input only: 0 = USB-PD contract powers the board |
| 13 | `PWR_FLT_N` | 15 | input only: 0 = eFuse U9/U12 fault |
| 14 | `SWD_SEL` | 27 | high = SWD and DAP nRESET to the CC3501E (released = E8, R300 pull-down) |
| 15 | `MARKER` | 19 | input only: inference marker from the E8 |
| 16 | `WIFI_LOGGER` | 18 | input only: CC3501E LOGGER while `SWD_SEL` is high |

The 5 V module current range returns from uA to mA only by the hardware trip
(about 4 mA through R278) or a power cycle; there is no firmware path back.
This pin map is the 2641-R3 carrier with the RP2040 provisioning additions
(U57 SWD mux, U58 I2C0 link, marker on E2.AG6); earlier pd exports lack them.

**USB serial number:** U54 (Macronix) may not implement the `0x4B` unique-ID
command, so the EVK build (`PROBE_SERIAL_IN_FLASH`) keeps a random 64-bit serial
in the last flash sector (`0x1FF000`, record `ALPS`, version 1, serial, CRC-32),
created on first boot. A UF2 update keeps it; a full chip erase gives a new one.

**Warning:** `MODULE_EN_RP`, `MCU_RST_RP` and DAP nRESET power off or reset the
module. I2C bus 1 is shared with the module SoC; use it only while the SoC is held
in reset or idle on that bus.

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

# Alp Lab E1M EVK -> build-evk/debugprobe_alp_e1m_evk.uf2
cmake -G Ninja -S . -B build-evk -DALP_E1M_EVK=ON -DPICO_BOARD=alp_e1m_evk && ninja -C build-evk
```

All targets must build warning-free (`-Wall`). Load a `.uf2` through the RP2040
UF2 bootloader.

## Host tool

`host/` is the Python host tool (`alp-probe`), used from a shell and from VS Code:

```sh
pip install -e host            # pyusb + pyserial; tests: pip install -e "host[test]" && pytest host
alp-probe info                 # probe, firmware, board
alp-probe ports                # SE-UART and app console serial devices
alp-probe power                # one reading of every monitor on the board
alp-probe range ua             # 5 V module uA range
alp-probe stream --seconds 5 --marker NAME --out run.csv   # streamed 5 V module power, energy summary
alp-probe pin list | reset pulse | por pulse | module off | i2c 0x52 --bus 1 --read 8 | vio
alp-probe eeprom dump --out som.bin   # module EEPROM (raw bytes); holds the SoC in POR meanwhile
alp-probe swd-target cc35|e8|status   # route SWD to the CC3501E (only when it is powered) or the E8
alp-probe cc35 info                   # read-only CC3501E Config-AP: IDs, boot diag, lifecycle
```

Every command takes `--json` and then prints exactly one JSON object, errors
included (`{"ok": false, "error": ...}`, non-zero exit). `--serial` selects a
probe. Current is the shunt voltage divided by the board's shunt value; register
LSBs follow TI SBOSA81D (INA236) and SLYS021A (INA228). `stream` sets the INA228
ADC timing for the period and restores the power-on `ADC_CONFIG` afterwards. On
Linux the probe needs a udev rule for `2e8a:000c`.

## Flashing the EVK RP2040

1. Build `build-evk/debugprobe_alp_e1m_evk.uf2` (above), or take it from the CI
   `uf2` artifact or a release.
2. Hold S2 (BOOTSEL) and press S3 (RUN), or fit JP7, or plug USB with S2 held.
   The RP2040 enumerates as the `RPI-RP2` drive (USB `2e8a:0003`).
3. Copy the `.uf2` to the drive (or `picotool load -x debugprobe_alp_e1m_evk.uf2`).
   Remove JP7 if fitted. The probe re-enumerates as `2e8a:000c` with the CMSIS-DAP
   interface and the two CDC ports.
4. On Linux install `tools/99-alp-probe.rules`, then `pip install -e host` and
   run `alp-probe info` (board `alp-e1m-evk-2641-r3`, protocol 3).

The board must be powered from the barrel jack or a USB-PD source for the module
side to work; the RP2040 itself runs from the board +3V3.

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
has not been verified on RP2040 hardware yet. The E1M EVK target builds but has
not run on an EVK 2641-R3 yet. To check on first boards: the USB serial number
(derived from the flash unique ID, command `0x4B`; if U54 does not support it,
every EVK probe reports the same serial) and the 12 MHz crystal start-up.
