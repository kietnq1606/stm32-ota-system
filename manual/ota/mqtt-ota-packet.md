# MQTT OTA Packet Reference

This reference defines the current OTA wire format. Send only one outstanding OTA transaction per device.

## Topics and encoding

| Topic | Direction | Payload |
| --- | --- | --- |
| `atm/<DEVICE_ID>/command` | Loader to device | Encrypted command containing `OTA_PING` |
| `atm/<DEVICE_ID>/response` | Device to loader/backend | Encrypted command ACK, separate from OTA status |
| `atm/<DEVICE_ID>/ota/down` | Loader to device | Raw binary OTA packet |
| `atm/<DEVICE_ID>/ota/up` | Device to loader | Plain JSON ACK/NACK or slot report |

Exact configured downlink strings determine device routing. The device does not infer a new downlink subscription merely because a client uses a different device ID.

The reviewed device subscribes to OTA downlink at QoS 0 and publishes replies at QoS 0 with retain disabled. The loader publishes at QoS 1 and subscribes at QoS 1. Broker acknowledgment of the loader's publication is not a device application ACK.

Do not retain OTA packets or command requests. Retained control packets can be delivered on a later subscription when they are no longer appropriate.

## Packet framing

The first byte is an unsigned packet type. All multibyte integers are unsigned 32-bit little-endian. Publish the actual bytes, not an ASCII hex string, JSON wrapper, or Base64 text.

| Packet | Type byte | Exact payload length |
| --- | --- | --- |
| START | `0x00` | 9 bytes |
| DATA | `0x01` | 9 + data length bytes |
| FINISH | `0x02` | 1 byte |
| ABORT | `0x03` | 1 byte |
| CONFIRM | `0x04` | 1 byte |

### START

| Byte offsets | Field | Meaning |
| --- | --- | --- |
| 0 | Type | `0x00` |
| 1-4 | Image size | Number of firmware-image bytes |
| 5-8 | Image CRC32 | CRC of exactly those bytes |

Image size must be nonzero and no greater than 126976 bytes. The receiver also rejects a CRC32 value of zero, even though zero is a possible CRC result.

START has no target-slot field. The device selects the inactive slot relative to the running application. A pending trial or a mismatch between running and confirmed slots prevents a new transfer.

### DATA

| Byte offsets | Field | Meaning |
| --- | --- | --- |
| 0 | Type | `0x01` |
| 1-4 | Offset | Byte offset from the target slot base |
| 5-8 | Data length | Number of bytes after the header |
| 9 onward | Data | Firmware-image bytes |

Data length must be nonzero and equal the actual payload length minus 9. The range must fit inside the announced image.

The application receive limit is 256 bytes, including the 9-byte OTA header. Use 246 data bytes for each full chunk, producing a 255-byte packet. New offsets are then `0, 246, 492, ...`.

Every offset must be even. A non-final chunk must have even length; only the final chunk may be odd. The Flash writer pads an odd final byte with `0xFF` for half-word programming. That padding byte is not part of the announced size or image CRC.

A 247-byte chunk fits the receive buffer but cannot be used repeatedly: the next offset becomes odd. Older 1024-byte chunks exceed the receive limit and can be discarded before an OTA NACK is generated.

### FINISH

FINISH verifies that all bytes have arrived and that the complete image CRC matches. It commits PENDING metadata before returning success.

The device resets only after its ACK publish call reports success, then waits 500 ms. QoS 0 still does not guarantee delivery to the loader. A locally failed publish is logged and does not trigger this software reset.

### ABORT

ABORT applies only while a download session is active. It marks the target image INVALID and closes the session. With no active download it returns NACK.

ABORT is not an idempotent rollback operation and cannot cancel an already committed pending boot.

### CONFIRM

CONFIRM has no slot parameter. It operates on the running slot.

A pending image must pass the local health gate before confirmation succeeds. If the running image is already confirmed and no pending slot remains, CONFIRM succeeds again. An early NACK can mean that health observation has not completed.

## Image CRC32

| Parameter | Value |
| --- | --- |
| Initial value | `0xFFFFFFFF` |
| Reflected polynomial | `0xEDB88320` |
| Processing | LSB first, right shift |
| Final XOR | `0xFFFFFFFF` |
| Input | Exactly the firmware-image bytes |
| Equivalent loader calculation | `zlib.crc32(image) & 0xFFFFFFFF` |

This is independent of the command CRC16 used inside encrypted `OTA_PING`.

## Replies

Application success:

```json
{"status":"ACK"}
```

Application failure:

```json
{"status":"NACK"}
```

Replies contain no offset, packet type, session ID, error code, or image identity. They cannot unambiguously correlate delayed replies from different operations. Serialize transfers and do not run two loaders against one device.

Packets rejected before OTA dispatch, including oversized packets or arrivals while the receive mailbox is occupied, may produce no reply.

## Slot report

Example for a running slot A:

```json
{"type":"OTA_SLOT","active_slot":"A","active_base":"0x08001800"}
```

For slot B, `active_slot` is `B` and `active_base` is `0x08020800`. The report is generated after boot or reconnect when online, and on encrypted `OTA_PING`. An unrecognized vector-table base prevents the report.

`OTA_SLOT` does not contain a firmware version, image CRC, confirmation state, or session identifier.

## Retransmissions

| Operation | Accepted repeat |
| --- | --- |
| START | Same size and CRC while the existing session has written zero bytes; no second erase |
| DATA | Entire range is already written and Flash bytes match the request; no second programming |
| FINISH | Completed session remains in RAM and metadata still marks its target pending; allows re-ACK before reset |
| CONFIRM | Running slot is already confirmed with no pending slot |
| ABORT | No special repeat handling; NACK after the session has closed |

Out-of-order future DATA is rejected. A duplicate overlapping unwritten bytes is rejected. The protocol does not support download resume after device reset.

## Loader timing

| Operation | Current setting |
| --- | --- |
| Broker connection wait | 10 s |
| Subscription wait | 10 s |
| Publish completion wait | 5 s |
| Per-attempt OTA ACK wait | 5 s |
| Default packet retries | 5 retries after the initial attempt |
| Initial slot-report wait | 15 s |
| Slot-report wait after FINISH | 60 s |
| CONFIRM retry window | 60 s, with a 1 s pause between unsuccessful attempts |

Publish and ACK waits are separate. A blocking attempt can finish after the CONFIRM retry window expires; the window controls whether another attempt starts.

The loader preserves responses while retrying the same packet. During FINISH it can accept a slot report as evidence of reboot if the ACK was missed, then checks the reported slot against the target. A slot report alone is not proof of firmware version or successful health confirmation.

## Internal status reference

These values are available to local diagnostics, not transmitted in the current NACK:

| Value | Name | Meaning |
| --- | --- | --- |
| 0 | `OTA_OK` | Operation succeeded |
| 1 | `OTA_ERROR` | Generic declared error |
| 2 | `OTA_INVALID_PARAM` | Invalid framing, size, range, or alignment |
| 3 | `OTA_INVALID_STATE` | Operation conflicts with session or trial state |
| 4 | `OTA_IMAGE_TOO_LARGE` | Image exceeds the slot |
| 5 | `OTA_FLASH_ERROR` | Erase, program, or metadata operation failed |
| 6 | `OTA_VERIFY_FAILED` | Read-back, image CRC, or trial validation failed |

A declared status need not be produced by every operation.
