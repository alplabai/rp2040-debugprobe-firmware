/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2023 Raspberry Pi (Trading) Ltd.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 */

#ifndef BOARD_PICO_H_
#define BOARD_PICO_H_

#define PROBE_IO_RAW
#define PROBE_CDC_UART

// PIO config
#define PROBE_SM 0
#define PROBE_PIN_OFFSET 2
#define PROBE_PIN_SWCLK (PROBE_PIN_OFFSET + 0) // 2
#define PROBE_PIN_SWDIO (PROBE_PIN_OFFSET + 1) // 3
// Target reset config
#define PROBE_PIN_RESET 1

// UART config
// Channel 0 (CDC0, "Alp SE-UART"): UART0. GP0/GP1 are avoided because GP1 is
// the target reset line above.
#define PROBE_UART_TX 12
#define PROBE_UART_RX 13
#define PROBE_UART_INTERFACE uart0
#define PROBE_UART_BAUDRATE 115200
// UART0 belongs to channel 0, so the probe's own debug stdio must not claim it.
#define PROBE_NO_STDIO_UART

// Channel 1 (CDC1, "Alp App Console"): UART1 on the standard Pico pins.
// Leave PROBE_UART1_INTERFACE undefined to build a single-CDC probe.
#define PROBE_UART1_TX 8
#define PROBE_UART1_RX 9
#define PROBE_UART1_INTERFACE uart1
#define PROBE_UART1_BAUDRATE 115200

#define PROBE_USB_CONNECTED_LED 25

// Vendor commands (alp_vendor.c). Development pins only, all free on the Pico:
// I2C0 SDA/SCL on GP16/GP17; demo pins GP20/GP21 (GP20 and GP21 are not used by
// SWD GP2/GP3, reset GP1, UART0 GP12/13, UART1 GP8/9 or the LED GP25).
// The Pico has no I2C pull-ups, so enable the internal ones.
#define PROBE_I2C_INTERFACE i2c0
#define PROBE_I2C_SDA 16
#define PROBE_I2C_SCL 17
#define PROBE_I2C_BAUDRATE 100000
#define PROBE_I2C_INTERNAL_PULLUP
#define PROBE_PIN_TABLE { \
	{20, "DEMO_A", 0}, \
	{21, "DEMO_B", 0}, \
}

#define PROBE_PRODUCT_STRING "Alp Lab Debug Probe on Pico (CMSIS-DAP)"

#endif
