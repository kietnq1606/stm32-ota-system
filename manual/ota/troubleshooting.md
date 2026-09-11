# OTA Troubleshooting

Use this guide to identify the stage that failed before changing firmware or hardware state.

## Payload is visible, but no device reply appears

**Possible causes:** Wrong exact topic, offline device, oversized packet, occupied receive mailbox, or reply publish failure.

**Verification:**

1. Confirm the device is connected to the same broker.
2. Check the exact OTA downlink and uplink topics.
3. Inspect payload byte length and type without converting binary into text.
4. Distinguish MQTT PUBACK from an application `{"status":"ACK"}`.
5. Look for `[OTA] reply failed status=<number>` in local device logs.

**Resolution:** Use raw OTA bytes, subscribe before sending, use 246-byte full DATA chunks, and serialize transfers. A packet dropped before OTA dispatch may time out without NACK. If encrypted slot checking also fails, use [encryption troubleshooting](../encrypt/troubleshooting.md).

## Active-slot check times out

**Possible causes:** Plaintext `OTA_PING`, invalid envelope/CRC, wrong subscription, or an unexpected vector-table base.

**Verification:** Check command encoding and listen on the OTA uplink, not only the command response topic. A command ACK can arrive even when slot-report publication fails.

**Resolution:** Send the encrypted command defined in the [wire protocol](../encrypt/wire-protocol.md) and verify the running application's vector relocation.

## START returns NACK

**Possible causes:** Zero size or CRC, image too large, an existing incompatible session, unreadable metadata, an unconfirmed trial, running/confirmed-slot mismatch, or Flash erase/persistence failure.

**Verification:** Compare size and CRC, identify the running slot, and determine whether a previous download or trial is still active. Generic NACK does not carry the internal cause.

**Resolution:** Wait for a healthy trial to confirm, or abort an in-progress download when appropriate. Retry identical START only while that session has written zero bytes. Do not erase metadata to clear an unexplained NACK.

## DATA stops at offset 247

**Possible cause:** A sender uses odd-sized non-final chunks.

**Verification:** Inspect the full chunk length and offset sequence. Offset 247 is odd and incompatible with half-word programming.

**Resolution:** Use 246-byte full chunks. The updated receiver rejects odd offsets and odd non-final lengths before programming. Older installed firmware may behave differently.

## DATA returns NACK after an ACK timeout

**Possible causes:** A retransmission has different bytes, spans unwritten data, uses a future offset, or the installed receiver predates duplicate handling.

**Verification:** Compare the retransmitted offset and bytes with the original packet. Confirm the running firmware contains the current retry behavior.

**Resolution:** Retry the exact packet. The current receiver accepts already written ranges only after read-back equality. If the device reset, restart from START because session progress is not persisted.

## FINISH returns NACK or the device does not reboot

**Possible causes:** Incomplete image, CRC mismatch, metadata failure, or failed ACK publication.

**Verification:** Check the acknowledged byte count and the CRC32 over exactly the BIN bytes. Inspect the local reply-failure log. A successfully accepted publish is required before software reset.

**Resolution:** Retry FINISH when its response was lost and the completed session remains available in RAM with its target still pending. If integrity failed, restart the transfer with the correct image. A broker-level publish success cannot prove the FINISH operation completed.

## Post-boot slot is wrong or no slot report appears

**Possible causes:** Wrong-slot image, invalid vectors, exhausted trials, initialization failure, lost slot report, or network recovery beyond the loader timeout.

**Verification:** Compare the image origin and vectors with the intended slot. Review boot attempts and startup logs. Allow for the 20 s settling interval and network startup.

**Resolution:** Recheck the running slot after reconnect. A fallback slot report indicates the target is not currently running; rebuild for the correct slot or investigate trial failure. Avoid repeated blind uploads.

## CONFIRM returns NACK shortly after boot

**Possible cause:** The 30 s continuous healthy observation has not completed, or a sensor fault restarted it.

**Verification:** Check elapsed healthy foreground time and sensor-fault status. Startup settling is not healthy observation.

**Resolution:** Allow bounded retries. A network connection alone does not satisfy the health gate. If confirmation continues to fail, investigate initialization, sensor health, and metadata persistence rather than forcing confirmation.

## Loader reports failure, but the new image keeps running

**Possible causes:** The device confirmed locally, or the loader missed an ACK/report.

**Verification:** Reconnect and query the running slot. A slot report identifies execution location but does not directly expose persistent confirmation state.

**Resolution:** Inspect device diagnostics or perform the authorized CONFIRM exchange. Do not assume a loader timeout rolled back the update.

## Device repeatedly resets

**Possible causes:** Trial initialization failure, foreground stall, sensor-health timeout, failed metadata write, or no bootable image.

**Verification:** Check startup diagnostics and trial count without erasing state. The bootloader allows three pending trial starts.

**Resolution:** Diagnose the failing application and confirmed-image availability. If no usable application exists, recovery requires a separately authorized hardware programming procedure; there is no MQTT recovery mode in the bootloader.

## Evidence to collect

Retain stage, offset, packet length, image size/CRC32, target and reported slots, response type, and relevant timestamps. Keep credentials, AES keys, and sensitive command contents out of shared logs.
