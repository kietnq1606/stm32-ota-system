#include "ota_flash.h"

#include <string.h>

#include "stm32f1xx_hal.h"

#define OTA_FLASH_CRC32_INITIAL       0xFFFFFFFFUL
#define OTA_FLASH_CRC32_POLY          0xEDB88320UL

static uint8_t ota_flash_range_is_valid(ota_slot_t slot,
                                        uint32_t offset,
                                        uint32_t length);
static uint32_t ota_flash_crc32_update(uint32_t crc,
                                       const uint8_t *data,
                                       uint32_t length);

uint8_t ota_flash_erase_slot(ota_slot_t slot)
{
    FLASH_EraseInitTypeDef erase;
    uint32_t page_error = 0UL;
    uint32_t slot_base;
    uint32_t page_base;
    uint32_t page_count;
    HAL_StatusTypeDef status;

    /* Erase only the common writable image size used by both slots. */
    slot_base = ota_layout_get_slot_base(slot);
    page_base = slot_base - (slot_base % OTA_FLASH_PAGE_SIZE);
    page_count = (APP_SLOT_SIZE + (slot_base - page_base) + OTA_FLASH_PAGE_SIZE - 1UL) /
                 OTA_FLASH_PAGE_SIZE;

    status = HAL_FLASH_Unlock();
    if (status != HAL_OK)
    {
        return 0U;
    }

    memset(&erase, 0, sizeof(erase));
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.PageAddress = page_base;
    erase.NbPages = page_count;

    status = HAL_FLASHEx_Erase(&erase, &page_error);
    (void)HAL_FLASH_Lock();

    return (status == HAL_OK) ? 1U : 0U;
}

uint8_t ota_flash_write(ota_slot_t slot,
                        uint32_t offset,
                        const uint8_t *data,
                        uint32_t length)
{
    uint32_t address;
    uint32_t index;
    HAL_StatusTypeDef status;

    /* Half-word programming requires an even address. */
    if ((data == NULL) || ((offset & 1UL) != 0UL) ||
        !ota_flash_range_is_valid(slot, offset, length))
    {
        return 0U;
    }

    status = HAL_FLASH_Unlock();
    if (status != HAL_OK)
    {
        return 0U;
    }

    address = ota_layout_get_slot_base(slot) + offset;

    /* STM32F103 programs internal Flash as 16-bit half-words. */
    for (index = 0UL; index < length; index += 2UL)
    {
        uint16_t halfword = (uint16_t)data[index];

        if ((index + 1UL) < length)
        {
            halfword |= (uint16_t)((uint16_t)data[index + 1UL] << 8U);
        }
        else
        {
            halfword |= 0xFF00U;
        }

        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                                   address,
                                   (uint64_t)halfword);
        if (status != HAL_OK)
        {
            break;
        }

        address += 2UL;
    }

    (void)HAL_FLASH_Lock();

    return (status == HAL_OK) ? 1U : 0U;
}

uint8_t ota_flash_read(ota_slot_t slot,
                       uint32_t offset,
                       uint8_t *data,
                       uint32_t length)
{
    const uint8_t *source;

    /* Read back Flash bytes for verification or resume logic. */
    if ((data == NULL) || !ota_flash_range_is_valid(slot, offset, length))
    {
        return 0U;
    }

    source = (const uint8_t *)(ota_layout_get_slot_base(slot) + offset);
    memcpy(data, source, length);
    return 1U;
}

uint8_t ota_flash_verify(ota_slot_t slot,
                         uint32_t offset,
                         const uint8_t *data,
                         uint32_t length)
{
    const uint8_t *flash_data;

    /* Compare written Flash data against the received OTA chunk. */
    if ((data == NULL) || !ota_flash_range_is_valid(slot, offset, length))
    {
        return 0U;
    }

    flash_data = (const uint8_t *)(ota_layout_get_slot_base(slot) + offset);
    return (memcmp(flash_data, data, length) == 0) ? 1U : 0U;
}

uint32_t ota_flash_crc32(ota_slot_t slot, uint32_t image_size)
{
    const uint8_t *image;
    uint32_t crc;

    /* CRC32 is used to validate the complete firmware image. */
    if (!ota_flash_is_valid_image_size(image_size))
    {
        return 0UL;
    }

    image = (const uint8_t *)ota_layout_get_slot_base(slot);
    crc = ota_flash_crc32_update(OTA_FLASH_CRC32_INITIAL, image, image_size);
    return crc ^ OTA_FLASH_CRC32_INITIAL;
}

uint8_t ota_flash_is_valid_image_size(uint32_t image_size)
{
    if (image_size == 0UL)
    {
        return 0U;
    }

    return (image_size <= APP_SLOT_SIZE) ? 1U : 0U;
}

static uint8_t ota_flash_range_is_valid(ota_slot_t slot,
                                        uint32_t offset,
                                        uint32_t length)
{
    uint32_t base;

    if (length == 0UL)
    {
        return 0U;
    }

    if (offset > APP_SLOT_SIZE)
    {
        return 0U;
    }

    if (length > (APP_SLOT_SIZE - offset))
    {
        return 0U;
    }

    base = ota_layout_get_slot_base(slot);
    return ota_layout_address_in_slot(slot, base + offset, length);
}

static uint32_t ota_flash_crc32_update(uint32_t crc,
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
                crc = (crc >> 1U) ^ OTA_FLASH_CRC32_POLY;
            }
            else
            {
                crc >>= 1U;
            }
        }
    }

    return crc;
}
