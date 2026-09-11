# Boot Selection and Metadata

This reference explains persistent OTA state and how the bootloader selects an application. It applies to the shared ATM and Bootloader layout described in the [architecture](architecture.md).

## Metadata fields

| Field | Meaning |
| --- | --- |
| `magic` | Format marker `0x4341414F` |
| `sequence` | Journal ordering counter |
| `crc32` | Integrity check for the metadata record |
| `active_slot` | Preferred next boot slot |
| `confirmed_slot` | Last confirmed slot |
| `pending_slot` | Trial slot, or `0xFF` when none |
| `boot_try_count` | Number of recorded trial boots |
| `app_a_state`, `app_b_state` | Image state for each slot |
| `app_a_size`, `app_b_size` | Image length in bytes |
| `app_a_crc32`, `app_b_crc32` | Expected image CRC32 |

Slot identifiers in metadata are A = 0 and B = 1. They differ from the string values `"A"` and `"B"` in MQTT reports.

| Image state | Value | Boot eligible |
| --- | --- | --- |
| EMPTY | 0 | No |
| WRITING | 1 | No |
| PENDING | 2 | Yes, subject to boot selection and vector checks |
| VALID | 3 | Yes, subject to vector checks |
| INVALID | 4 | No |

## Journal behavior

Metadata occupies one 2 KiB Flash page. Records use the shared native metadata representation with half-word-aligned storage; this is not a portable MQTT structure.

The reader scans records, rejects invalid marker/CRC/state values, and selects the valid record with the highest sequence number. Record CRC excludes the CRC field itself but includes the remaining stored representation. Keep the metadata definitions and layout compatible between both firmware components.

A save appends a new record into erased space, calculates its CRC, programs it, and validates it. Once the page is full, the writer erases that same page before writing a replacement record.

> **Warning:** There is no second metadata page. A power interruption during journal rollover can destroy all previous valid records. The journal does not provide atomic persistence across page erase.

Sequence comparison uses ordinary unsigned ordering; wraparound is not specially handled.

## Defaults

When no valid record exists, defaults select slot A as both active and confirmed, mark A VALID, mark B EMPTY, and clear pending state. Initial image sizes and image CRC values are zero.

Defaults do not prove that a valid application was programmed. The bootloader still checks slot A's vectors. Loss of metadata is not a general recovery mechanism for a device whose only usable image is in slot B.

## Boot selection

1. If a pending slot exists, check that fewer than three trial boots have been recorded and that its state and vectors are acceptable.
2. Before jumping to a pending image, increment the trial count and persist the record. A persistence failure prevents that trial jump.
3. If the pending image is invalid or three trial boots have already been consumed, mark it INVALID, clear pending state, and restore the confirmed slot as active.
4. Try the active slot.
5. If needed, try the confirmed slot.
6. If needed, try slot A and then slot B, subject to each image's state and vector checks.
7. If no eligible image exists, enter the error path.

Three trial starts are allowed. On a subsequent boot with the count already at three, the pending slot is rejected. An image can also be rejected earlier if its vector checks fail.

The final A/B scan does not ignore image-state restrictions and is not a network recovery mode.

## Image validation

The bootloader checks:

- Initial stack pointer is between `0x20000000` and `0x2000C000`, inclusive, and aligned to 4 bytes.
- Reset handler has the Thumb bit set.
- The reset-handler address after clearing bit 0 has a 4-byte range inside the selected slot.
- Image state permits boot.

The bootloader does not recalculate the stored image CRC32 or verify a digital signature before each jump. Whole-image CRC checking occurs during FINISH in the ATM application.

The loader checks the reset address is inside the target slot, but does not apply the bootloader's additional 4-byte range condition at the upper boundary. Passing loader validation is therefore not identical to passing every bootloader check.

## Application handoff

The bootloader deinitializes its clock/runtime context, disables and clears interrupt sources, sets the vector-table base, installs the application's stack pointer, and branches to its reset handler. The application startup uses its own linked vector table.

The independent watchdog remains active across the handoff. The application must take over watchdog refresh during initialization and normal foreground execution.

## Trial health and confirmation

A pending application begins trial timing during OTA initialization. Startup includes a 20 s settling interval with watchdog refresh. This interval is not counted as the required healthy observation.

After a completed application cycle with no sensor fault flags, the health monitor starts a continuous 30 s observation period. A sensor fault clears accumulated healthy observation. Network availability is not part of the health criterion.

When the healthy period completes, the application confirms locally: mark the running image VALID, set active and confirmed to that slot, clear pending state and trial count, and persist the result. MQTT CONFIRM cannot bypass this gate.

The overall trial timeout is 180 s from OTA trial initialization. If it expires, or required confirmation persistence fails, the application enters an error path instead of continuing normal watchdog refresh.

## Watchdog behavior

Both components configure watchdog prescaler 256 and reload 4095. The watchdog is refreshed at defined initialization progress points and after foreground work completes. Error paths do not continue normal refresh.

Timeout depends on the actual low-speed internal oscillator frequency. It is not documented here as an exact measured number of seconds. A stalled or failing trial can reset and return control to the bootloader, which applies the recorded attempt limit.

Rollback still depends on a reset and usable confirmed image/metadata. It is not an unconditional recovery guarantee after arbitrary Flash corruption.
