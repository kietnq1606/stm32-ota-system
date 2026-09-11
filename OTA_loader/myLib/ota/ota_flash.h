#ifndef OTA_FLASH_H
#define OTA_FLASH_H

#include <stdint.h>
#include "ota_layout.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Erase the writable image area of one OTA app slot. */
uint8_t ota_flash_erase_slot(ota_slot_t slot);

/* Write an OTA chunk to a slot at a byte offset from slot base. */
uint8_t ota_flash_write(ota_slot_t slot,
                        uint32_t offset,
                        const uint8_t *data,
                        uint32_t length);

/* Read bytes from a slot at a byte offset from slot base. */
uint8_t ota_flash_read(ota_slot_t slot,
                       uint32_t offset,
                       uint8_t *data,
                       uint32_t length);

/* Compare Flash content with the provided RAM buffer. */
uint8_t ota_flash_verify(ota_slot_t slot,
                         uint32_t offset,
                         const uint8_t *data,
                         uint32_t length);

/* Calculate CRC32 of an image already written in a slot. */
uint32_t ota_flash_crc32(ota_slot_t slot, uint32_t image_size);

/* Check whether an image fits the common OTA image limit. */
uint8_t ota_flash_is_valid_image_size(uint32_t image_size);

#ifdef __cplusplus
}
#endif

#endif /* OTA_FLASH_H */
