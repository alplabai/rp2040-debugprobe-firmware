/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Alp Lab AB
 */

#ifndef ALP_VENDOR_H_
#define ALP_VENDOR_H_

#include <stdint.h>

#define ALP_PROTO_VERSION 2

#define ALP_ID_INFO       0x80U
#define ALP_ID_I2C_XFER   0x81U
#define ALP_ID_I2C_CONFIG 0x82U
#define ALP_ID_PIN_SET    0x83U
#define ALP_ID_PIN_GET    0x84U
#define ALP_ID_PIN_INFO   0x85U
#define ALP_ID_STREAM_CONFIG 0x86U
#define ALP_ID_STREAM_START  0x87U
#define ALP_ID_STREAM_STOP   0x88U
#define ALP_ID_STREAM_READ   0x89U

#define ALP_FEAT_I2C  (1U << 0)
#define ALP_FEAT_GPIO (1U << 1)
#define ALP_FEAT_STREAM (1U << 2)

// Board pin table entry: PROBE_PIN_TABLE is { {gpio, "NAME", default_mode}, ... }
struct alp_pin_def {
	uint8_t gpio;
	const char *name;
	uint8_t mode; // ALP_PIN_*; 0 (released) unless the board gives a safe default
};

enum {
	ALP_PIN_RELEASE = 0,
	ALP_PIN_LOW = 1,
	ALP_PIN_HIGH = 2,
	ALP_PIN_PULL_UP = 3,
	ALP_PIN_PULL_DOWN = 4,
	ALP_PIN_MODE_COUNT
};

// Validates the board pin table and sets up I2C. Call once before the DAP thread runs.
void alp_vendor_init(void);

// Stop any running stream (USB unmounted). Safe from any task.
void alp_vendor_usb_unmount(void);

#endif
