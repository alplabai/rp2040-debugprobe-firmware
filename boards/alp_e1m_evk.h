/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Alp Lab AB
 */

// pico-sdk board file: on-board RP2040 debugger (U55) of the Alp Lab E1M EVK
// 2641-R3. IOVDD is +1V8; the firmware switches the pads to 1.8 V in main()
// (PROBE_PADS_1V8 in include/board_alp_e1m_evk_config.h).

#ifndef _BOARDS_ALP_E1M_EVK_H
#define _BOARDS_ALP_E1M_EVK_H

pico_board_cmake_set(PICO_PLATFORM, rp2040)

#define ALP_LAB_E1M_EVK

// Y1: 12 MHz crystal.
#ifndef XOSC_KHZ
#define XOSC_KHZ 12000
#endif
#ifndef PICO_XOSC_STARTUP_DELAY_MULTIPLIER
#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64
#endif

// No PICO_DEFAULT_UART / PICO_DEFAULT_LED_PIN: both UARTs and the LED belong
// to the probe (see the probe board config).

// U54 MX25U1632F (Macronix, 2 MiB, 1.8 V). The W25Q080 boot2 writes SR2, which
// this part does not have (QE is SR1 bit 6), so use the generic 03h boot2.
#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1

pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (2 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
#endif

#endif
