#include "boot_service.h"

#include "main.h"
#include "ota_metadata.h"

#define BOOT_SERVICE_SRAM_BASE      0x20000000UL
#define BOOT_SERVICE_SRAM_SIZE      (48UL * 1024UL)
#define BOOT_SERVICE_SRAM_END       (BOOT_SERVICE_SRAM_BASE + BOOT_SERVICE_SRAM_SIZE)
#define BOOT_SERVICE_NVIC_REGISTER_COUNT 8UL

static uint8_t boot_service_slot_has_valid_image(const ota_metadata_t *metadata,
                                                 ota_slot_t slot);
static uint8_t boot_service_application_is_valid(ota_slot_t slot);
static uint8_t boot_service_image_state_allows_boot(uint8_t state);
static uint8_t boot_service_select_slot(ota_metadata_t *metadata,
                                        ota_slot_t *slot);
static void boot_service_jump_to_slot(ota_slot_t slot);

void boot_service_run(void)
{
    ota_metadata_t metadata;
    ota_slot_t slot;

    /*
     * Load the latest valid OTA metadata record. If flash is empty or corrupted,
     * rebuild the default metadata so the bootloader has a known App A fallback.
     */
    if (ota_metadata_load(&metadata) == 0U)
    {
        if (ota_metadata_save(&metadata) == 0U)
        {
            Error_Handler();
            return;
        }
    }

    /*
     * Pick the slot to run based on pending, active, confirmed, and fallback
     * order. The selected slot is jumped to only after its vector table passes
     * validation.
     */
    if (boot_service_select_slot(&metadata, &slot) != 0U)
    {
        boot_service_jump_to_slot(slot);
    }

    Error_Handler();
}

static uint8_t boot_service_select_slot(ota_metadata_t *metadata,
                                        ota_slot_t *slot)
{
    ota_slot_t candidate;

    if ((metadata == 0) || (slot == 0))
    {
        return 0U;
    }

    if (metadata->pending_slot != OTA_SLOT_NONE)
    {
        candidate = (ota_slot_t)metadata->pending_slot;

        /*
         * A pending image is a newly downloaded image waiting for confirmation.
         * Each boot attempt increments the counter before jumping, so repeated
         * resets can eventually reject a bad image and return to the confirmed
         * slot.
         */
        if ((metadata->boot_try_count < OTA_BOOT_TRY_MAX) &&
            boot_service_slot_has_valid_image(metadata, candidate))
        {
            metadata->active_slot = (uint8_t)candidate;
            metadata->boot_try_count++;
            /* Never start an unrecorded trial boot. */
            if (ota_metadata_save(metadata) == 0U)
            {
                return 0U;
            }
            *slot = candidate;
            return 1U;
        }

        /*
         * The pending image is either invalid or has exhausted its trial boots.
         * Mark it invalid and restore the last confirmed slot as the active
         * target.
         */
        if (candidate == SLOT_A)
        {
            metadata->app_a_state = (uint8_t)OTA_IMAGE_INVALID;
        }
        else
        {
            metadata->app_b_state = (uint8_t)OTA_IMAGE_INVALID;
        }

        metadata->pending_slot = OTA_SLOT_NONE;
        metadata->boot_try_count = 0U;
        metadata->active_slot = metadata->confirmed_slot;
        if (ota_metadata_save(metadata) == 0U)
        {
            return 0U;
        }
    }

    /* Prefer the current active slot when it still contains a bootable image. */
    candidate = (ota_slot_t)metadata->active_slot;
    if (boot_service_slot_has_valid_image(metadata, candidate))
    {
        *slot = candidate;
        return 1U;
    }

    /* If active is not usable, try the last image that was confirmed by the app. */
    candidate = (ota_slot_t)metadata->confirmed_slot;
    if (boot_service_slot_has_valid_image(metadata, candidate))
    {
        *slot = candidate;
        return 1U;
    }

    /* Last-resort scan keeps the device bootable if metadata points to a bad slot. */
    if (boot_service_slot_has_valid_image(metadata, SLOT_A))
    {
        *slot = SLOT_A;
        return 1U;
    }

    if (boot_service_slot_has_valid_image(metadata, SLOT_B))
    {
        *slot = SLOT_B;
        return 1U;
    }

    /* No valid image exists in either slot. The caller will enter Error_Handler(). */
    return 0U;
}

static uint8_t boot_service_slot_has_valid_image(const ota_metadata_t *metadata,
                                                 ota_slot_t slot)
{
    uint8_t state;

    if ((metadata == 0) || ((slot != SLOT_A) && (slot != SLOT_B)))
    {
        return 0U;
    }

    state = (slot == SLOT_A) ? metadata->app_a_state : metadata->app_b_state;

    /*
     * Metadata state is checked first, then the image vector table is checked
     * directly from flash. This avoids jumping into erased or wrong-slot data.
     */
    if (boot_service_image_state_allows_boot(state) == 0U)
    {
        return 0U;
    }

    return boot_service_application_is_valid(slot);
}

static uint8_t boot_service_application_is_valid(ota_slot_t slot)
{
    uint32_t base = ota_layout_get_slot_base(slot);
    uint32_t stack_pointer = *(const uint32_t *)base;
    uint32_t reset_handler = *(const uint32_t *)(base + 4UL);
    uint32_t reset_address = reset_handler & ~1UL;

    /*
     * The first vector entry must be a valid SRAM stack pointer. The alignment
     * check catches corrupted vectors before MSP is changed.
     */
    if ((stack_pointer < BOOT_SERVICE_SRAM_BASE) ||
        (stack_pointer > BOOT_SERVICE_SRAM_END) ||
        ((stack_pointer & 3UL) != 0UL))
    {
        return 0U;
    }

    /*
     * Cortex-M reset handlers run in Thumb state, so bit 0 must be set. The
     * actual reset address must also live inside the selected slot, which is why
     * each App A/App B binary must be linked for the slot it will boot from.
     */
    if (((reset_handler & 1UL) == 0UL) ||
        (ota_layout_address_in_slot(slot, reset_address, 4UL) == 0U))
    {
        return 0U;
    }

    return 1U;
}

static uint8_t boot_service_image_state_allows_boot(uint8_t state)
{
    return ((state == (uint8_t)OTA_IMAGE_VALID) ||
            (state == (uint8_t)OTA_IMAGE_PENDING)) ? 1U : 0U;
}

/*
 * Transfer control to the selected application. The bootloader disables its
 * runtime context first so the application starts with its own vector table,
 * stack pointer, and interrupt state.
 */
static void boot_service_jump_to_slot(ota_slot_t slot)
{
    uint32_t base = ota_layout_get_slot_base(slot);
    uint32_t stack_pointer = *(const uint32_t *)base;
    uint32_t reset_handler = *(const uint32_t *)(base + 4UL);
    uint32_t index;
    /* RCC timeouts require the bootloader SysTick to keep running. */
    if (HAL_RCC_DeInit() != HAL_OK)
    {
        Error_Handler();
        return;
    }

    __disable_irq();
    (void)HAL_DeInit();

    /* Clear SysTick so the bootloader tick cannot fire after the jump. */
    SysTick->CTRL = 0UL;
    SysTick->LOAD = 0UL;
    SysTick->VAL = 0UL;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;

    /* Disable and clear all pending NVIC interrupts owned by the bootloader. */
    for (index = 0UL; index < BOOT_SERVICE_NVIC_REGISTER_COUNT; index++)
    {
        NVIC->ICER[index] = 0xFFFFFFFFUL;
        NVIC->ICPR[index] = 0xFFFFFFFFUL;
    }

    /*
     * Finish entirely in assembly: no C stack access is safe after changing MSP.
     * All interrupt sources are stopped before restoring reset-like CPU masks.
     */
    /* IWDG survives the jump. Give the app a full period to take over refresh. */
    IWDG->KR = IWDG_KEY_RELOAD;
    SCB->VTOR = base;
    __DSB();
    __ISB();

    __asm volatile (
        "movs r2, #0\n"
        "msr basepri, r2\n"
        "msr msp, %0\n"
        "msr control, r2\n"
        "isb\n"
        "msr faultmask, r2\n"
        "cpsie i\n"
        "bx %1\n"
        :
        : "r" (stack_pointer), "r" (reset_handler)
        : "r2", "memory"
    );
    __builtin_unreachable();
}
