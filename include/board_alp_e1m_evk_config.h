/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Alp Lab AB
 */

// Probe config for the on-board RP2040 debugger (U55) of the Alp Lab E1M EVK
// 2641-R3. Build: -DALP_E1M_EVK=ON -DPICO_BOARD=alp_e1m_evk.
// RP2040 IOVDD = +1V8: the GPIOs are NOT 3.3 V tolerant.

#ifndef BOARD_ALP_E1M_EVK_H_
#define BOARD_ALP_E1M_EVK_H_

#define PROBE_BOARD_NAME "alp-e1m-evk-2641-r3"

// IOVDD is +1V8: switch PADS_BANK0 and PADS_QSPI to 1.8 V first thing in main.
#define PROBE_PADS_1V8

#define PROBE_IO_RAW
#define PROBE_CDC_UART

// PIO config. SWDIO/SWCLK go through U3 TXS0108E (auto-direction): 4 MHz max.
// SWCLK is the side-set pin, SWDIO the out pin; PROBE_PIN_OFFSET is the lowest
// of the two (the PIO sets both pin directions from it).
#define PROBE_SM 0
#define PROBE_PIN_OFFSET 16
#define PROBE_PIN_SWDIO 16 // TMS/SWDIO_RP
#define PROBE_PIN_SWCLK 17 // TCK/SWDCLK_RP
#define PROBE_SWCLK_MAX_KHZ 4000

// DAP nRESET: GPIO20 JTAG_RST_RP drives Q16; high pulls JTAG_RST = module
// E.NSRST low. Push-pull, active high.
#define PROBE_PIN_RESET 20
#define PROBE_RESET_ACTIVE_HIGH

// Channel 0 (CDC0, "Alp SE-UART"): UART1 to the module SE-UART.
#define PROBE_UART_TX 4
#define PROBE_UART_RX 5
#define PROBE_UART_INTERFACE uart1
#define PROBE_UART_BAUDRATE 115200
#define PROBE_NO_STDIO_UART

// Channel 1 (CDC1, "Alp App Console"): UART0 to the module UART5.
#define PROBE_UART1_TX 0
#define PROBE_UART1_RX 1
#define PROBE_UART1_INTERFACE uart0
#define PROBE_UART1_BAUDRATE 115200

// Status LED D53 via Q27, active high.
#define PROBE_USB_CONNECTED_LED 21

// I2C bus 0: CURR_I2C power monitors (only while switch S6 selects the RP2040).
// External 2.2k pull-ups.
#define PROBE_I2C_INTERFACE i2c0
#define PROBE_I2C_SDA 24
#define PROBE_I2C_SCL 25
#define PROBE_I2C_BAUDRATE 100000

// I2C bus 1: module GP_I2C (GreenPAK/RTC bus) via U14 LSF0102. External 10k pull-ups.
#define PROBE_I2C1_INTERFACE i2c1
#define PROBE_I2C1_SDA 2
#define PROBE_I2C1_SCL 3
#define PROBE_I2C1_BAUDRATE 100000

// ADC3 (GPIO29) = VIO_OUT / 2. ADC reference = ADC_AVDD = 3.3 V.
#define PROBE_ADC_CHANNELS (1u << 3)
#define PROBE_ADC_VREF_MV 3300

// Stream record flags bit3: RP_CURR_STATUS (1 = mA range, 0 = uA range).
#define PROBE_STREAM_AUX_GPIO 26

// TDO_RP / TDI_RP: unused in SWD, input with pulls off.
#define PROBE_RELEASE_GPIO_MASK ((1u << 18) | (1u << 19))

// All FET gates have external pull-downs: "released" = FET off = line not pulled.
#define PROBE_PIN_TABLE { \
	{13, "BOOT0", 0}, \
	{12, "BOOT1", 0}, \
	{11, "BOOT2", 0}, \
	{10, "BOOT3", 0}, \
	{8, "MCU_RST_RP", 0}, \
	{9, "MCU_RST_SENSE", ALP_PIN_IN}, \
	{23, "MODULE_EN_RP", 0}, \
	{22, "MODULE_STBY_RP", 0}, \
	{7, "RTC_WAKE_N", ALP_PIN_IN}, \
	{6, "CURR_SEL", 0}, \
	{26, "CURR_STATUS", ALP_PIN_IN}, \
	{28, "CURR_ALERT_N", ALP_PIN_IN}, \
	{14, "PD_ACTIVE_N", ALP_PIN_IN}, \
	{15, "PWR_FLT_N", ALP_PIN_IN}, \
}

#define PROBE_PRODUCT_STRING "Alp Lab E1M EVK Probe (CMSIS-DAP)"

#endif
