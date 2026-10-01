/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2021 Raspberry Pi (Trading) Ltd.
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

#include <pico/stdlib.h>
#include "FreeRTOS.h"
#include "task.h"
#include "tusb.h"
#include "autobaud.h"

#include "probe_config.h"
#include "cdc_uart.h"

// Actually s^-1 so 25ms
#define DEBOUNCE_MS 40

/* One entry per CDC-ACM interface; index == tud_cdc_n_* instance number. */
typedef struct {
    uart_inst_t *uart;
    uint8_t tx_pin, rx_pin;
    int8_t tx_led, rx_led;          /* -1: no LED */
    uint baudrate;
    TaskHandle_t task;
    TickType_t last_wake, interval;
    volatile TickType_t break_expiry;
    volatile bool timed_break;
    uint debounce_ticks;
    volatile uint tx_led_debounce;
    uint rx_led_debounce;
    int was_connected;
    uint cdc_tx_oe;
    /* Max 1 FIFO worth of data */
    uint8_t tx_buf[32];
    uint8_t rx_buf[32];
} cdc_inst_t;

#ifdef PROBE_UART_TX_LED
#define LED0_TX PROBE_UART_TX_LED
#else
#define LED0_TX -1
#endif
#ifdef PROBE_UART_RX_LED
#define LED0_RX PROBE_UART_RX_LED
#else
#define LED0_RX -1
#endif

static cdc_inst_t cdc[CDC_UART_COUNT] = {
    {
        .uart = PROBE_UART_INTERFACE,
        .tx_pin = PROBE_UART_TX, .rx_pin = PROBE_UART_RX,
        .tx_led = LED0_TX, .rx_led = LED0_RX,
        .baudrate = PROBE_UART_BAUDRATE,
        .interval = 100, .debounce_ticks = 5,
    },
#ifdef PROBE_UART1_INTERFACE
    {
        .uart = PROBE_UART1_INTERFACE,
        .tx_pin = PROBE_UART1_TX, .rx_pin = PROBE_UART1_RX,
        .tx_led = -1, .rx_led = -1,
        .baudrate = PROBE_UART1_BAUDRATE,
        .interval = 100, .debounce_ticks = 5,
    },
#endif
};

static BaudInfo_t baud_info;

static inline void led_put(int pin, bool v) {
    if (pin >= 0)
        gpio_put(pin, v);
}

static void led_init(int pin) {
    if (pin >= 0) {
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_OUT);
    }
}

void cdc_uart_init(void) {
    for (uint i = 0; i < CDC_UART_COUNT; i++) {
        cdc_inst_t *c = &cdc[i];
        gpio_set_function(c->tx_pin, GPIO_FUNC_UART);
        gpio_set_function(c->rx_pin, GPIO_FUNC_UART);
        gpio_set_pulls(c->tx_pin, 1, 0);
        gpio_set_pulls(c->rx_pin, 1, 0);
        uart_init(c->uart, c->baudrate);
        c->tx_led_debounce = 0;
        c->rx_led_debounce = 0;
        led_init(c->tx_led);
        led_init(c->rx_led);
    }

/* Flow control / modem lines exist on channel 0 only. */
#ifdef PROBE_UART_HWFC
    /* HWFC implies that hardware flow control is implemented and the
     * UART operates in "full-duplex" mode (See USB CDC PSTN120 6.3.12).
     * Default to pulling in the active direction, so an unconnected CTS
     * behaves the same as if CTS were not enabled. */
    gpio_set_pulls(PROBE_UART_CTS, 0, 1);
    gpio_set_function(PROBE_UART_RTS, GPIO_FUNC_UART);
    gpio_set_function(PROBE_UART_CTS, GPIO_FUNC_UART);
    uart_set_hw_flow(PROBE_UART_INTERFACE, true, true);
#else
#ifdef PROBE_UART_RTS
    gpio_init(PROBE_UART_RTS);
    gpio_set_dir(PROBE_UART_RTS, GPIO_OUT);
    gpio_put(PROBE_UART_RTS, 1);
#endif
#endif

#ifdef PROBE_UART_DTR
    gpio_init(PROBE_UART_DTR);
    gpio_set_dir(PROBE_UART_DTR, GPIO_OUT);
    gpio_put(PROBE_UART_DTR, 1);
#endif
}

static bool cdc_task_n(uint itf)
{
    cdc_inst_t *c = &cdc[itf];
    uint rx_len = 0;
    bool keep_alive = false;

    // Consume uart fifo regardless even if not connected
    while(uart_is_readable(c->uart) && (rx_len < sizeof(c->rx_buf))) {
        c->rx_buf[rx_len++] = uart_getc(c->uart);
    }

    if (tud_cdc_n_connected(itf)) {
        c->was_connected = 1;
        int written = 0;
        /* Implicit overflow if we don't write all the bytes to the host.
         * Also throw away bytes if we can't write... */
        if (rx_len) {
          led_put(c->rx_led, 1);
          c->rx_led_debounce = c->debounce_ticks;
          written = MIN(tud_cdc_n_write_available(itf), rx_len);
          if (rx_len > written)
              c->cdc_tx_oe++;

          if (written > 0) {
            tud_cdc_n_write(itf, c->rx_buf, written);
            tud_cdc_n_write_flush(itf);
          }
        } else {
          if (c->rx_led_debounce)
            c->rx_led_debounce--;
          else
            led_put(c->rx_led, 0);
        }

      /* Reading from a firehose and writing to a FIFO. */
      size_t watermark = MIN(tud_cdc_n_available(itf), sizeof(c->tx_buf));
      if (watermark > 0) {
        size_t tx_len;
        led_put(c->tx_led, 1);
        c->tx_led_debounce = c->debounce_ticks;
        /* Batch up to half a FIFO of data - don't clog up on RX */
        watermark = MIN(watermark, 16);
        tx_len = tud_cdc_n_read(itf, c->tx_buf, watermark);
        uart_write_blocking(c->uart, c->tx_buf, tx_len);
      } else {
          if (c->tx_led_debounce)
            c->tx_led_debounce--;
          else
            led_put(c->tx_led, 0);
      }
      /* Pending break handling */
      if (c->timed_break) {
        if (((int)c->break_expiry - (int)xTaskGetTickCount()) < 0) {
          c->timed_break = false;
          uart_set_break(c->uart, false);
          c->tx_led_debounce = 0;
        } else {
          keep_alive = true;
        }
      }
    } else if (c->was_connected) {
      tud_cdc_n_write_clear(itf);
      uart_set_break(c->uart, false);
      c->timed_break = false;
      c->was_connected = 0;
      c->tx_led_debounce = 0;
      c->cdc_tx_oe = 0;
    }
    return keep_alive;
}

bool cdc_task(void)
{
    bool keep_alive = false;
    for (uint i = 0; i < CDC_UART_COUNT; i++)
        keep_alive |= cdc_task_n(i);
    return keep_alive;
}

static void cdc_uart_set_baudrate(uint itf, uint32_t baudrate) {
  cdc_inst_t *c = &cdc[itf];
  /* Set the tick thread interval to the amount of time it takes to
   * fill up half a FIFO. Millis is too coarse for integer divide.
   */
  uint32_t micros = (1000 * 1000 * 16 * 10) / MAX(baudrate, 1);
  c->interval = MAX(1, micros / ((1000 * 1000) / configTICK_RATE_HZ));
  c->debounce_ticks = MAX(1, configTICK_RATE_HZ / (c->interval * DEBOUNCE_MS));
  probe_info("CDC%u new baud rate %ld micros %ld interval %lu\n",
              itf, baudrate, micros, c->interval);
  uart_deinit(c->uart);
  tud_cdc_n_write_clear(itf);
  tud_cdc_n_read_flush(itf);

  uart_init(c->uart, baudrate);
}

void cdc_thread(void *ptr)
{
  uint itf = (uint)(uintptr_t)ptr;
  cdc_inst_t *c = &cdc[itf];
  BaseType_t delayed;
  c->last_wake = xTaskGetTickCount();
  bool keep_alive;
  /* Threaded with a polling interval that scales according to linerate */
  while (1) {
    keep_alive = cdc_task_n(itf);
    if (!keep_alive) {
      delayed = xTaskDelayUntil(&c->last_wake, c->interval);
      if (delayed == pdFALSE)
        c->last_wake = xTaskGetTickCount();
      /* Autobaud is scoped to CDC0 */
      if (itf == 0 && autobaud_running) {
        // Receive baud information from autobaud thread
        if (xQueueReceive(baudQueue, &baud_info, 0) == pdTRUE) {
          cdc_uart_set_baudrate(0, baud_info.baud);
          // Assume 8N1
          uart_set_format(c->uart, 8, 1, UART_PARITY_NONE);
        }
      }
    }
  }
}

void cdc_uart_tasks_create(UBaseType_t prio)
{
  for (uint i = 0; i < CDC_UART_COUNT; i++) {
    xTaskCreate(cdc_thread, "UART", configMINIMAL_STACK_SIZE, (void *)(uintptr_t)i, prio, &cdc[i].task);
#if (configNUMBER_OF_CORES > 1)
    vTaskCoreAffinitySet(cdc[i].task, (1 << 0));
#endif
  }
}

void cdc_uart_tasks_suspend(void)
{
  for (uint i = 0; i < CDC_UART_COUNT; i++)
    vTaskSuspend(cdc[i].task);
}

void cdc_uart_tasks_resume(void)
{
  for (uint i = 0; i < CDC_UART_COUNT; i++)
    vTaskResume(cdc[i].task);
}

void cdc_uart_tasks_delete(void)
{
  for (uint i = 0; i < CDC_UART_COUNT; i++)
    vTaskDelete(cdc[i].task);
}

void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const* line_coding)
{
  cdc_inst_t *c = &cdc[itf];
  if (itf == 0) {
    if (line_coding->bit_rate == MAGIC_BAUD) {
      if (!autobaud_running)
        autobaud_start();
      return;
    }
    else if (autobaud_running) {
      autobaud_wait_stop();
    }
  }
  uart_parity_t parity;
  uint data_bits, stop_bits;

  /* Modifying state, so park the thread before changing it. */
  if (tud_cdc_n_connected(itf))
    vTaskSuspend(c->task);

  cdc_uart_set_baudrate(itf, line_coding->bit_rate);

  switch (line_coding->parity) {
  case CDC_LINE_CODING_PARITY_ODD:
    parity = UART_PARITY_ODD;
    break;
  case CDC_LINE_CODING_PARITY_EVEN:
    parity = UART_PARITY_EVEN;
    break;
  default:
    probe_info("invalid parity setting %u\n", line_coding->parity);
    /* fallthrough */
  case CDC_LINE_CODING_PARITY_NONE:
    parity = UART_PARITY_NONE;
    break;
  }

  switch (line_coding->data_bits) {
  case 5:
  case 6:
  case 7:
  case 8:
    data_bits = line_coding->data_bits;
    break;
  default:
    probe_info("invalid data bits setting: %u\n", line_coding->data_bits);
    data_bits = 8;
    break;
  }

  /* The PL011 only supports 1 or 2 stop bits. 1.5 stop bits is translated to 2,
   * which is safer than the alternative. */
  switch (line_coding->stop_bits) {
  case CDC_LINE_CONDING_STOP_BITS_1_5:
  case CDC_LINE_CONDING_STOP_BITS_2:
    stop_bits = 2;
  break;
  default:
    probe_info("invalid stop bits setting: %u\n", line_coding->stop_bits);
    /* fallthrough */
  case CDC_LINE_CONDING_STOP_BITS_1:
    stop_bits = 1;
  break;
  }

  uart_set_format(c->uart, data_bits, stop_bits, parity);
  /* Windows likes to arbitrarily set/get line coding after dtr/rts changes, so
   * don't resume if we shouldn't */
  if(tud_cdc_n_connected(itf))
    vTaskResume(c->task);
}

void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts)
{
  cdc_inst_t *c = &cdc[itf];
  if (itf == 0) {
#ifdef PROBE_UART_RTS
    gpio_put(PROBE_UART_RTS, !rts);
#endif
#ifdef PROBE_UART_DTR
    gpio_put(PROBE_UART_DTR, !dtr);
#endif
  }
  (void)rts;

  /* CDC drivers use linestate as a bodge to activate/deactivate the interface.
   * Resume our UART polling on activate, stop on deactivate */
  if (!dtr) {
    vTaskSuspend(c->task);
    led_put(c->rx_led, 0);
    c->rx_led_debounce = 0;
    led_put(c->tx_led, 0);
    c->tx_led_debounce = 0;
  } else
    vTaskResume(c->task);
}

void tud_cdc_send_break_cb(uint8_t itf, uint16_t wValue) {
  cdc_inst_t *c = &cdc[itf];
  switch(wValue) {
    case 0:
    uart_set_break(c->uart, false);
    c->timed_break = false;
    c->tx_led_debounce = 0;
    break;
    case 0xffff:
    uart_set_break(c->uart, true);
    c->timed_break = false;
    led_put(c->tx_led, 1);
    c->tx_led_debounce = 1 << 30;
    break;
    default:
    uart_set_break(c->uart, true);
    c->timed_break = true;
    led_put(c->tx_led, 1);
    c->tx_led_debounce = 1 << 30;
    c->break_expiry = xTaskGetTickCount() + (wValue * (configTICK_RATE_HZ / 1000));
    break;
  }
}
