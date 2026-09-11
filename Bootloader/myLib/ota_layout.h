#ifndef LAYOUT_H
#define LAYOUT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * STM32F103VCTx internal Flash layout.
 *
 * Total Flash : 256 KB, 0x08000000 - 0x0803FFFF
 * Page size   : 2 KB
 *
 * OTA layout:
 * Bootloader  :   6 KB
 * App A       : 124 KB
 * App B       : 124 KB
 * Metadata    :   2 KB
 */

#define FLASH_BASE_ADDR             0x08000000UL
#define FLASH_TOTAL_SIZE            (256UL * 1024UL)
#define OTA_FLASH_PAGE_SIZE         (2UL * 1024UL)

#define BOOTLOADER_SIZE             (6UL * 1024UL)
#define APP_A_SLOT_SIZE             (124UL * 1024UL)
#define APP_B_SLOT_SIZE             (124UL * 1024UL)
#define APP_SLOT_SIZE               APP_A_SLOT_SIZE
#define METADATA_SIZE               (2UL * 1024UL)

#define BOOTLOADER_BASE_ADDR        FLASH_BASE_ADDR
#define BOOTLOADER_END_ADDR         (BOOTLOADER_BASE_ADDR + BOOTLOADER_SIZE)

#define APP_A_BASE_ADDR             BOOTLOADER_END_ADDR
#define APP_A_END_ADDR              (APP_A_BASE_ADDR + APP_A_SLOT_SIZE)

#define APP_B_BASE_ADDR             APP_A_END_ADDR
#define APP_B_END_ADDR              (APP_B_BASE_ADDR + APP_B_SLOT_SIZE)

#define METADATA_BASE_ADDR          APP_B_END_ADDR
#define METADATA_END_ADDR           (METADATA_BASE_ADDR + METADATA_SIZE)

#define FLASH_END_ADDR              (FLASH_BASE_ADDR + FLASH_TOTAL_SIZE)

#define APP_A_VECTOR_OFFSET         (APP_A_BASE_ADDR - FLASH_BASE_ADDR)
#define APP_B_VECTOR_OFFSET         (APP_B_BASE_ADDR - FLASH_BASE_ADDR)

#define BOOTLOADER_PAGE_COUNT       (BOOTLOADER_SIZE / OTA_FLASH_PAGE_SIZE)
#define APP_A_SLOT_PAGE_COUNT       (APP_A_SLOT_SIZE / OTA_FLASH_PAGE_SIZE)
#define APP_B_SLOT_PAGE_COUNT       (APP_B_SLOT_SIZE / OTA_FLASH_PAGE_SIZE)
#define APP_SLOT_PAGE_COUNT         APP_A_SLOT_PAGE_COUNT
#define METADATA_PAGE_COUNT         (METADATA_SIZE / OTA_FLASH_PAGE_SIZE)

#if (METADATA_END_ADDR != FLASH_END_ADDR)
#error "OTA flash layout must consume exactly 256 KB"
#endif

#if ((BOOTLOADER_SIZE % OTA_FLASH_PAGE_SIZE) != 0)
#error "OTA bootloader size must be flash-page aligned"
#endif

#if ((APP_A_SLOT_SIZE % OTA_FLASH_PAGE_SIZE) != 0)
#error "OTA App A slot size must be flash-page aligned"
#endif

#if ((APP_B_SLOT_SIZE % OTA_FLASH_PAGE_SIZE) != 0)
#error "OTA App B slot size must be flash-page aligned"
#endif

#if ((METADATA_SIZE % OTA_FLASH_PAGE_SIZE) != 0)
#error "OTA metadata size must be flash-page aligned"
#endif

typedef enum
{
    SLOT_A = 0,
    SLOT_B = 1
} ota_slot_t;

uint32_t ota_layout_get_slot_base(ota_slot_t slot);
uint32_t ota_layout_get_slot_end(ota_slot_t slot);
uint32_t ota_layout_get_slot_vector_offset(ota_slot_t slot);
uint32_t ota_layout_get_slot_size(void);
uint8_t ota_layout_address_in_flash(uint32_t address, uint32_t length);
uint8_t ota_layout_address_in_slot(ota_slot_t slot,
                                   uint32_t address,
                                   uint32_t length);
uint8_t ota_layout_address_is_page_aligned(uint32_t address);

#ifdef __cplusplus
}
#endif

#endif /* LAYOUT_H */
