#ifndef OTA_SERVICE_H
#define OTA_SERVICE_H

#include <stdint.h>
#include "ota_metadata.h"

/*
 * Public OTA service status.
 * Each API returns one of these values so the caller can decide whether to
 * retry, stop the update, or report an error to the server.
 */
typedef enum
{
    OTA_OK = 0,
    OTA_ERROR,
    OTA_INVALID_PARAM,
    OTA_INVALID_STATE,
    OTA_IMAGE_TOO_LARGE,
    OTA_FLASH_ERROR,
    OTA_VERIFY_FAILED
} ota_status_t;

typedef enum
{
    OTA_PACKET_START = 0,
    OTA_PACKET_DATA,
    OTA_PACKET_FINISH,
    OTA_PACKET_ABORT,
    OTA_PACKET_CONFIRM
} ota_packet_type_t;

/*
 * Runtime OTA download session.
 * This data is kept in RAM only while one firmware image is being received.
 */
typedef struct
{
    ota_slot_t slot;        /* Target slot being written. */
    uint32_t size;          /* Expected firmware image size. */
    uint32_t crc32;         /* Expected firmware image CRC32. */
    uint32_t written;       /* Number of bytes written so far. */
    uint8_t active;         /* 1 while an OTA session is running. */
} ota_session_t;

/* Load metadata and prepare the OTA service before receiving firmware. */
ota_status_t ota_service_init(void);

/* Call after each completed application cycle, after successful hardware init.
 * Healthy pending images confirm locally; a non-OK result must stop refresh. */
ota_status_t ota_service_process_boot_trial(uint8_t healthy);

/* Start a new OTA session and erase the inactive app slot. */
ota_status_t ota_service_start(uint32_t size,
                               uint32_t crc32);

/* Write one firmware chunk to the target slot at the expected offset. */
ota_status_t ota_service_write(uint32_t offset,
                               const uint8_t *data,
                               uint32_t length);

/* Validate the completed image and mark it pending for test boot. */
ota_status_t ota_service_finish(void);

/* Confirm only after the local boot trial passes, including MQTT CONFIRM calls. */
ota_status_t ota_service_confirm(void);

/* Cancel the active OTA session and mark the partial image invalid. */
ota_status_t ota_service_abort(void);

/* Report which OTA slot is currently running from the vector-table base. */
ota_status_t ota_service_get_running_slot(ota_slot_t *running_slot);

/* Parse one OTA MQTT packet and dispatch it to start/write/finish/abort/confirm. */
ota_status_t ota_service_on_packet(const uint8_t *payload,
                                   uint16_t length);
#endif /* OTA_SERVICE_H */
