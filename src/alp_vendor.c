/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Alp Lab AB
 */

// CMSIS-DAP vendor commands 0x80..0x9F: raw I2C transfers, named GPIO
// control, ADC and board name. Layout is documented in README.md ("Vendor commands").
//
// Threading: DAP_ProcessVendorCommand runs in the DAP thread (core 1). The only
// other user of I2C bus 0 is the stream sampler task (core 0, see "Stream");
// the two are mutually exclusive: while a stream runs 0x81/0x82 on bus 0
// answer busy, and STOP/CONFIG wait until the sampler has parked. Bus 1 and the
// ADC are used by the DAP thread only. Table pins and PROBE_STREAM_AUX_GPIO are
// only read (gpio_get) by the sampler.

#include <string.h>

#include "pico/stdlib.h"
#include "FreeRTOS.h"
#include "task.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/adc.h"

#include "DAP_config.h"
#include "DAP.h"
#include "probe_config.h"
#include "alp_vendor.h"

#if defined(PROBE_I2C_INTERFACE) && defined(PROBE_I2C_SDA) && defined(PROBE_I2C_SCL)
#define ALP_HAVE_I2C 1
#ifndef PROBE_I2C_BAUDRATE
#define PROBE_I2C_BAUDRATE 100000
#endif
#else
#define ALP_HAVE_I2C 0
#endif

#if defined(PROBE_I2C1_INTERFACE) && defined(PROBE_I2C1_SDA) && defined(PROBE_I2C1_SCL)
#define ALP_HAVE_I2C1 1
#ifndef PROBE_I2C1_BAUDRATE
#define PROBE_I2C1_BAUDRATE 100000
#endif
#else
#define ALP_HAVE_I2C1 0
#endif

#ifndef PROBE_BOARD_NAME
#define PROBE_BOARD_NAME "generic"
#endif

// PROBE_ADC_CHANNELS: bit n = ADCn (GPIO ADC_BASE_PIN + n) is available.
#ifndef PROBE_ADC_CHANNELS
#define PROBE_ADC_CHANNELS 0
#define PROBE_ADC_VREF_MV 0
#endif
#ifndef PROBE_ADC_VREF_MV
#error "alp_vendor: PROBE_ADC_CHANNELS needs PROBE_ADC_VREF_MV"
#endif
_Static_assert((PROBE_ADC_CHANNELS) <= 0xF, "alp_vendor: PROBE_ADC_CHANNELS has bits beyond ADC3");

#ifdef PROBE_PIN_TABLE
static const struct alp_pin_def board_pins[] = PROBE_PIN_TABLE;
#define ALP_BOARD_PINS (sizeof(board_pins) / sizeof(board_pins[0]))
#else
#define ALP_BOARD_PINS 0
#endif

// GPIOs the probe uses for itself. Table pins must never overlap these.
// Bit masks fit an int, so GPIO numbers must stay below 31.
_Static_assert(NUM_BANK0_GPIOS <= 31, "alp_vendor: reserved mask needs <= 31 GPIOs");
enum {
	ALP_RESERVED = 0
#ifdef PROBE_PIN_SWCLK
	| (1 << PROBE_PIN_SWCLK)
#endif
#ifdef PROBE_PIN_SWDIO
	| (1 << PROBE_PIN_SWDIO)
#endif
#ifdef PROBE_PIN_SWDI
	| (1 << PROBE_PIN_SWDI)
#endif
#ifdef PROBE_PIN_SWDIOEN
	| (1 << PROBE_PIN_SWDIOEN)
#endif
#ifdef PROBE_PIN_RESET
	| (1 << PROBE_PIN_RESET)
#endif
#ifdef PROBE_UART_TX
	| (1 << PROBE_UART_TX) | (1 << PROBE_UART_RX)
#endif
#ifdef PROBE_UART1_INTERFACE
	| (1 << PROBE_UART1_TX) | (1 << PROBE_UART1_RX)
#endif
#ifdef PROBE_USB_CONNECTED_LED
	| (1 << PROBE_USB_CONNECTED_LED)
#endif
#ifdef PROBE_DAP_CONNECTED_LED
	| (1 << PROBE_DAP_CONNECTED_LED)
#endif
#ifdef PROBE_DAP_RUNNING_LED
	| (1 << PROBE_DAP_RUNNING_LED)
#endif
#ifdef PROBE_UART_RX_LED
	| (1 << PROBE_UART_RX_LED)
#endif
#ifdef PROBE_UART_TX_LED
	| (1 << PROBE_UART_TX_LED)
#endif
};

// Pins owned by the vendor peripherals. Table pins must not overlap these either.
enum {
	ALP_I2C0_PINS = 0
#if ALP_HAVE_I2C
	| (1 << PROBE_I2C_SDA) | (1 << PROBE_I2C_SCL)
#endif
	,
	ALP_I2C1_PINS = 0
#if ALP_HAVE_I2C1
	| (1 << PROBE_I2C1_SDA) | (1 << PROBE_I2C1_SCL)
#endif
	,
	ALP_ADC_PINS = (PROBE_ADC_CHANNELS) << ADC_BASE_PIN,
};

// Compile-time: the I2C and ADC pins must not collide with the probe's own pins
// or with each other.
#if ALP_HAVE_I2C
_Static_assert(PROBE_I2C_SDA != PROBE_I2C_SCL, "alp_vendor: I2C SDA == SCL");
_Static_assert(!(ALP_RESERVED & ALP_I2C0_PINS), "alp_vendor: I2C pin collides with a probe pin");
#endif
#if ALP_HAVE_I2C1
_Static_assert(PROBE_I2C1_SDA != PROBE_I2C1_SCL, "alp_vendor: I2C1 SDA == SCL");
_Static_assert(!((ALP_RESERVED | ALP_I2C0_PINS) & ALP_I2C1_PINS),
               "alp_vendor: I2C1 pin collides with a probe or I2C pin");
#endif
_Static_assert(!((ALP_RESERVED | ALP_I2C0_PINS | ALP_I2C1_PINS) & ALP_ADC_PINS),
               "alp_vendor: ADC channel GPIO collides with a probe or I2C pin");
#ifdef PROBE_RELEASE_GPIO_MASK
_Static_assert(!((ALP_RESERVED | ALP_I2C0_PINS | ALP_I2C1_PINS | ALP_ADC_PINS) & (PROBE_RELEASE_GPIO_MASK)),
               "alp_vendor: PROBE_RELEASE_GPIO_MASK covers a probe, I2C or ADC pin");
#endif
#ifdef PROBE_STREAM_AUX_GPIO
// Only read by the sampler, so it may be a table pin; ADC pins have the digital input off.
_Static_assert(PROBE_STREAM_AUX_GPIO < NUM_BANK0_GPIOS && !(ALP_ADC_PINS & (1 << PROBE_STREAM_AUX_GPIO)),
               "alp_vendor: bad PROBE_STREAM_AUX_GPIO");
#endif
_Static_assert(sizeof(PROBE_BOARD_NAME) - 1 <= DAP_PACKET_SIZE - 2, "alp_vendor: PROBE_BOARD_NAME too long");

// Packet limits (standalone command, not inside DAP_ExecuteCommands).
// Request: cmd, addr, flags, wlen, rlen + wdata. Response: cmd, status + rdata.
#define I2C_REQ_HDR 5U
#define I2C_MAX_LEN (DAP_PACKET_SIZE - I2C_REQ_HDR)
#define I2C_US_PER_BYTE 3000U // generous: 3 ms/byte, i.e. fine down to the 10 kHz floor
#define PIN_NAME_MAX 32U

enum { I2C_OK = 0, I2C_NACK = 1, I2C_TIMEOUT = 2, I2C_BAD_LEN = 3, I2C_NA = 4, I2C_BUSY = 5, STREAM_NOCFG = 6 };

// Validated, compacted copy of the board table: the wire index is the index here.
static struct {
	uint8_t gpio;
	uint8_t mode;
	const char *name;
	uint8_t flags;
} pins[ALP_BOARD_PINS + 1];
static uint8_t npins;
#ifdef PROBE_PIN_TABLE
static uint32_t seen; // GPIOs already in the compacted table
#endif

static void pin_apply(uint8_t i, uint8_t mode)
{
	uint g = pins[i].gpio;

	switch (mode) {
	case ALP_PIN_LOW:
	case ALP_PIN_HIGH:
		gpio_disable_pulls(g);
		gpio_put(g, mode == ALP_PIN_HIGH); // preload the level before enabling the driver
		gpio_set_dir(g, GPIO_OUT);
		break;
	case ALP_PIN_PULL_UP:
		gpio_set_dir(g, GPIO_IN);
		gpio_pull_up(g);
		break;
	case ALP_PIN_PULL_DOWN:
		gpio_set_dir(g, GPIO_IN);
		gpio_pull_down(g);
		break;
	default:
		gpio_set_dir(g, GPIO_IN);
		gpio_disable_pulls(g);
		break;
	}
	gpio_set_function(g, GPIO_FUNC_SIO);
	pins[i].mode = mode;
}

#if ALP_HAVE_I2C
static void stream_init(void);
static bool stream_ok;
#define STREAM_AVAILABLE stream_ok
#else
#define STREAM_AVAILABLE 0
#endif

void alp_vendor_init(void)
{
#if ALP_HAVE_I2C
	i2c_init(PROBE_I2C_INTERFACE, PROBE_I2C_BAUDRATE);
	gpio_set_function(PROBE_I2C_SDA, GPIO_FUNC_I2C);
	gpio_set_function(PROBE_I2C_SCL, GPIO_FUNC_I2C);
#ifdef PROBE_I2C_INTERNAL_PULLUP // dev boards without external pull-ups only
	gpio_pull_up(PROBE_I2C_SDA);
	gpio_pull_up(PROBE_I2C_SCL);
#endif
	stream_init();
#endif
#if ALP_HAVE_I2C1
	i2c_init(PROBE_I2C1_INTERFACE, PROBE_I2C1_BAUDRATE);
	gpio_set_function(PROBE_I2C1_SDA, GPIO_FUNC_I2C);
	gpio_set_function(PROBE_I2C1_SCL, GPIO_FUNC_I2C);
#endif
#if PROBE_ADC_CHANNELS
	adc_init();
	for (unsigned c = 0; c < 4; c++)
		if ((PROBE_ADC_CHANNELS) & (1u << c))
			adc_gpio_init(ADC_BASE_PIN + c); // input buffer and pulls off
#endif
#if defined(PROBE_PIN_RESET) && defined(PROBE_RESET_ACTIVE_HIGH)
	// Push-pull reset is a SIO output from boot (low = released), so
	// DAP_SWJ_Pins drives it without a DAP_Connect.
	gpio_init(PROBE_PIN_RESET);
	gpio_set_dir(PROBE_PIN_RESET, GPIO_OUT);
#endif
#ifdef PROBE_RELEASE_GPIO_MASK
	// Board-connected inputs nobody claims: input, pulls off (the reset default
	// pull-down would load the board's pull-ups).
	for (unsigned g = 0; g < NUM_BANK0_GPIOS; g++) {
		if ((PROBE_RELEASE_GPIO_MASK) & (1u << g)) {
			gpio_init(g);
			gpio_disable_pulls(g);
		}
	}
#endif

	// Boot-time check: drop entries that would let the host drive the probe's own
	// pins, bad GPIO numbers, bad modes, duplicates and over-long names.
	for (unsigned i = 0; i < ALP_BOARD_PINS && npins < 255; i++) {
#ifdef PROBE_PIN_TABLE
		const struct alp_pin_def *d = &board_pins[i];
		bool in_only = d->mode == ALP_PIN_IN;
		uint8_t mode = in_only ? ALP_PIN_RELEASE : d->mode;
		if (d->gpio >= NUM_BANK0_GPIOS || mode >= ALP_PIN_MODE_COUNT || !d->name ||
		    strlen(d->name) > PIN_NAME_MAX || (d->name[0] == 0))
			continue;
		uint32_t bit = 1u << d->gpio;
		if ((bit & (uint32_t)(ALP_RESERVED | ALP_I2C0_PINS | ALP_I2C1_PINS | ALP_ADC_PINS)) ||
		    (bit & seen))
			continue;
		seen |= bit;
		pins[npins].gpio = d->gpio;
		pins[npins].name = d->name;
		// bit0: boot default is not "released"; bit1: input only
		pins[npins].flags = (mode != ALP_PIN_RELEASE) | (in_only << 1);
		pin_apply(npins, mode);
		npins++;
#endif
	}
}

// flags bit1 (0x81) / hz bit31 (0x82) select bus 1. NULL: that bus is not on this board.
static i2c_inst_t *i2c_bus(bool bus1)
{
	if (bus1) {
#if ALP_HAVE_I2C1
		return PROBE_I2C1_INTERFACE;
#endif
	} else {
#if ALP_HAVE_I2C
		return PROBE_I2C_INTERFACE;
#endif
	}
	return NULL;
}

static uint8_t i2c_status(int rc, size_t want)
{
	if ((size_t)rc == want)
		return I2C_OK;
	return rc == PICO_ERROR_TIMEOUT ? I2C_TIMEOUT : I2C_NACK;
}

static uint32_t cmd_i2c_xfer(const uint8_t *req, uint8_t *resp, i2c_inst_t *bus)
{
	uint8_t addr = req[1], flags = req[2], wlen = req[3], rlen = req[4];
	uint32_t consumed = I2C_REQ_HDR + wlen;
	uint8_t st;

	if (consumed > DAP_PACKET_SIZE)
		consumed = DAP_PACKET_SIZE;
	resp[1] = I2C_BAD_LEN;

	if (addr < 0x08 || addr > 0x77 || (flags & ~3u) || wlen > I2C_MAX_LEN || rlen > I2C_MAX_LEN ||
	    (wlen == 0 && rlen == 0) || ((flags & 1u) && (wlen == 0 || rlen == 0)))
		return (consumed << 16) | 2U;

	bool repeated = flags & 1u;
	st = I2C_OK;
	if (wlen) {
		int rc = i2c_write_timeout_us(bus, addr, req + I2C_REQ_HDR, wlen, repeated,
		                              (wlen + 1u) * I2C_US_PER_BYTE);
		st = i2c_status(rc, wlen);
	}
	if (st == I2C_OK && rlen) {
		int rc = i2c_read_timeout_us(bus, addr, resp + 2, rlen, false,
		                             (rlen + 1u) * I2C_US_PER_BYTE);
		st = i2c_status(rc, rlen);
	}
	resp[1] = st;
	return (consumed << 16) | (st == I2C_OK ? 2U + rlen : 2U);
}

static uint32_t cmd_i2c_config(uint32_t hz, uint8_t *resp, i2c_inst_t *bus)
{
	if (hz < 10000) hz = 10000;
	if (hz > 1000000) hz = 1000000;
	hz = i2c_set_baudrate(bus, hz);
	resp[1] = hz;
	resp[2] = hz >> 8;
	resp[3] = hz >> 16;
	resp[4] = hz >> 24;
	return (5U << 16) | 5U;
}

// Mean of 16 conversions. Response: status, raw u16, vref_mv u16.
static uint32_t cmd_adc_read(const uint8_t *req, uint8_t *resp)
{
	uint8_t ch = req[1];

	memset(resp + 1, 0, 5);
	resp[1] = 1; // channel not available
	if (ch < 4 && ((PROBE_ADC_CHANNELS) & (1u << ch))) {
		uint32_t sum = 0;
		adc_select_input(ch);
		for (unsigned i = 0; i < 16; i++)
			sum += adc_read();
		sum = (sum + 8U) / 16U;
		resp[1] = 0;
		resp[2] = sum;
		resp[3] = sum >> 8;
		resp[4] = (PROBE_ADC_VREF_MV) & 0xFF;
		resp[5] = (PROBE_ADC_VREF_MV) >> 8;
	}
	return (2U << 16) | 6U;
}

#if ALP_HAVE_I2C
// ---- Stream: firmware-side I2C sampler ---------------------------------------
// One task (core 0, priority idle+1: below TUD/UART so USB is never starved,
// timestamps record any jitter) wakes every period_us (rounded up to the 50 us
// FreeRTOS tick), reads every channel and pushes one fixed-size record into a
// single-producer (sampler) / single-consumer (DAP thread) ring.
//   record = ts u32 (time_us_32 at sample start), flags u8, channel bytes
// The ring indices are free-running counters; each side writes only its own.
// Producer: write record, release fence, head++. Consumer: read head, acquire
// fence, copy, release fence, tail++. A full ring drops the new sample.
#define STREAM_RING_BYTES 12288U
#define STREAM_MIN_PERIOD_US 200U
#define STREAM_MAX_PERIOD_US 10000000U
#define STREAM_MAX_CHAN 8U
#define STREAM_REC_HDR 5U
#define STREAM_RESP_HDR 8U
#define STREAM_MARKER_NONE 0xFFU
#define STREAM_F_MARKER 1U
#define STREAM_F_I2C_ERR 2U
#define STREAM_F_OVERFLOW 4U
#define STREAM_F_AUX 8U

static struct {
	uint8_t addr, reg, len;
} chan[STREAM_MAX_CHAN];
static uint8_t nchan, marker_gpio, rec_size, configured;
static uint32_t period_ticks, nslots;
static uint8_t ring[STREAM_RING_BYTES] __attribute__((aligned(4)));
static volatile uint32_t ring_head, ring_tail; // record counters, always in [0, 2 * nslots)
static volatile uint32_t dropped;              // written by the sampler only
static volatile uint8_t run_req;               // set by the DAP thread; cleared by it or on USB unmount
static volatile uint8_t sampler_active;        // written by the sampler only
static TaskHandle_t sampler_task;


static bool stream_busy(void)
{
	return run_req || sampler_active;
}

// USB unmounted (host gone or crashed): stop sampling. The sampler parks itself.
void alp_vendor_usb_unmount(void)
{
	run_req = 0;
}

static void stream_stop(void)
{
	run_req = 0;
	__mem_fence_release();
	xTaskNotifyGive(sampler_task); // cut the sampler's inter-sample wait short
	while (sampler_active) // it parks within one sample, bounded by the I2C timeouts
		vTaskDelay(1);
}

// Ring counters wrap at 2 * nslots, so "full" (used == nslots) and "empty"
// stay distinguishable and a non-power-of-two nslots is safe.
static uint32_t ring_used(uint32_t head, uint32_t tail)
{
	return (head + 2U * nslots - tail) % (2U * nslots);
}

static uint32_t ring_slot(uint32_t idx)
{
	return idx >= nslots ? idx - nslots : idx;
}

static void sample_once(uint8_t *rec, bool overflow)
{
	uint8_t flags = overflow ? STREAM_F_OVERFLOW : 0, *p = rec + STREAM_REC_HDR;
	uint32_t ts = time_us_32();

	if (marker_gpio != STREAM_MARKER_NONE && gpio_get(marker_gpio))
		flags |= STREAM_F_MARKER;
#ifdef PROBE_STREAM_AUX_GPIO
	if (gpio_get(PROBE_STREAM_AUX_GPIO))
		flags |= STREAM_F_AUX;
#endif
	for (unsigned i = 0; i < nchan; i++) {
		bool ok = i2c_write_timeout_us(PROBE_I2C_INTERFACE, chan[i].addr, &chan[i].reg, 1, true,
		                               2U * I2C_US_PER_BYTE) == 1 &&
		          i2c_read_timeout_us(PROBE_I2C_INTERFACE, chan[i].addr, p, chan[i].len, false,
		                              (chan[i].len + 1U) * I2C_US_PER_BYTE) == chan[i].len;
		if (!ok) {
			memset(p, 0, chan[i].len);
			flags |= STREAM_F_I2C_ERR;
		}
		p += chan[i].len;
	}
	memcpy(rec, &ts, 4); // RP2040 is little-endian
	rec[4] = flags;
}

static void sampler_thread(void *arg)
{
	(void)arg;
	for (;;) {
		ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
		sampler_active = 1;
		__mem_fence_acquire(); // config was written before START notified us
		bool overflow = false;
		TickType_t last = xTaskGetTickCount();
		while (run_req) {
			uint32_t head = ring_head;
			if (ring_used(head, ring_tail) >= nslots) {
				dropped++;
				overflow = true;
			} else {
				sample_once(&ring[ring_slot(head) * rec_size], overflow);
				overflow = false;
				__mem_fence_release();
				ring_head = (head + 1U) % (2U * nslots);
			}
			// Wait out the period, but wake early on STOP (notification). Behind
			// schedule (preempted by USB): resync instead of bursting.
			TickType_t next = last + period_ticks, now = xTaskGetTickCount();
			if ((int32_t)(next - now) <= 0) {
				last = now;
				continue;
			}
			while (run_req && (int32_t)(next - now) > 0) {
				ulTaskNotifyTake(pdTRUE, next - now);
				now = xTaskGetTickCount();
			}
			last = next;
		}
		sampler_active = 0;
	}
}

static void stream_init(void)
{
	if (xTaskCreate(sampler_thread, "ALPS", 384, NULL, tskIDLE_PRIORITY + 1, &sampler_task) != pdPASS)
		return; // no stream: INFO omits the feature bit and the commands answer status 4
	stream_ok = true;
#if (configNUMBER_OF_CORES > 1)
	vTaskCoreAffinitySet(sampler_task, 1 << 0); // core 0, away from the DAP thread (core 1)
#endif
}

static uint32_t cmd_stream_config(const uint8_t *req, uint8_t *resp)
{
	uint32_t period = req[1] | (req[2] << 8) | (req[3] << 16) | ((uint32_t)req[4] << 24);
	uint8_t marker = req[5], n = req[6];
	uint32_t consumed = 7U + 3U * n, size = STREAM_REC_HDR;

	if (consumed > DAP_PACKET_SIZE)
		consumed = DAP_PACKET_SIZE;
	if (!stream_ok) {
		resp[1] = I2C_NA;
		return (consumed << 16) | 2U;
	}
	stream_stop();
	configured = 0;
	resp[1] = I2C_BAD_LEN;
	if (period < STREAM_MIN_PERIOD_US || period > STREAM_MAX_PERIOD_US || n < 1 || n > STREAM_MAX_CHAN || 7U + 3U * n > DAP_PACKET_SIZE ||
	    (marker != STREAM_MARKER_NONE && marker >= npins))
		return (consumed << 16) | 2U;
	for (unsigned i = 0; i < n; i++) {
		const uint8_t *c = req + 7 + 3 * i;
		if (c[0] < 0x08 || c[0] > 0x77 || c[2] < 1 || c[2] > 4)
			return (consumed << 16) | 2U;
		size += c[2];
	}
	if (size > DAP_PACKET_SIZE - STREAM_RESP_HDR)
		return (consumed << 16) | 2U;
	for (unsigned i = 0; i < n; i++) {
		chan[i].addr = req[7 + 3 * i];
		chan[i].reg = req[8 + 3 * i];
		chan[i].len = req[9 + 3 * i];
	}
	nchan = n;
	marker_gpio = marker == STREAM_MARKER_NONE ? STREAM_MARKER_NONE : pins[marker].gpio;
	rec_size = size;
	nslots = STREAM_RING_BYTES / size;
	period_ticks = ((uint64_t)period * configTICK_RATE_HZ + 999999U) / 1000000U; // round up
	ring_head = ring_tail = dropped = 0; // old records have the old framing
	configured = 1;
	resp[1] = I2C_OK;
	return (consumed << 16) | 2U;
}

static uint32_t cmd_stream_start(uint8_t *resp)
{
	if (!stream_ok) {
		resp[1] = I2C_NA;
	} else if (!configured) {
		resp[1] = STREAM_NOCFG;
	} else {
		resp[1] = I2C_OK;
		if (!stream_busy()) { // already running: no-op, keep the data
			ring_head = ring_tail = dropped = 0;
			run_req = 1;
			__mem_fence_release();
			xTaskNotifyGive(sampler_task);
		}
	}
	return (1U << 16) | 2U;
}

static uint32_t cmd_stream_read(uint8_t *resp)
{
	memset(resp + 1, 0, STREAM_RESP_HDR - 1);
	if (!stream_ok || !configured) {
		resp[1] = stream_ok ? STREAM_NOCFG : I2C_NA;
		return (1U << 16) | STREAM_RESP_HDR;
	}
	uint32_t tail = ring_tail, head = ring_head;
	__mem_fence_acquire();
	uint32_t n = MIN(ring_used(head, tail), (DAP_PACKET_SIZE - STREAM_RESP_HDR) / rec_size);
	uint32_t d = dropped;
	for (uint32_t i = 0; i < n; i++)
		memcpy(resp + STREAM_RESP_HDR + i * rec_size, &ring[ring_slot((tail + i) % (2U * nslots)) * rec_size], rec_size);
	__mem_fence_release();
	ring_tail = (tail + n) % (2U * nslots);
	resp[2] = n;
	resp[3] = rec_size;
	memcpy(resp + 4, &d, 4);
	return (1U << 16) | (STREAM_RESP_HDR + n * rec_size);
}
#endif

#if !ALP_HAVE_I2C
void alp_vendor_usb_unmount(void)
{
}
#endif

// The stream sampler owns bus 0 while it runs.
static bool i2c_bus_busy(i2c_inst_t *bus)
{
#if ALP_HAVE_I2C
	return bus == PROBE_I2C_INTERFACE && stream_busy();
#else
	(void)bus;
	return false;
#endif
}

uint32_t DAP_ProcessVendorCommand(const uint8_t *request, uint8_t *response)
{
	response[0] = request[0]; // echo the command ID

	switch (request[0]) {
	case ALP_ID_INFO:
		response[1] = ALP_PROTO_VERSION;
		response[2] = (ALP_HAVE_I2C ? ALP_FEAT_I2C : 0) | (npins ? ALP_FEAT_GPIO : 0) |
		              (STREAM_AVAILABLE ? ALP_FEAT_STREAM : 0) | (ALP_HAVE_I2C1 ? ALP_FEAT_I2C1 : 0) |
		              (PROBE_ADC_CHANNELS ? ALP_FEAT_ADC : 0) | ALP_FEAT_BOARD;
		response[3] = npins;
		response[4] = (ALP_HAVE_I2C || ALP_HAVE_I2C1) ? I2C_MAX_LEN : 0;
		return (1U << 16) | 5U;

	case ALP_ID_I2C_XFER: {
		i2c_inst_t *bus = i2c_bus(request[2] & 2u);
		if (!bus || i2c_bus_busy(bus)) {
			response[1] = bus ? I2C_BUSY : I2C_NA;
			return (MIN(I2C_REQ_HDR + request[3], DAP_PACKET_SIZE) << 16) | 2U;
		}
		return cmd_i2c_xfer(request, response, bus);
	}

	case ALP_ID_I2C_CONFIG: {
		uint32_t hz = request[1] | (request[2] << 8) | (request[3] << 16) | ((uint32_t)request[4] << 24);
		i2c_inst_t *bus = i2c_bus(hz >> 31);
		if (!bus || i2c_bus_busy(bus)) { // no status byte in this reply: actual_hz 0 == unavailable
			memset(response + 1, 0, 4);
			return (5U << 16) | 5U;
		}
		return cmd_i2c_config(hz & 0x7FFFFFFFU, response, bus);
	}

	case ALP_ID_ADC_READ:
		return cmd_adc_read(request, response);

	case ALP_ID_BOARD: {
		size_t n = sizeof(PROBE_BOARD_NAME) - 1;
		response[1] = n;
		memcpy(response + 2, PROBE_BOARD_NAME, n);
		return (1U << 16) | (2U + n);
	}

	case ALP_ID_PIN_SET: {
		uint8_t i = request[1], mode = request[2];
		response[1] = i >= npins ? 1 :
			      mode >= ALP_PIN_MODE_COUNT ||
				      ((pins[i].flags & 2u) && (mode == ALP_PIN_LOW || mode == ALP_PIN_HIGH)) ? 2 : 0;
		response[2] = 0;
		if (response[1] == 0) {
			pin_apply(i, mode);
			response[2] = gpio_get(pins[i].gpio);
		}
		return (3U << 16) | 3U;
	}

	case ALP_ID_PIN_GET: {
		uint8_t i = request[1];
		response[1] = i >= npins ? 1 : 0;
		response[2] = response[3] = 0;
		if (response[1] == 0) {
			response[2] = gpio_get(pins[i].gpio);
			response[3] = pins[i].mode;
		}
		return (2U << 16) | 4U;
	}

	case ALP_ID_PIN_INFO: {
		uint8_t i = request[1];
		response[1] = i >= npins ? 1 : 0;
		response[2] = response[3] = 0;
		if (response[1])
			return (2U << 16) | 4U;
		size_t n = strlen(pins[i].name);
		response[2] = pins[i].flags;
		response[3] = n;
		memcpy(response + 4, pins[i].name, n);
		return (2U << 16) | (4U + n);
	}

#if ALP_HAVE_I2C
	case ALP_ID_STREAM_CONFIG:
		return cmd_stream_config(request, response);
	case ALP_ID_STREAM_START:
		return cmd_stream_start(response);
	case ALP_ID_STREAM_STOP:
		stream_stop();
		response[1] = I2C_OK;
		return (1U << 16) | 2U;
	case ALP_ID_STREAM_READ:
		return cmd_stream_read(response);
#else
	case ALP_ID_STREAM_CONFIG:
	case ALP_ID_STREAM_START:
	case ALP_ID_STREAM_STOP:
	case ALP_ID_STREAM_READ:
		response[1] = I2C_NA;
		return (1U << 16) | 2U;
#endif

	default:
		response[0] = ID_DAP_Invalid;
		return (1U << 16) | 1U;
	}
}
