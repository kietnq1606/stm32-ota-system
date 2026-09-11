# Deploy an OTA Firmware Image

Use this procedure to update an existing device through its running ATM application. Initial hardware provisioning and bootloader replacement are separate operations.

## Prerequisites

- A working bootloader and an ATM application capable of receiving OTA.
- Stable power and broker connectivity for the update.
- Matching device identity, topic configuration, and authorized broker access.
- A provisioned AES key for encrypted `OTA_PING`; see the [integration guide](../encrypt/integration-guide.md).
- A firmware image built for the target slot with compatible Flash layout, metadata, vector relocation, and watchdog behavior.
- One loader controlling the device at a time.

> **Important:** Source changes on the development machine do not update the installed firmware. In particular, firmware-side retry and alignment checks become available only after an image containing those changes is installed.

## Prepare the image

1. Query the running slot using the loader's active-slot check.
2. Select the opposite slot as the target.
3. Select the corresponding existing build configuration in STM32CubeIDE.
4. Build that configuration and export its raw BIN image.
5. Verify the image size and vector table before starting the transfer.

| Running slot | Target slot | Build configuration | Target Flash origin | Maximum image |
| --- | --- | --- | --- | --- |
| A | B | `Debug_B` | `0x08020800` | 124 KiB |
| B | A | `Debug_A` | `0x08001800` | 124 KiB |

Use `Build Configurations > Set Active` to select the configuration. The existing configurations already select different linker origins. Do not relocate an image by renaming it or modifying its first bytes.

The linker Flash length must remain 124 KiB. RAM is 48 KiB at `0x20000000`. The image starts with its application vector table, not a bootloader header or a combined whole-Flash image.

The loader accepts BIN, rejects an empty image or one shorter than 8 bytes, and rejects an image above 126976 bytes. It checks the stack pointer, Thumb reset bit, and reset address against the selected target. It calculates the image CRC32 from the complete BIN contents.

## Transfer and confirm

1. Connect the loader using the configured OTA topics.
2. Run the active-slot check and wait for `OTA_SLOT`.
3. Confirm that the displayed target is the opposite slot.
4. Select the BIN built for that target.
5. Start the upload. The loader sends START and waits for an application ACK.
6. Allow the loader to send sequential DATA packets with 246 bytes per full chunk.
7. Wait for FINISH validation and reboot. Download progress reaching 100% does not yet mean confirmation.
8. Wait for the post-boot slot report and verify that it identifies the target.
9. Allow CONFIRM retries while the application completes health observation.
10. Accept completion only when the loader reports that the target slot was confirmed.

The device can confirm locally even if the loader disconnects. A loader timeout is therefore not, by itself, evidence that the image remains pending.

## Timing expectations

The loader's post-FINISH slot wait is 60 s. Application startup includes 20 s of settling before foreground processing; initialization and network reconnection add further time.

Local confirmation requires 30 s of healthy foreground observation. The loader retries CONFIRM within a 60 s window and pauses 1 s after an unsuccessful attempt. An early CONFIRM NACK is expected while health is not ready.

These settings are not a guaranteed end-to-end deployment deadline. See the [packet reference](mqtt-ota-packet.md#loader-timing).

## Recover from interruption

1. Record the failed stage, last DATA offset, and whether an application ACK or NACK was received.
2. Recheck device connectivity and the running slot before starting another update.
3. If a download session is still active and must be discarded, use ABORT through the authorized loader.
4. Start a fresh transfer when the device is ready. A reset loses RAM download progress; persistent resume is not supported.
5. If FINISH already committed or the device rebooted, inspect slot and trial behavior rather than assuming ABORT can undo the update.

Do not erase metadata or replace the bootloader as the first response to a timeout. Use [troubleshooting](troubleshooting.md) to distinguish routing, alignment, boot, and health failures.

## Completion evidence

Record the image's target slot, size, CRC32, final reported running slot, and confirmation result. The current slot report does not identify firmware version or build ID; obtain any release-identity evidence through a separate approved process.

This guide describes build and deployment steps for an operator. It does not indicate that a build, hardware programming operation, or acceptance test was performed during documentation preparation.
