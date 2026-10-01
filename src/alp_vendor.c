/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Alp Lab AB
 */

// CMSIS-DAP vendor commands 0x80..0x9F: raw I2C transfers and named GPIO
// control. Layout is documented in README.md ("Vendor commands").
//
// Threading: DAP_ProcessVendorCommand runs in the DAP thread only. Nothing
// else touches the I2C peripheral or the table pins, so no locking is needed.
// If another user of either ever appears, add a mutex here.

#include <string.h>

#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"

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

#if ALP_HAVE_I2C
// Compile-time: the I2C pins must not collide with the probe's own pins.
_Static_assert(PROBE_I2C_SDA != PROBE_I2C_SCL, "alp_vendor: I2C SDA == SCL");
_Static_assert(!(ALP_RESERVED & ((1 << PROBE_I2C_SDA) | (1 << PROBE_I2C_SCL))),
               "alp_vendor: I2C pin collides with a probe pin");
#endif

// Packet limits (standalone command, not inside DAP_ExecuteCommands).
// Request: cmd, addr, flags, wlen, rlen + wdata. Response: cmd, status + rdata.
#define I2C_REQ_HDR 5U
#define I2C_MAX_LEN (DAP_PACKET_SIZE - I2C_REQ_HDR)
#define I2C_US_PER_BYTE 3000U // generous: 3 ms/byte, i.e. fine down to the 10 kHz floor
#define PIN_NAME_MAX 32U

enum { I2C_OK = 0, I2C_NACK = 1, I2C_TIMEOUT = 2, I2C_BAD_LEN = 3, I2C_NA = 4 };

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
#endif

	// Boot-time check: drop entries that would let the host drive the probe's own
	// pins, bad GPIO numbers, bad modes, duplicates and over-long names.
	for (unsigned i = 0; i < ALP_BOARD_PINS && npins < 255; i++) {
#ifdef PROBE_PIN_TABLE
		const struct alp_pin_def *d = &board_pins[i];
		if (d->gpio >= NUM_BANK0_GPIOS || d->mode >= ALP_PIN_MODE_COUNT || !d->name ||
		    strlen(d->name) > PIN_NAME_MAX || (d->name[0] == 0))
			continue;
		uint32_t bit = 1u << d->gpio;
		if ((bit & (uint32_t)ALP_RESERVED) || (bit & seen))
			continue;
#if ALP_HAVE_I2C
		if (d->gpio == PROBE_I2C_SDA || d->gpio == PROBE_I2C_SCL)
			continue;
#endif
		seen |= bit;
		pins[npins].gpio = d->gpio;
		pins[npins].name = d->name;
		pins[npins].flags = d->mode != ALP_PIN_RELEASE; // bit0: boot default is not "released"
		pin_apply(npins, d->mode);
		npins++;
#endif
	}
}

#if ALP_HAVE_I2C
static uint8_t i2c_status(int rc, size_t want)
{
	if ((size_t)rc == want)
		return I2C_OK;
	return rc == PICO_ERROR_TIMEOUT ? I2C_TIMEOUT : I2C_NACK;
}

static uint32_t cmd_i2c_xfer(const uint8_t *req, uint8_t *resp)
{
	uint8_t addr = req[1], flags = req[2], wlen = req[3], rlen = req[4];
	uint32_t consumed = I2C_REQ_HDR + wlen;
	uint8_t st;

	if (consumed > DAP_PACKET_SIZE)
		consumed = DAP_PACKET_SIZE;
	resp[1] = I2C_BAD_LEN;

	if (addr < 0x08 || addr > 0x77 || (flags & ~1u) || wlen > I2C_MAX_LEN || rlen > I2C_MAX_LEN ||
	    (wlen == 0 && rlen == 0) || ((flags & 1u) && (wlen == 0 || rlen == 0)))
		return (consumed << 16) | 2U;

	bool repeated = flags & 1u;
	st = I2C_OK;
	if (wlen) {
		int rc = i2c_write_timeout_us(PROBE_I2C_INTERFACE, addr, req + I2C_REQ_HDR, wlen, repeated,
		                              (wlen + 1u) * I2C_US_PER_BYTE);
		st = i2c_status(rc, wlen);
	}
	if (st == I2C_OK && rlen) {
		int rc = i2c_read_timeout_us(PROBE_I2C_INTERFACE, addr, resp + 2, rlen, false,
		                             (rlen + 1u) * I2C_US_PER_BYTE);
		st = i2c_status(rc, rlen);
	}
	resp[1] = st;
	return (consumed << 16) | (st == I2C_OK ? 2U + rlen : 2U);
}

static uint32_t cmd_i2c_config(const uint8_t *req, uint8_t *resp)
{
	uint32_t hz = req[1] | (req[2] << 8) | (req[3] << 16) | ((uint32_t)req[4] << 24);
	if (hz < 10000) hz = 10000;
	if (hz > 1000000) hz = 1000000;
	hz = i2c_set_baudrate(PROBE_I2C_INTERFACE, hz);
	resp[1] = hz;
	resp[2] = hz >> 8;
	resp[3] = hz >> 16;
	resp[4] = hz >> 24;
	return (5U << 16) | 5U;
}
#endif

uint32_t DAP_ProcessVendorCommand(const uint8_t *request, uint8_t *response)
{
	response[0] = request[0]; // echo the command ID

	switch (request[0]) {
	case ALP_ID_INFO:
		response[1] = ALP_PROTO_VERSION;
		response[2] = (ALP_HAVE_I2C ? ALP_FEAT_I2C : 0) | (npins ? ALP_FEAT_GPIO : 0);
		response[3] = npins;
		response[4] = ALP_HAVE_I2C ? I2C_MAX_LEN : 0;
		return (1U << 16) | 5U;

	case ALP_ID_I2C_XFER:
#if ALP_HAVE_I2C
		return cmd_i2c_xfer(request, response);
#else
		response[1] = I2C_NA;
		return (MIN(I2C_REQ_HDR + request[3], DAP_PACKET_SIZE) << 16) | 2U;
#endif

	case ALP_ID_I2C_CONFIG:
#if ALP_HAVE_I2C
		return cmd_i2c_config(request, response);
#else
		memset(response + 1, 0, 4); // actual baud 0 == not available
		return (5U << 16) | 5U;
#endif

	case ALP_ID_PIN_SET: {
		uint8_t i = request[1], mode = request[2];
		response[1] = i >= npins ? 1 : mode >= ALP_PIN_MODE_COUNT ? 2 : 0;
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

	default:
		response[0] = ID_DAP_Invalid;
		return (1U << 16) | 1U;
	}
}
