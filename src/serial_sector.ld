/* PROBE_SERIAL_IN_FLASH: the last 4 KiB flash sector holds the USB serial record. */
ASSERT(__flash_binary_end <= ORIGIN(FLASH) + LENGTH(FLASH) - 4096,
       "image reaches the serial record sector (last 4 KiB of flash)")
