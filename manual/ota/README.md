# OTA Documentation

This set explains how the ATM application receives firmware over MQTT and how the bootloader selects, retries, and confirms an application slot. It is intended for firmware engineers, backend integrators, and deployment operators.

It reflects source reviewed on 2026-09-07, including local changes that may not yet be deployed to hardware. Hardware behavior and installed firmware revision still need verification on the intended device.

## Reading guide

| Document | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Understand component roles, Flash layout, and update stages. |
| [MQTT packet reference](mqtt-ota-packet.md) | Implement packets, responses, and retry behavior. |
| [Boot and metadata](boot-and-metadata.md) | Understand slot selection, health confirmation, watchdog recovery, and persistence. |
| [Deployment guide](deploy-guide.md) | Prepare the correct image and perform an update. |
| [Troubleshooting](troubleshooting.md) | Diagnose failures by stage and symptom. |
| [Limitations and follow-up work](integration-next-steps.md) | Identify current guarantees and remaining gaps. |

## Essential distinctions

- `OTA_PING` is an encrypted command on the command topic.
- START, DATA, FINISH, ABORT, and CONFIRM are raw binary packets on the OTA downlink.
- OTA ACK/NACK and `OTA_SLOT` are plain JSON on the OTA uplink.
- The firmware image must be linked for the opposite slot from the running application.
- Download completion, booting the target slot, and health confirmation are separate events.
- The device can confirm a healthy trial locally without a network connection.

The [encryption documentation](../encrypt/README.md) defines the command envelope. No operational keys, broker credentials, or deployment addresses are included here.

## Evidence basis

The device OTA receiver, Flash writer, metadata journal, boot selection logic, startup flow, and linker configurations were reviewed. ATM and Bootloader agree on the Flash layout and metadata field definitions. Loader behavior was checked against the local OTA application for the operator workflow.

This documentation update does not establish a tested release or certify power-loss recovery. No build, hardware programming, or live transfer is implied.
