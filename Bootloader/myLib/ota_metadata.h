#ifndef OTA_METADATA_H
#define OTA_METADATA_H

#include <stdint.h>
#include "ota_layout.h"

#define OTA_METADATA_MAGIC      0x4341414FUL  /* "CAAO" */
#define OTA_SLOT_NONE           0xFFU
#define OTA_BOOT_TRY_MAX        3UL

typedef enum
{
    OTA_IMAGE_EMPTY   = 0,
    OTA_IMAGE_WRITING = 1,
    OTA_IMAGE_PENDING = 2,
    OTA_IMAGE_VALID   = 3,
    OTA_IMAGE_INVALID = 4
} ota_image_state_t;

typedef struct
{
    uint32_t magic;              /* Valid metadata marker. */
    uint32_t sequence;           /* Metadata update counter. */
    uint32_t crc32;              /* Metadata CRC32. */

    uint8_t active_slot;         /* Slot to boot. */
    uint8_t confirmed_slot;      /* Last good slot. */

    uint8_t pending_slot;        /* Slot waiting test boot. */
    uint8_t boot_try_count;      /* Pending boot attempts. */
    uint8_t app_a_state;         /* App A state. */
    uint8_t app_b_state;         /* App B state. */

    uint32_t app_a_size;         /* App A size. */
    uint32_t app_a_crc32;        /* App A CRC32. */

    uint32_t app_b_size;         /* App B size. */
    uint32_t app_b_crc32;        /* App B CRC32. */
} ota_metadata_t;


void ota_metadata_set_defaults(ota_metadata_t *metadata);
uint8_t ota_metadata_is_valid(const ota_metadata_t *metadata);
uint32_t ota_metadata_calculate_crc32(const ota_metadata_t *metadata);
uint8_t ota_metadata_load(ota_metadata_t *metadata);
uint8_t ota_metadata_save(const ota_metadata_t *metadata);


#endif /* OTA_METADATA_H */
