#include "ota_metadata.h"

#include <stddef.h>
#include <string.h>

#include "stm32f1xx_hal.h"

#define OTA_CRC32_INITIAL       0xFFFFFFFFUL
#define OTA_CRC32_POLY          0xEDB88320UL
#define OTA_METADATA_RECORD_SIZE (((uint32_t)sizeof(ota_metadata_t) + 1UL) & ~1UL)
#define OTA_METADATA_RECORD_COUNT (METADATA_SIZE / OTA_METADATA_RECORD_SIZE)

/* Standard reflected CRC32 helper used for metadata integrity. */
static uint32_t ota_metadata_crc32_update(uint32_t crc,
                                          const uint8_t *data,
                                          uint32_t length);
static uint8_t ota_metadata_slot_is_valid(uint8_t slot);
static uint8_t ota_metadata_image_state_is_valid(uint8_t state);
static uint8_t ota_metadata_record_is_erased(uint32_t address);
static uint8_t ota_metadata_program_record(uint32_t address,
                                           const ota_metadata_t *metadata);

void ota_metadata_set_defaults(ota_metadata_t *metadata)
{
    if (metadata == NULL)
    {
        return;
    }

    memset(metadata, 0, sizeof(*metadata));

    /* Default boot path is App A until an OTA update marks another slot. */
    metadata->magic = OTA_METADATA_MAGIC;
    metadata->sequence = 0UL;
    metadata->active_slot = (uint8_t)SLOT_A;
    metadata->confirmed_slot = (uint8_t)SLOT_A;
    metadata->pending_slot = OTA_SLOT_NONE;
    metadata->boot_try_count = 0U;

    metadata->app_a_state = (uint8_t)OTA_IMAGE_VALID;
    metadata->app_b_state = (uint8_t)OTA_IMAGE_EMPTY;

    metadata->crc32 = ota_metadata_calculate_crc32(metadata);
}

uint8_t ota_metadata_is_valid(const ota_metadata_t *metadata)
{
    if (metadata == NULL)
    {
        return 0U;
    }

    /* Reject erased, old-format, or corrupted metadata. */
    if (metadata->magic != OTA_METADATA_MAGIC)
    {
        return 0U;
    }

    if (!ota_metadata_slot_is_valid(metadata->active_slot) ||
        !ota_metadata_slot_is_valid(metadata->confirmed_slot))
    {
        return 0U;
    }

    if ((metadata->pending_slot != OTA_SLOT_NONE) &&
        !ota_metadata_slot_is_valid(metadata->pending_slot))
    {
        return 0U;
    }

    if (!ota_metadata_image_state_is_valid(metadata->app_a_state) ||
        !ota_metadata_image_state_is_valid(metadata->app_b_state))
    {
        return 0U;
    }

    if (metadata->boot_try_count > OTA_BOOT_TRY_MAX)
    {
        return 0U;
    }

    return (metadata->crc32 == ota_metadata_calculate_crc32(metadata)) ? 1U : 0U;
}

uint32_t ota_metadata_calculate_crc32(const ota_metadata_t *metadata)
{
    uint32_t crc;
    const uint8_t *bytes;
    uint32_t crc_offset;

    if (metadata == NULL)
    {
        return 0UL;
    }

    bytes = (const uint8_t *)metadata;
    crc = OTA_CRC32_INITIAL;
    crc_offset = (uint32_t)offsetof(ota_metadata_t, crc32);

    /* The stored CRC field is excluded from the CRC calculation. */
    crc = ota_metadata_crc32_update(crc, bytes, crc_offset);
    crc = ota_metadata_crc32_update(crc,
                                    &bytes[crc_offset + sizeof(metadata->crc32)],
                                    (uint32_t)sizeof(*metadata) -
                                    crc_offset -
                                    (uint32_t)sizeof(metadata->crc32));

    return crc ^ OTA_CRC32_INITIAL;
}

uint8_t ota_metadata_load(ota_metadata_t *metadata)
{
    ota_metadata_t candidate;
    uint32_t index;
    uint8_t found = 0U;

    if (metadata == NULL)
    {
        return 0U;
    }

    for (index = 0UL; index < OTA_METADATA_RECORD_COUNT; index++)
    {
        memcpy(&candidate,
               (const void *)(METADATA_BASE_ADDR +
               (index * OTA_METADATA_RECORD_SIZE)),
               sizeof(candidate));

        if (ota_metadata_is_valid(&candidate))
        {
            if ((found == 0U) || (candidate.sequence > metadata->sequence))
            {
                *metadata = candidate;
                found = 1U;
            }
        }
    }

    if (found == 0U)
    {
        ota_metadata_set_defaults(metadata);
        return 0U;
    }

    return 1U;
}

uint8_t ota_metadata_save(const ota_metadata_t *metadata)
{
    ota_metadata_t metadata_to_write;
    ota_metadata_t current_metadata;
    FLASH_EraseInitTypeDef erase;
    uint32_t page_error = 0UL;
    uint32_t index;
    uint32_t write_address = METADATA_BASE_ADDR;
    uint8_t erase_required = 1U;
    HAL_StatusTypeDef status;

    if (metadata == NULL)
    {
        return 0U;
    }

    metadata_to_write = *metadata;
    metadata_to_write.magic = OTA_METADATA_MAGIC;
    if (ota_metadata_load(&current_metadata) != 0U)
    {
        metadata_to_write.sequence = current_metadata.sequence + 1UL;
    }
    else
    {
        metadata_to_write.sequence = 0UL;
    }
    metadata_to_write.crc32 = ota_metadata_calculate_crc32(&metadata_to_write);

    if (!ota_metadata_is_valid(&metadata_to_write))
    {
        return 0U;
    }

    for (index = 0UL; index < OTA_METADATA_RECORD_COUNT; index++)
    {
        write_address = METADATA_BASE_ADDR + (index * OTA_METADATA_RECORD_SIZE);
        if (ota_metadata_record_is_erased(write_address))
        {
            erase_required = 0U;
            break;
        }
    }

    status = HAL_FLASH_Unlock();
    if (status != HAL_OK)
    {
        return 0U;
    }

    if (erase_required != 0U)
    {
        memset(&erase, 0, sizeof(erase));
        erase.TypeErase = FLASH_TYPEERASE_PAGES;
        erase.PageAddress = METADATA_BASE_ADDR;
        erase.NbPages = METADATA_SIZE / OTA_FLASH_PAGE_SIZE;

        /* The journal page is erased only when all record slots are consumed. */
        status = HAL_FLASHEx_Erase(&erase, &page_error);
        if (status == HAL_OK)
        {
            write_address = METADATA_BASE_ADDR;
        }
    }

    if (status == HAL_OK)
    {
        status = ota_metadata_program_record(write_address, &metadata_to_write)
               ? HAL_OK
               : HAL_ERROR;
    }

    (void)HAL_FLASH_Lock();

    if (status != HAL_OK)
    {
        return 0U;
    }

    return ota_metadata_load(&metadata_to_write);
}

static uint8_t ota_metadata_record_is_erased(uint32_t address)
{
    const uint8_t *bytes = (const uint8_t *)address;
    uint32_t index;

    for (index = 0UL; index < OTA_METADATA_RECORD_SIZE; index++)
    {
        if (bytes[index] != 0xFFU)
        {
            return 0U;
        }
    }

    return 1U;
}

static uint8_t ota_metadata_program_record(uint32_t address,
                                           const ota_metadata_t *metadata)
{
    const uint8_t *bytes;
    uint32_t index;
    HAL_StatusTypeDef status = HAL_OK;

    if (metadata == NULL)
    {
        return 0U;
    }

    bytes = (const uint8_t *)metadata;

    for (index = 0UL; index < sizeof(*metadata); index += 2UL)
    {
        uint16_t halfword = (uint16_t)bytes[index];

        if ((index + 1UL) < sizeof(*metadata))
        {
            halfword |= (uint16_t)((uint16_t)bytes[index + 1UL] << 8U);
        }
        else
        {
            halfword |= 0xFF00U;
        }

        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                                   address + index,
                                   (uint64_t)halfword);
        if (status != HAL_OK)
        {
            return 0U;
        }
    }

    return ota_metadata_is_valid((const ota_metadata_t *)address);
}

static uint32_t ota_metadata_crc32_update(uint32_t crc,
                                          const uint8_t *data,
                                          uint32_t length)
{
    uint32_t index;
    uint8_t bit;

    if (data == NULL)
    {
        return crc;
    }

    for (index = 0UL; index < length; index++)
    {
        crc ^= (uint32_t)data[index];

        for (bit = 0U; bit < 8U; bit++)
        {
            if ((crc & 1UL) != 0UL)
            {
                crc = (crc >> 1U) ^ OTA_CRC32_POLY;
            }
            else
            {
                crc >>= 1U;
            }
        }
    }

    return crc;
}

static uint8_t ota_metadata_slot_is_valid(uint8_t slot)
{
    return ((slot == (uint8_t)SLOT_A) || (slot == (uint8_t)SLOT_B)) ? 1U : 0U;
}

static uint8_t ota_metadata_image_state_is_valid(uint8_t state)
{
    return (state <= (uint8_t)OTA_IMAGE_INVALID) ? 1U : 0U;
}
