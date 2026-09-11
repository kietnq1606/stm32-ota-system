# Alarm OTA Loader — User Manual

## Before you begin

Make sure you have:

- The app supplied by your support team for the device you want to update. Its connection settings are included in the app; contact support if those settings need to change.
- The update file supplied for that device. The app accepts files ending in `.bin`.
- A stable network connection for the computer and device.
- Time for the device to restart during the update.

Keep the device powered on until the app reports the final result. If you are unsure which device the app is configured to update, confirm with your support team before continuing.

## Understand the main controls

| Control or field | What you use it for |
| --- | --- |
| **Check Active Slot** | Read the device's current update location before choosing a file. |
| **Active slot** | See which location, A or B, the device currently uses. |
| **OTA target slot** | See which location will receive the update. The app chooses it automatically. |
| **Required linker** | Information your support team may need to supply the correct update file. You do not edit this field. |
| **Device slot** | View the device's reported running location. |
| **Browse BIN File** | Choose the update file on your computer. |
| **File size** | Check the size of the selected file. |
| **Target check** | See whether the app accepts the file for the selected update location. |
| **Upload Firmware** | Start the update. |
| **Cancel OTA** | Request cancellation while an update is running. |

The device has two update locations, called slots: A and B. If it currently uses A, the app updates B. If it currently uses B, the app updates A. You do not choose the destination manually.

## Update the device

### Step 1: Open the app

1. Double-click `STM32_OTA_Firmware_Loader.exe`. You do not need to install Python or copy separate configuration files.
2. Confirm that the main window appears.

The app opens in Light mode. Select **Dark** if you prefer a dark appearance; select **Light** to switch back.

### Step 2: Check the device

1. Select **Check Active Slot**.
2. Wait for the **Device slot detected** message.
3. Note whether **OTA target slot** shows **Slot A** or **Slot B**.
4. Dismiss the message.

If the check fails, follow [Resolve common problems](#resolve-common-problems) before continuing.

**Disconnected** after a successful check is normal. The app reconnects when you start an update. The initial **Ready** label alone does not mean the device has been checked.

### Step 3: Choose the update file

1. Select **Browse BIN File**.
2. Choose the `.bin` file supplied for your device and the slot shown in **OTA target slot**. You can also drag the file into the app's file drop area.
3. Dismiss the **BIN linker verified** message if it appears.
4. Confirm that **Target check** shows **Valid for slot A** or **Valid for slot B**, matching the target.
5. Confirm that the status reads **Ready to upload firmware** and **Upload Firmware** is available.

If you received separate files for slots A and B, choose the one matching **OTA target slot**. If you cannot identify the correct file, send your support team the target slot and **Required linker** value. Do not rename a rejected file to make it appear suitable.

The app's file check does not replace your support team's approval of the update for your device.

### Step 4: Start the update

1. Select **Upload Firmware**.
2. Leave the app open and keep the computer connected to the network.
3. Wait while the progress bar advances and the device restarts.

**Cancel OTA** appears while the update is running. Other update controls are temporarily unavailable.

> **Important:** Reaching 100% does not mean the update is complete. Keep the device powered on and wait for the final result message.

The messages **Waiting for device reboot and slot report...** and **Waiting for device health confirmation** mean the app is still checking the result. No additional button press is required during these stages.

### Step 5: Confirm success

1. Wait for **OTA successful** and the message **Firmware installed and confirmed on slot A** or **Firmware installed and confirmed on slot B**.
2. Dismiss the message.
3. Select **Check Active Slot** again to refresh the device information.
4. Confirm that **Active slot** matches the slot named in the success message.
5. Check that the device performs its normal functions before returning it to service.

The displayed slot information does not refresh automatically after an update. Always repeat the device check before starting another update, because the required target slot may have changed.

## Cancel an update

1. Select **Cancel OTA**.
2. Wait for the cancellation result.
3. Select **Check Active Slot** to check the device before attempting another update.

Cancellation does not confirm that the device is still using its previous software. If the device cannot be checked or does not operate normally, contact your support team before trying again.

The app prevents normal window closure while a check or update is running. Wait for the check to finish, or use **Cancel OTA** for an update and wait for its result.

## Resolve common problems

| What you see | What to do |
| --- | --- |
| The app does not open | Ask your support team to check the app installation. Include the error message, if one appears. |
| **Slot check failed** or a connection error | Check that the device is powered on and that the computer and device have network access. Try **Check Active Slot** again. If it fails again, contact support. |
| **Upload Firmware** is unavailable | Complete **Check Active Slot**, choose the supplied `.bin` file, and check for **Ready to upload firmware**. Wait for any current operation to finish. |
| The app rejects the selected file | Confirm that you selected the supplied `.bin` file for the displayed target slot. If it is still rejected, send support the complete error message and target slot. Do not modify the file. |
| **Wrong BIN linker** | The app could not accept the file. Ask support for a suitable file and include the full message, **OTA target slot**, and **Required linker** value. |
| Progress reaches 100% but the update is still running | Keep the app open and wait for **OTA successful** or **OTA failed**. The device may still be restarting or completing its checks. |
| **OTA failed** | Record the full message. Once the operation finishes, check the device again. If the check fails, the device behaves unexpectedly, or you are unsure which file to use, contact support before retrying. |
| **Disconnected** after a successful check or update | This is expected. Use the result message to determine whether the operation succeeded. |
| The slot information still shows the previous location | Select **Check Active Slot** after the update finishes to refresh it. |

If power or network access is interrupted, restore it and wait for the current operation to finish. Check the device again before retrying; do not assume the update succeeded or that the previous software is still active.
