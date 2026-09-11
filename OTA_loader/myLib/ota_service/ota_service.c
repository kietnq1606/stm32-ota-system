#include "ota_service.h"

#include <stddef.h>
#include <string.h>

#include "ota_flash.h"
#include "stm32f1xx_hal.h"

#define OTA_BOOT_HEALTHY_MS       30000U
#define OTA_BOOT_TRIAL_TIMEOUT_MS 180000U

static ota_session_t  s_ota_session;
static ota_metadata_t s_ota_metadata;
/* Trial timing is RAM-only; the existing flash metadata counts reset attempts. */
static uint32_t s_boot_trial_started_ms;
static uint32_t s_boot_healthy_since_ms;
static uint8_t s_boot_trial_pending;
static uint8_t s_boot_health_started;
static uint8_t s_boot_confirm_ready;

static ota_slot_t ota_service_get_inactive_slot(void);
static void ota_service_set_slot_image(ota_slot_t slot,
                                       ota_image_state_t state,
                                       uint32_t size,
                                       uint32_t crc32);

ota_status_t ota_service_init(void)
{
    ota_slot_t running_slot;

    /* Clear any RAM-only session from a previous boot. */
    memset(&s_ota_session, 0, sizeof(s_ota_session));
    s_boot_trial_pending = 0U;
    s_boot_health_started = 0U;
    s_boot_confirm_ready = 0U;

    /* Load persistent OTA state; create default metadata if Flash is empty. */
    if (!ota_metadata_load(&s_ota_metadata))
    {
        ota_metadata_set_defaults(&s_ota_metadata);

        if (!ota_metadata_save(&s_ota_metadata))
        {
            return OTA_FLASH_ERROR;
        }
    }

    if (s_ota_metadata.pending_slot != OTA_SLOT_NONE)
    {
        if ((ota_service_get_running_slot(&running_slot) != OTA_OK) ||
            (s_ota_metadata.pending_slot != (uint8_t)running_slot))
        {
            return OTA_INVALID_STATE;
        }
        s_boot_trial_pending = 1U;
        s_boot_trial_started_ms = HAL_GetTick();
    }

    return OTA_OK;
}

ota_status_t ota_service_process_boot_trial(uint8_t healthy)
{
    uint32_t now_ms;

    if (s_boot_trial_pending == 0U)
    {
        return OTA_OK;
    }

    now_ms = HAL_GetTick();
    /* Allow bounded sensor recovery, but never feed a failed trial forever. */
    if ((uint32_t)(now_ms - s_boot_trial_started_ms) >= OTA_BOOT_TRIAL_TIMEOUT_MS)
    {
        return OTA_VERIFY_FAILED;
    }
    if (healthy == 0U)
    {
        s_boot_health_started = 0U;
        s_boot_confirm_ready = 0U;
        return OTA_OK;
    }
    if (s_boot_health_started == 0U)
    {
        /* The first completed cycle starts observation, excluding init delays. */
        s_boot_healthy_since_ms = now_ms;
        s_boot_health_started = 1U;
        return OTA_OK;
    }
    if ((uint32_t)(now_ms - s_boot_healthy_since_ms) < OTA_BOOT_HEALTHY_MS)
    {
        return OTA_OK;
    }

    /* Both local and remote confirmation use the same readiness gate. */
    s_boot_confirm_ready = 1U;
    return ota_service_confirm();
}

ota_status_t ota_service_start(uint32_t size,
                               uint32_t crc32)
{
    ota_slot_t target_slot;
    ota_slot_t running_slot;

    /* A lost START ACK must not cause a second erase. */
    if (s_ota_session.active)
    {
        if ((s_ota_session.written == 0UL) &&
            (s_ota_session.size == size) && (s_ota_session.crc32 == crc32))
        {
            return OTA_OK;
        }
        return OTA_INVALID_STATE;
    }

    /* The server must provide image size and expected validation data. */
    if ((size == 0UL) || (crc32 == 0UL))
    {
        return OTA_INVALID_PARAM;
    }

    /* Reject images that cannot fit in the OTA app slot. */
    if (!ota_flash_is_valid_image_size(size))
    {
        return OTA_IMAGE_TOO_LARGE;
    }

    /* Refresh metadata so the inactive slot is selected from current state. */
    if (!ota_metadata_load(&s_ota_metadata))
    {
        return OTA_INVALID_STATE;
    }

    /* A trial must not erase its confirmed fallback, even on a new START packet. */
    if ((s_ota_metadata.pending_slot != OTA_SLOT_NONE) ||
        (ota_service_get_running_slot(&running_slot) != OTA_OK) ||
        (s_ota_metadata.confirmed_slot != (uint8_t)running_slot))
    {
        return OTA_INVALID_STATE;
    }

    target_slot = ota_service_get_inactive_slot();

    /* Erase the target slot before receiving chunks. */
    if (!ota_flash_erase_slot(target_slot))
    {
        return OTA_FLASH_ERROR;
    }

    /* Mark the slot as being written so bootloader will not boot it yet. */
    ota_service_set_slot_image(target_slot,
                               OTA_IMAGE_WRITING,
                               size,
                               crc32);
    s_ota_metadata.pending_slot = OTA_SLOT_NONE;
    s_ota_metadata.boot_try_count = 0;
    s_ota_metadata.sequence++;

    if (!ota_metadata_save(&s_ota_metadata))
    {
        return OTA_FLASH_ERROR;
    }

    /* Keep the transfer progress in RAM until finish or abort. */
    s_ota_session.slot = target_slot;
    s_ota_session.size = size;
    s_ota_session.crc32 = crc32;
    s_ota_session.written = 0;
    s_ota_session.active = 1;

    return OTA_OK;
}

ota_status_t ota_service_write(uint32_t offset,
                               const uint8_t *data,
                               uint32_t length)
{
    /* A chunk is valid only after ota_service_start(). */
    if (!s_ota_session.active)
    {
        return OTA_INVALID_STATE;
    }

    if ((data == NULL) || (length == 0UL))
    {
        return OTA_INVALID_PARAM;
    }

    /* Do not allow a chunk to pass the announced firmware size. */
    if (offset > s_ota_session.size)
    {
        return OTA_INVALID_PARAM;
    }

    if (length > (s_ota_session.size - offset))
    {
        return OTA_INVALID_PARAM;
    }

    /* Only the final chunk may need an erased padding byte. */
    if (((offset & 1UL) != 0UL) ||
        (((length & 1UL) != 0UL) && (length != s_ota_session.size - offset)))
    {
        return OTA_INVALID_PARAM;
    }

    /* ACK a verified retransmission without programming Flash twice. */
    if (offset < s_ota_session.written)
    {
        if (length > s_ota_session.written - offset)
        {
            return OTA_INVALID_STATE;
        }
        return ota_flash_verify(s_ota_session.slot, offset, data, length)
             ? OTA_OK : OTA_VERIFY_FAILED;
    }
    if (offset != s_ota_session.written)
    {
        return OTA_INVALID_STATE;
    }

    /* Write chunk to Flash, then read it back through verify. */
    if (!ota_flash_write(s_ota_session.slot, offset, data, length))
    {
        return OTA_FLASH_ERROR;
    }

    if (!ota_flash_verify(s_ota_session.slot, offset, data, length))
    {
        return OTA_VERIFY_FAILED;
    }

    s_ota_session.written += length;
    return OTA_OK;
}

ota_status_t ota_service_finish(void)
{
    uint32_t actual_crc32;

    /* Re-ACK FINISH if metadata was committed but publishing its ACK failed. */
    if (!s_ota_session.active)
    {
        if ((s_ota_session.size != 0UL) &&
            (s_ota_session.written == s_ota_session.size) &&
            (s_ota_metadata.pending_slot == (uint8_t)s_ota_session.slot))
        {
            return OTA_OK;
        }
        return OTA_INVALID_STATE;
    }

    if (s_ota_session.written != s_ota_session.size)
    {
        return OTA_INVALID_STATE;
    }

    /* Validate the complete image against the CRC32 sent by the server. */
    actual_crc32 = ota_flash_crc32(s_ota_session.slot, s_ota_session.size);
    if (actual_crc32 != s_ota_session.crc32)
    {
        ota_service_set_slot_image(s_ota_session.slot,
                                   OTA_IMAGE_INVALID,
                                   s_ota_session.size,
                                   s_ota_session.crc32);
        s_ota_metadata.sequence++;
        s_ota_session.active = 0;
        (void)ota_metadata_save(&s_ota_metadata);
        return OTA_VERIFY_FAILED;
    }

    /* Mark new image as pending so bootloader performs one test boot. */
    ota_service_set_slot_image(s_ota_session.slot,
                               OTA_IMAGE_PENDING,
                               s_ota_session.size,
                               s_ota_session.crc32);
    s_ota_metadata.pending_slot = (uint8_t)s_ota_session.slot;
    s_ota_metadata.active_slot = (uint8_t)s_ota_session.slot;
    s_ota_metadata.boot_try_count = 0U;
    s_ota_metadata.sequence++;

    if (!ota_metadata_save(&s_ota_metadata))
    {
        return OTA_FLASH_ERROR;
    }

    s_ota_session.active = 0U;

    /* The caller sends the FINISH ACK before resetting the MCU. */
    return OTA_OK;
}

ota_status_t ota_service_confirm(void)
{
    ota_slot_t running_slot;

    if (s_ota_session.active)
    {
        return OTA_INVALID_STATE;
    }

    if (ota_service_get_running_slot(&running_slot) != OTA_OK)
    {
        return OTA_INVALID_STATE;
    }

    if (!ota_metadata_load(&s_ota_metadata))
    {
        return OTA_INVALID_STATE;
    }

    if (s_ota_metadata.pending_slot == OTA_SLOT_NONE)
    {
        if (s_ota_metadata.confirmed_slot == (uint8_t)running_slot)
        {
            return OTA_OK;
        }

        return OTA_INVALID_STATE;
    }

    if (s_ota_metadata.pending_slot != (uint8_t)running_slot)
    {
        return OTA_INVALID_STATE;
    }

    /* MQTT CONFIRM cannot bypass the local health observation period. */
    if ((s_boot_trial_pending == 0U) || (s_boot_confirm_ready == 0U))
    {
        return OTA_INVALID_STATE;
    }

    if (running_slot == SLOT_A)
    {
        s_ota_metadata.app_a_state = (uint8_t)OTA_IMAGE_VALID;
    }
    else
    {
        s_ota_metadata.app_b_state = (uint8_t)OTA_IMAGE_VALID;
    }

    s_ota_metadata.confirmed_slot = (uint8_t)running_slot;
    s_ota_metadata.active_slot = (uint8_t)running_slot;
    s_ota_metadata.pending_slot = OTA_SLOT_NONE;
    s_ota_metadata.boot_try_count = 0U;
    s_ota_metadata.sequence++;

    if (!ota_metadata_save(&s_ota_metadata))
    {
        return OTA_FLASH_ERROR;
    }

    /* Stop checking only after confirmation has been persisted successfully. */
    s_boot_trial_pending = 0U;
    s_boot_confirm_ready = 0U;
    return OTA_OK;
}

ota_status_t ota_service_get_running_slot(ota_slot_t *running_slot)
{
    uint32_t vector_base;

    if (running_slot == NULL)
    {
        return OTA_INVALID_PARAM;
    }

    /*
     * SystemInit() programs VTOR with the app vector-table base. This tells us
     * which linked slot is currently executing.
     */
    vector_base = SCB->VTOR;
    if (vector_base == APP_A_BASE_ADDR)
    {
        *running_slot = SLOT_A;
        return OTA_OK;
    }

    if (vector_base == APP_B_BASE_ADDR)
    {
        *running_slot = SLOT_B;
        return OTA_OK;
    }

    return OTA_INVALID_STATE;
}

ota_status_t ota_service_abort(void)
{
    /* Abort is only meaningful while a download is in progress. */
    if (!s_ota_session.active)
    {
        return OTA_INVALID_STATE;
    }

    if (!ota_metadata_load(&s_ota_metadata))
    {
        ota_metadata_set_defaults(&s_ota_metadata);
    }

    /* Keep the old active slot unchanged and invalidate the partial image. */
    ota_service_set_slot_image(s_ota_session.slot,
                               OTA_IMAGE_INVALID,
                               s_ota_session.size,
                               s_ota_session.crc32);
    s_ota_metadata.pending_slot = OTA_SLOT_NONE;
    s_ota_metadata.boot_try_count = 0U;
    s_ota_metadata.sequence++;

    s_ota_session.active = 0U;

    if (!ota_metadata_save(&s_ota_metadata))
    {
        return OTA_FLASH_ERROR;
    }

    return OTA_OK;
}

static ota_slot_t ota_service_get_inactive_slot(void)
{
    ota_slot_t running_slot;

    /*
     * Prefer the real running slot over metadata. Stale metadata from a failed
     * OTA must never make the app erase the slot it is currently executing.
     */
    if (ota_service_get_running_slot(&running_slot) == OTA_OK)
    {
        return (running_slot == SLOT_A) ? SLOT_B : SLOT_A;
    }

    /* Fall back to metadata only if the vector table does not identify a slot. */
    return (s_ota_metadata.active_slot == (uint8_t)SLOT_A) ? SLOT_B : SLOT_A;
}

static void ota_service_set_slot_image(ota_slot_t slot,
                                       ota_image_state_t state,
                                       uint32_t size,
                                       uint32_t crc32)
{
    /* Store image metadata in the compact per-slot fields. */
    if (slot == SLOT_A)
    {
        s_ota_metadata.app_a_state = (uint8_t)state;
        s_ota_metadata.app_a_size = size;
        s_ota_metadata.app_a_crc32 = crc32;
    }
    else
    {
        s_ota_metadata.app_b_state = (uint8_t)state;
        s_ota_metadata.app_b_size = size;
        s_ota_metadata.app_b_crc32 = crc32;
    }
}

static uint32_t ota_service_read_u32_le(const uint8_t *data)
{
    /* Decode a 32-bit little-endian field from the MQTT payload. */
    return ((uint32_t)data[0]) |
           ((uint32_t)data[1] << 8U) |
           ((uint32_t)data[2] << 16U) |
           ((uint32_t)data[3] << 24U);
}

/*
 * OTA packet format:
 *
 * START:
 *   byte 0      : OTA_PACKET_START
 *   byte 1..4   : image size, little-endian uint32
 *   byte 5..8   : image crc32, little-endian uint32
 *
 * DATA:
 *   byte 0      : OTA_PACKET_DATA
 *   byte 1..4   : offset, little-endian uint32
 *   byte 5..8   : data length, little-endian uint32
 *   byte 9..n   : firmware bytes
 *
 * FINISH:
 *   byte 0      : OTA_PACKET_FINISH
 *
 * ABORT:
 *   byte 0      : OTA_PACKET_ABORT
 *
 * CONFIRM:
 *   byte 0      : OTA_PACKET_CONFIRM
 */
ota_status_t ota_service_on_packet(const uint8_t *payload,
                                   uint16_t length)
{
    ota_packet_type_t type;

    /* Reject empty or missing MQTT payloads before reading the packet type. */
    if ((payload == NULL) || (length == 0U)){
        return OTA_INVALID_PARAM;
    }

    /* The first byte selects which OTA operation this packet carries. */
    type = (ota_packet_type_t)payload[0];

    switch (type)
    {
        case OTA_PACKET_START:
        {
            uint32_t size;
            uint32_t crc32;

            /* START has only fixed metadata: type, image size, and CRC32. */
            if (length != 9U){
                return OTA_INVALID_PARAM;
            }

            size = ota_service_read_u32_le(&payload[1]);
            crc32 = ota_service_read_u32_le(&payload[5]);

            /* Erase the inactive slot and open a new OTA session. */
            return ota_service_start(size, crc32);
        }

        case OTA_PACKET_DATA:
        {
            uint32_t offset;
            uint32_t data_length;
            const uint8_t *data;

            /* DATA must include type, offset, length, and at least one byte. */
            if (length < 9U){
                return OTA_INVALID_PARAM;
            }

            offset = ota_service_read_u32_le(&payload[1]);
            data_length = ota_service_read_u32_le(&payload[5]);

            /* Zero-length chunks are not useful and may hide sender bugs. */
            if (data_length == 0UL){
                return OTA_INVALID_PARAM;
            }

            /* The announced chunk size must match the MQTT payload size. */
            if (data_length != ((uint32_t)length - 9UL)){
                return OTA_INVALID_PARAM;
            }

            data = &payload[9];

            /* Write this chunk at the requested image offset. */
            return ota_service_write(offset, data, data_length);
        }

        case OTA_PACKET_FINISH:
            /* FINISH carries no extra fields; it validates and reboots. */
            if (length != 1U){
                return OTA_INVALID_PARAM;
            }
            return ota_service_finish();

        case OTA_PACKET_ABORT:
            /* ABORT carries no extra fields; it invalidates the partial image. */
            if (length != 1U){
                return OTA_INVALID_PARAM;
            }
            return ota_service_abort();

        case OTA_PACKET_CONFIRM:
            /* CONFIRM carries no extra fields; it accepts the test-boot image. */
            if (length != 1U){
                return OTA_INVALID_PARAM;
            }
            return ota_service_confirm();

        default:
            /* Unknown packet types are ignored as invalid OTA commands. */
            return OTA_INVALID_PARAM;
    }
}
