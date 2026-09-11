#include "ota_layout.h"

uint32_t ota_layout_get_slot_base(ota_slot_t slot)
{
    return (slot == SLOT_A) ? APP_A_BASE_ADDR : APP_B_BASE_ADDR;
}

uint32_t ota_layout_get_slot_end(ota_slot_t slot)
{
    return (slot == SLOT_A) ? APP_A_END_ADDR : APP_B_END_ADDR;
}

uint32_t ota_layout_get_slot_vector_offset(ota_slot_t slot)
{
    return (slot == SLOT_A) ? APP_A_VECTOR_OFFSET : APP_B_VECTOR_OFFSET;
}

uint32_t ota_layout_get_slot_size(void)
{
    return APP_SLOT_SIZE;
}

uint8_t ota_layout_address_in_flash(uint32_t address, uint32_t length)
{
    if (length == 0UL)
    {
        return 0U;
    }

    if (address < FLASH_BASE_ADDR)
    {
        return 0U;
    }

    if (address > FLASH_END_ADDR)
    {
        return 0U;
    }

    if (length > (FLASH_END_ADDR - address))
    {
        return 0U;
    }

    return 1U;
}

uint8_t ota_layout_address_in_slot(ota_slot_t slot,
                                   uint32_t address,
                                   uint32_t length)
{
    uint32_t base = ota_layout_get_slot_base(slot);
    uint32_t end = ota_layout_get_slot_end(slot);

    if (length == 0UL)
    {
        return 0U;
    }

    if (address < base)
    {
        return 0U;
    }

    if (address > end)
    {
        return 0U;
    }

    if (length > (end - address))
    {
        return 0U;
    }

    return 1U;
}

uint8_t ota_layout_address_is_page_aligned(uint32_t address)
{
    return ((address - FLASH_BASE_ADDR) % OTA_FLASH_PAGE_SIZE) == 0UL;
}
