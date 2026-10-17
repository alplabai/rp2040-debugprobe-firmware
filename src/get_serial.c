/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2021 Federico Zuccardi Merli
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

#include <stdint.h>
#include "pico.h"
#include "pico/unique_id.h"
#include "get_serial.h"
#include "probe_config.h"

#ifdef PROBE_SERIAL_IN_FLASH
#include <stddef.h>
#include <string.h>
#include "hardware/flash.h"
#include "hardware/sync.h"
#include "hardware/regs/addressmap.h"
#include "pico/rand.h"

/* Serial record in the last 4 KiB flash sector (the image is linked below it,
 * see CMakeLists.txt). Little-endian, CRC-32 (IEEE) over the first 16 bytes. */
#define SERIAL_SECTOR_OFFS (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)
#define SERIAL_MAGIC 0x53504C41u /* "ALPS" in memory order */
#define SERIAL_VERSION 1u

struct serial_record {
    uint32_t magic;
    uint32_t version;
    uint8_t serial[8];
    uint32_t crc;
};
_Static_assert(sizeof(struct serial_record) == 20, "serial record layout");
_Static_assert(sizeof(struct serial_record) <= FLASH_PAGE_SIZE, "serial record must fit one page");
_Static_assert(PICO_UNIQUE_BOARD_ID_SIZE_BYTES == 8, "usb_serial is 16 hex chars");

static uint32_t crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & -(c & 1u));
    }
    return ~c;
}

static bool record_valid(const struct serial_record *r)
{
    return r->magic == SERIAL_MAGIC && r->version == SERIAL_VERSION &&
           r->crc == crc32((const uint8_t *)r, offsetof(struct serial_record, crc));
}

/* Must run before USB, the scheduler and the second core start: the flash
 * write disables interrupts and takes the flash out of XIP mode. The image runs
 * from RAM (copy_to_ram), so nothing executes from flash meanwhile. */
static void serial_from_flash(uint8_t id[8])
{
    /* Uncached alias: never read a stale XIP cache line. */
    const struct serial_record *stored =
        (const struct serial_record *)(XIP_NOCACHE_NOALLOC_BASE + SERIAL_SECTOR_OFFS);
    struct serial_record r;

    memcpy(&r, stored, sizeof(r));
    if (!record_valid(&r)) {
        static uint8_t page[FLASH_PAGE_SIZE];
        uint64_t rnd = get_rand_64();

        r.magic = SERIAL_MAGIC;
        r.version = SERIAL_VERSION;
        memcpy(r.serial, &rnd, sizeof(r.serial));
        r.crc = crc32((const uint8_t *)&r, offsetof(struct serial_record, crc));
        memset(page, 0xFF, sizeof(page));
        memcpy(page, &r, sizeof(r));

        uint32_t irq = save_and_disable_interrupts();
        flash_range_erase(SERIAL_SECTOR_OFFS, FLASH_SECTOR_SIZE);
        flash_range_program(SERIAL_SECTOR_OFFS, page, FLASH_PAGE_SIZE);
        restore_interrupts(irq);
        /* A failed write only means a new serial on the next boot; use this one now. */
    }
    memcpy(id, r.serial, sizeof(r.serial));
}
#endif

/* C string for iSerialNumber in USB Device Descriptor, two chars per byte + terminating NUL */
char usb_serial[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];

/* Why a uint8_t[8] array inside a struct instead of an uint64_t an inquiring mind might wonder */
static pico_unique_board_id_t uID;

void usb_serial_init(void)
{
#ifdef PROBE_SERIAL_IN_FLASH
    serial_from_flash(uID.id);
#else
    pico_get_unique_board_id(&uID);
#endif

    for (int i = 0; i < PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2; i++)
    {
        /* Byte index inside the uid array */
        int bi = i / 2;
        /* Use high nibble first to keep memory order (just cosmetics) */
        uint8_t nibble = (uID.id[bi] >> 4) & 0x0F;
        uID.id[bi] <<= 4;
        /* Binary to hex digit */
        usb_serial[i] = nibble < 10 ? nibble + '0' : nibble + 'A' - 10;
    }
}
