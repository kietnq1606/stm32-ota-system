# OTA Limitations and Follow-Up Work

This document separates current behavior from potential improvements. It describes gaps visible in the reviewed source, not an implementation commitment.

## Reliability boundaries

| Current behavior | Consequence | Potential improvement |
| --- | --- | --- |
| ACK/NACK contains only status | Delayed replies cannot be tied unambiguously to a packet or session | Define a versioned response with session and offset correlation |
| Device OTA subscription and replies use QoS 0 | Broker delivery and device processing are not guaranteed by loader QoS 1 | Review delivery policy together with duplicate handling |
| Single occupied receive mailbox rejects new arrivals | Bursts can drop an OTA packet before dispatch | Evaluate bounded queuing or backpressure within RAM limits |
| START, DATA, FINISH, and CONFIRM have conditional repeat handling | Retries help within defined states but do not provide exactly-once semantics | Define complete transaction recovery before adding concurrency |
| Download progress is RAM-only | A device reset requires a new transfer | Add resume only with persistent session identity and validated offset recovery |
| Metadata journal occupies one page | Rollover erase can remove every valid record | Evaluate a second independent metadata region with a compatible layout migration |
| Metadata sequence uses ordinary numeric comparison | Sequence wraparound is not handled | Specify rollover-safe ordering |
| Slot report has no image identity or confirmation field | Matching slot does not prove the intended build is running | Add versioned image identity and explicit confirmation state |

The receive-mailbox guard protects the previously accepted message; it does not queue every arriving message. Do not describe it as lossless reception.

## Security boundaries

Firmware transfer is raw and CRC32-only. There is no signature verification, authenticated manifest, monotonic firmware version, or anti-rollback rule.

The bootloader validates image state and vectors rather than recalculating image CRC32 on every boot. It should not be described as a secure-boot implementation.

Encrypted slot commands use the existing AES-GCM scheme, whose nonce and replay limitations are documented in the [security model](../encrypt/manual.md). Successful command encryption does not secure the raw OTA channel.

Potential improvements require an explicit security design: authenticated images, controlled version policy, transport protection, broker access control, and persistent nonce allocation. They are not present merely because RSA or AES code exists elsewhere in the application.

## Operational boundaries

- Health confirmation observes completed application cycles and a clear sensor-fault mask; it does not validate every product requirement.
- A healthy image can confirm without broker connectivity, so backend approval is not the sole confirmation authority.
- No bootable application means no application-based MQTT updater. The bootloader has no MQTT recovery service.
- Missing metadata defaults to slot A; it does not discover and confirm an arbitrary image in slot B.
- The loader's 60 s waits may not cover slow initialization, network recovery, or sensor recovery up to the device's 180 s trial timeout.
- The protocol rejects zero-valued image CRC32 even when mathematically valid.

## Validation still required

Hardware acceptance is not established by source review. A separately authorized validation plan should cover successful A-to-B and B-to-A transfers, lost replies, duplicate packets, interrupted download, failed health observation, exhausted boot trials, and power loss during metadata append and rollover.

Observe the defined metadata limitation rather than assuming every power-loss case must preserve a recoverable confirmed image.
