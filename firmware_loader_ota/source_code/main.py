import base64
import json
import queue
import re
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PyQt5.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize,
    Qt, QThread, QTimer, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QApplication, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QStackedLayout, QVBoxLayout, QWidget)

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "info_broker.txt"
AES_KEY_FILE = APP_DIR / "aes_key.txt"

OTA_PACKET_START = 0
OTA_PACKET_DATA = 1
OTA_PACKET_FINISH = 2
OTA_PACKET_ABORT = 3
OTA_PACKET_CONFIRM = 4
GCM_NONCE_PREFIX = bytes.fromhex("75 69 74 74")
GCM_TAG_SIZE = 16

APP_A_BASE = 0x08001800
APP_A_END = 0x08020800
APP_B_BASE = 0x08020800
APP_B_END = 0x0803F800
SRAM_BASE = 0x20000000
SRAM_END = 0x2000C000

APP_SLOT_SIZE = 124 * 1024
# Keep the 9-byte header within 256 bytes and Flash offsets half-word aligned.
CHUNK_SIZE = 246
ACK_TIMEOUT_SECONDS = 5.0
SLOT_TIMEOUT_SECONDS = 15.0
BOOT_TIMEOUT_SECONDS = 60.0
CONFIRM_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 5


def load_broker_info():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_FILE.name}")

    config = {}
    for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"').strip("'")

    required = ("host_url", "port", "name", "pw", "publish_topic", "subscribe_topic")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError("Missing broker fields: " + ", ".join(missing))

    if not config["host_url"] or not config["port"]:
        raise ValueError("host_url and port must not be empty")
    if not config["publish_topic"] or not config["subscribe_topic"]:
        raise ValueError("publish_topic and subscribe_topic must not be empty")

    config["port"] = int(config["port"])
    if not 1 <= config["port"] <= 65535:
        raise ValueError("port must be between 1 and 65535")

    if not config.get("command_topic"):
        ota_down_suffix = "/ota/down"
        publish_topic = config["publish_topic"]
        if publish_topic.endswith(ota_down_suffix):
            config["command_topic"] = publish_topic[:-len(ota_down_suffix)] + "/command"
        else:
            raise ValueError("command_topic is missing and cannot be inferred from publish_topic")

    return config


def crc16_ccitt(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x2102) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def make_command_payload(command_name, command_id=1, information=""):
    crc_input = f"ID{command_id}CMD{command_name}information"
    crc_input += "".join(ch for ch in information if ch != " ")
    crc = crc16_ccitt(crc_input.encode("utf-8"))
    return json.dumps({
        "ID": command_id,
        "CMD": command_name,
        "information": information,
        "crc": crc
    }, separators=(",", ":")).encode("utf-8")


class SecurityCodec:
    def __init__(self, key_file):
        self.aes_key = self._load_aes_key(key_file)
        self.aesgcm = AESGCM(self.aes_key)
        self.tx_counter = 1

    @staticmethod
    def _load_aes_key(key_file):
        text = Path(key_file).read_text(encoding="ascii")
        for line in text.splitlines():
            stripped = line.strip()
            if re.fullmatch(r"(?:[0-9A-Fa-f]{2}\s+){15}[0-9A-Fa-f]{2}", stripped):
                key = bytes.fromhex(stripped)
                if len(key) == 16:
                    return key
        raise ValueError(f"Missing 16-byte hex AES key in {Path(key_file).name}")

    def encrypt(self, plaintext):
        counter = self.tx_counter
        self.tx_counter = 1 if self.tx_counter >= 0xFFFFFFFFFFFFFFFF else self.tx_counter + 1
        nonce = GCM_NONCE_PREFIX + counter.to_bytes(8, "big")
        encrypted = self.aesgcm.encrypt(nonce, plaintext, None)
        ciphertext = encrypted[:-GCM_TAG_SIZE]
        tag = encrypted[-GCM_TAG_SIZE:]
        return json.dumps({
            "h": base64.b64encode(nonce).decode("ascii"),
            "t": base64.b64encode(tag).decode("ascii"),
            "d": base64.b64encode(ciphertext).decode("ascii"),
        }, separators=(",", ":")).encode("utf-8")


def make_start_packet(firmware_size, firmware_crc32):
    return struct.pack("<BII", OTA_PACKET_START, firmware_size, firmware_crc32)


def make_data_packet(offset, data):
    return struct.pack("<BII", OTA_PACKET_DATA, offset, len(data)) + data


def slot_from_base(base_text):
    try:
        base = int(str(base_text), 16)
    except (TypeError, ValueError):
        return None
    if base == APP_A_BASE:
        return "A"
    if base == APP_B_BASE:
        return "B"
    return None


def get_slot_range(slot):
    if slot == "A":
        return APP_A_BASE, APP_A_END
    if slot == "B":
        return APP_B_BASE, APP_B_END
    raise ValueError("Unknown slot")


def get_target_slot(active_slot):
    if active_slot == "A":
        return "B"
    if active_slot == "B":
        return "A"
    return None


def format_address(value):
    return f"0x{value:08X}"


def detect_slot_from_reset_address(reset_address):
    if APP_A_BASE <= reset_address < APP_A_END:
        return "A"
    if APP_B_BASE <= reset_address < APP_B_END:
        return "B"
    return None


def read_bin_info(path, target_slot):
    firmware_path = Path(path)
    if firmware_path.suffix.lower() != ".bin":
        raise ValueError("Invalid firmware file format. Please select a .bin file.")

    firmware = firmware_path.read_bytes()
    firmware_size = len(firmware)

    if firmware_size == 0:
        raise ValueError("BIN file is empty")
    if firmware_size < 8:
        raise ValueError("BIN file is too small to contain a vector table")
    if firmware_size > APP_SLOT_SIZE:
        raise ValueError(f"BIN is too large: {firmware_size} bytes; slot limit is {APP_SLOT_SIZE} bytes")

    stack_pointer, reset_handler = struct.unpack_from("<II", firmware, 0)
    reset_address = reset_handler & ~1
    target_base, target_end = get_slot_range(target_slot)

    if not (SRAM_BASE <= stack_pointer <= SRAM_END) or (stack_pointer & 3):
        raise ValueError(f"Invalid stack pointer: {format_address(stack_pointer)}")
    if (reset_handler & 1) == 0:
        raise ValueError(f"Reset handler is not a Thumb address: {format_address(reset_handler)}")
    if not (target_base <= reset_address < target_end):
        detected_slot = detect_slot_from_reset_address(reset_address)
        raise ValueError(
            "Wrong BIN linker\n\n"
            f"OTA target is slot {target_slot}.\n"
            f"Selected BIN appears to be linked for slot {detected_slot or 'unknown'}.\n"
            f"Reset handler: {format_address(reset_handler)}\n\n"
            f"Please rebuild the firmware with linker base:\n{format_address(target_base)}"
        )

    return {
        "path": firmware_path,
        "data": firmware,
        "size": firmware_size,
        "crc32": zlib.crc32(firmware) & 0xFFFFFFFF,
        "stack_pointer": stack_pointer,
        "reset_handler": reset_handler,
        "target_base": target_base,
    }


class MqttSession:
    def __init__(self, config, client_id):
        self.config = config
        self.connected_event = threading.Event()
        self.subscribed_event = threading.Event()
        self.responses = queue.Queue()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if config["name"]:
            self.client.username_pw_set(config["name"], config["pw"])
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected_event.set()
            client.subscribe(self.config["subscribe_topic"], qos=1)
        else:
            self.responses.put({"type": "CONNECT_ERROR", "value": int(reason_code)})

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        self.subscribed_event.set()

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if isinstance(payload, dict):
                self.responses.put(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    def connect(self):
        self.client.connect(self.config["host_url"], self.config["port"], keepalive=60)
        self.client.loop_start()
        self._wait_event(self.connected_event, 10.0, "Broker connection timed out")
        self._wait_event(self.subscribed_event, 10.0, "MQTT subscription timed out")

    def close(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def check_cancelled(self, cancel_event):
        if cancel_event.is_set():
            raise InterruptedError("OTA cancelled by user")

    def _wait_event(self, event, timeout, message):
        deadline = time.monotonic() + timeout
        while not event.wait(0.05):
            if time.monotonic() >= deadline:
                raise TimeoutError(message)

    def clear_responses(self):
        while True:
            try:
                self.responses.get_nowait()
            except queue.Empty:
                return

    def publish(self, topic, payload):
        info = self.client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=ACK_TIMEOUT_SECONDS)
        if not info.is_published():
            raise TimeoutError("MQTT publish timed out")

    def wait_ack(self, cancel_event, allow_slot=False):
        deadline = time.monotonic() + ACK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self.check_cancelled(cancel_event)
            try:
                response = self.responses.get(timeout=0.05)
            except queue.Empty:
                continue

            if allow_slot and response.get("type") == "OTA_SLOT":
                self.responses.put(response)
                return True, "OTA_SLOT"

            status = response.get("status")
            if status == "ACK":
                return True, "ACK"
            if status == "NACK":
                return False, "NACK"

            kind, value = response.get("type"), response.get("value")
            if kind == "ACK" and value == 0:
                return True, "ACK"
            if kind == "NACK":
                return False, f"NACK_{value}"

        return False, "ACK_TIMEOUT"

    def wait_slot(self, timeout, cancel_event):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_cancelled(cancel_event)
            try:
                response = self.responses.get(timeout=0.1)
            except queue.Empty:
                continue

            if response.get("type") != "OTA_SLOT":
                continue

            active_slot = str(response.get("active_slot", "")).upper()
            active_base = str(response.get("active_base", ""))
            if active_slot not in ("A", "B"):
                active_slot = slot_from_base(active_base)
            if active_slot in ("A", "B"):
                return active_slot, active_base

        raise TimeoutError("OTA_SLOT was not received")


class SlotCheckWorker(QThread):
    connection_changed = pyqtSignal(bool, str)
    status_changed = pyqtSignal(str)
    slot_detected = pyqtSignal(str, str)
    check_finished = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.cancel_event = threading.Event()
        self.session = None

    def run(self):
        success = False
        message = "Slot check failed"
        try:
            config = load_broker_info()
            self.session = MqttSession(config, f"stm32-ota-check-{int(time.time())}")
            self.status_changed.emit("Connecting to MQTT broker...")
            self.session.connect()
            self.connection_changed.emit(True, "Connected")
            self.session.clear_responses()
            self.status_changed.emit("Sending OTA_PING command...")
            encrypted_command = SecurityCodec(AES_KEY_FILE).encrypt(make_command_payload("OTA_PING"))
            self.session.publish(config["command_topic"], encrypted_command)
            active_slot, active_base = self.session.wait_slot(SLOT_TIMEOUT_SECONDS, self.cancel_event)
            self.slot_detected.emit(active_slot, active_base)
            success = True
            message = "Active slot detected"
        except Exception as exc:
            message = str(exc)
        finally:
            if self.session is not None:
                self.session.close()
            self.connection_changed.emit(False, "Disconnected")
            self.check_finished.emit(success, message)


class OtaWorker(QThread):
    connection_changed = pyqtSignal(bool, str)
    progress_changed = pyqtSignal(int)
    status_changed = pyqtSignal(str)
    ota_finished = pyqtSignal(bool, str)

    def __init__(self, bin_path, target_slot):
        super().__init__()
        self.bin_path = Path(bin_path)
        self.target_slot = target_slot
        self.cancel_event = threading.Event()
        self.session = None

    def cancel(self):
        self.cancel_event.set()

    def _send_with_ack(self, packet, description, allow_slot=False, retry_timeout=None):
        last_error = "unknown error"
        deadline = None if retry_timeout is None else time.monotonic() + retry_timeout
        attempt = 0
        self.session.clear_responses()
        while (attempt <= MAX_RETRIES if deadline is None else time.monotonic() < deadline):
            self.session.check_cancelled(self.cancel_event)
            text = description if attempt == 0 else f"{description} - retry {attempt}"
            self.status_changed.emit(text)
            try:
                self.session.publish(self.session.config["publish_topic"], packet)
                acknowledged, result = self.session.wait_ack(self.cancel_event, allow_slot=allow_slot)
            except TimeoutError as exc:
                acknowledged, result = False, str(exc)
            if acknowledged:
                return
            last_error = result
            attempt += 1
            if deadline is not None:
                self.cancel_event.wait(1.0)
        raise RuntimeError(f"{description} failed after {attempt} attempts: {last_error}")

    def _send_abort(self):
        if self.session is not None:
            try:
                self.session.publish(self.session.config["publish_topic"], bytes([OTA_PACKET_ABORT]))
            except Exception:
                pass

    def run(self):
        success = False
        final_message = "OTA failed"
        try:
            config = load_broker_info()
            bin_info = read_bin_info(self.bin_path, self.target_slot)
            firmware = bin_info["data"]
            firmware_size = bin_info["size"]
            firmware_crc = bin_info["crc32"]

            self.session = MqttSession(config, f"stm32-ota-upload-{int(time.time())}")
            self.status_changed.emit("Connecting to MQTT broker...")
            self.session.connect()
            self.connection_changed.emit(True, "Connected")

            self._send_with_ack(make_start_packet(firmware_size, firmware_crc), "Sending START packet")
            acknowledged_bytes = 0
            for offset in range(0, firmware_size, CHUNK_SIZE):
                chunk = firmware[offset:offset + CHUNK_SIZE]
                self._send_with_ack(make_data_packet(offset, chunk), f"Sending data at offset {offset}")
                acknowledged_bytes += len(chunk)
                self.progress_changed.emit(int(acknowledged_bytes * 100 / firmware_size))

            self._send_with_ack(bytes([OTA_PACKET_FINISH]), "Sending FINISH packet", allow_slot=True)
            self.status_changed.emit("Waiting for device reboot and slot report...")
            active_slot, active_base = self.session.wait_slot(BOOT_TIMEOUT_SECONDS, self.cancel_event)
            if active_slot != self.target_slot:
                raise RuntimeError(
                    f"Device booted slot {active_slot}, expected slot {self.target_slot}. "
                    f"Active base: {active_base}"
                )

            self._send_with_ack(bytes([OTA_PACKET_CONFIRM]), "Waiting for device health confirmation",
                                retry_timeout=CONFIRM_TIMEOUT_SECONDS)
            success = True
            final_message = f"Firmware installed and confirmed on slot {active_slot}"
        except InterruptedError as exc:
            self._send_abort()
            final_message = str(exc)
        except Exception as exc:
            self._send_abort()
            final_message = str(exc)
        finally:
            if self.session is not None:
                self.session.close()
            self.connection_changed.emit(False, "Disconnected")
            self.ota_finished.emit(success, final_message)


class IconGlyph(QWidget):
    def __init__(self, kind="chip", color="#9CA3AF", size=20, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.color = QColor(color)
        self.icon_size = size
        self.setFixedSize(size, size)

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        w = self.width()
        h = self.height()
        r = min(w, h)
        x = (w - r) / 2
        y = (h - r) / 2

        if self.kind == "chip":
            body = QRectF(x + r * 0.26, y + r * 0.26, r * 0.48, r * 0.48)
            painter.drawRoundedRect(body, 2.5, 2.5)
            for i in (0.34, 0.5, 0.66):
                painter.drawLine(QPoint(int(x + r * i), int(y + r * 0.14)),
                                 QPoint(int(x + r * i), int(y + r * 0.26)))
                painter.drawLine(QPoint(int(x + r * i), int(y + r * 0.74)),
                                 QPoint(int(x + r * i), int(y + r * 0.86)))
                painter.drawLine(QPoint(int(x + r * 0.14), int(y + r * i)),
                                 QPoint(int(x + r * 0.26), int(y + r * i)))
                painter.drawLine(QPoint(int(x + r * 0.74), int(y + r * i)),
                                 QPoint(int(x + r * 0.86), int(y + r * i)))
            painter.drawLine(QPoint(int(x + r * 0.39), int(y + r * 0.5)),
                             QPoint(int(x + r * 0.61), int(y + r * 0.5)))
        elif self.kind == "drive":
            painter.drawRoundedRect(QRectF(x + r * 0.2, y + r * 0.25, r * 0.6, r * 0.5), 3, 3)
            painter.drawLine(QPoint(int(x + r * 0.31), int(y + r * 0.61)),
                             QPoint(int(x + r * 0.58), int(y + r * 0.61)))
            painter.drawEllipse(QRectF(x + r * 0.63, y + r * 0.57, r * 0.08, r * 0.08))
        elif self.kind == "upload":
            painter.drawLine(QPoint(int(x + r * 0.5), int(y + r * 0.24)),
                             QPoint(int(x + r * 0.5), int(y + r * 0.64)))
            painter.drawLine(QPoint(int(x + r * 0.34), int(y + r * 0.4)),
                             QPoint(int(x + r * 0.5), int(y + r * 0.24)))
            painter.drawLine(QPoint(int(x + r * 0.66), int(y + r * 0.4)),
                             QPoint(int(x + r * 0.5), int(y + r * 0.24)))
            painter.drawArc(QRectF(x + r * 0.22, y + r * 0.5, r * 0.56, r * 0.32), 200 * 16, 140 * 16)
        elif self.kind == "refresh":
            painter.drawArc(QRectF(x + r * 0.2, y + r * 0.2, r * 0.6, r * 0.6), 35 * 16, 280 * 16)
            painter.drawLine(QPoint(int(x + r * 0.74), int(y + r * 0.31)),
                             QPoint(int(x + r * 0.8), int(y + r * 0.17)))
            painter.drawLine(QPoint(int(x + r * 0.74), int(y + r * 0.31)),
                             QPoint(int(x + r * 0.59), int(y + r * 0.28)))
        elif self.kind == "check":
            painter.drawLine(QPoint(int(x + r * 0.25), int(y + r * 0.52)),
                             QPoint(int(x + r * 0.43), int(y + r * 0.7)))
            painter.drawLine(QPoint(int(x + r * 0.43), int(y + r * 0.7)),
                             QPoint(int(x + r * 0.78), int(y + r * 0.32)))
        elif self.kind == "warning":
            path = [
                QPoint(int(x + r * 0.5), int(y + r * 0.18)),
                QPoint(int(x + r * 0.84), int(y + r * 0.78)),
                QPoint(int(x + r * 0.16), int(y + r * 0.78)),
                QPoint(int(x + r * 0.5), int(y + r * 0.18)),
            ]
            painter.drawPolyline(*path)
            painter.drawLine(QPoint(int(x + r * 0.5), int(y + r * 0.38)),
                             QPoint(int(x + r * 0.5), int(y + r * 0.57)))
            painter.drawPoint(QPoint(int(x + r * 0.5), int(y + r * 0.67)))
        elif self.kind == "moon":
            painter.drawEllipse(QRectF(x + r * 0.22, y + r * 0.18, r * 0.56, r * 0.56))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#1E1E2E"))
            painter.drawEllipse(QRectF(x + r * 0.42, y + r * 0.08, r * 0.48, r * 0.56))
        elif self.kind == "sun":
            painter.drawEllipse(QRectF(x + r * 0.36, y + r * 0.36, r * 0.28, r * 0.28))
            for dx, dy in ((0.5, 0.14), (0.5, 0.86), (0.14, 0.5), (0.86, 0.5),
                           (0.24, 0.24), (0.76, 0.24), (0.24, 0.76), (0.76, 0.76)):
                painter.drawPoint(QPoint(int(x + r * dx), int(y + r * dy)))
        painter.end()


class PulseDot(QWidget):
    def __init__(self, color="#22C55E", parent=None):
        super().__init__(parent)
        self._pulse = 0.0
        self.color = QColor(color)
        self.setFixedSize(18, 18)
        self.animation = QPropertyAnimation(self, b"pulse", self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1400)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.InOutSine)
        self.animation.start()

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def get_pulse(self):
        return self._pulse

    def set_pulse(self, value):
        self._pulse = value
        self.update()

    pulse = pyqtProperty(float, fget=get_pulse, fset=set_pulse)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        ring = QColor(self.color)
        ring.setAlpha(int(88 * (1.0 - self._pulse)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(ring)
        radius = 5 + self._pulse * 6
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(self.color)
        painter.drawEllipse(center, 4, 4)
        painter.end()


class Spinner(QWidget):
    def __init__(self, color="#00D9C0", parent=None):
        super().__init__(parent)
        self.angle = 0
        self.color = QColor(color)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.setFixedSize(18, 18)
        self.hide()

    def start(self):
        self.show()
        self.timer.start(45)

    def stop(self):
        self.timer.stop()
        self.hide()

    def rotate(self):
        self.angle = (self.angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.color, 2.0, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(3, 3, 12, 12), self.angle * 16, 265 * 16)
        painter.end()


class HeaderLogo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 42)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#00D9C0"))
        painter.drawRoundedRect(QRectF(6, 6, 30, 30), 8, 8)
        painter.setBrush(QColor("#14161C"))
        painter.drawRoundedRect(QRectF(14, 14, 14, 14), 3, 3)
        painter.setPen(QPen(QColor("#14161C"), 2, Qt.SolidLine, Qt.RoundCap))
        for p in (12, 21, 30):
            painter.drawLine(QPoint(p, 2), QPoint(p, 8))
            painter.drawLine(QPoint(p, 34), QPoint(p, 40))
            painter.drawLine(QPoint(2, p), QPoint(8, p))
            painter.drawLine(QPoint(34, p), QPoint(40, p))
        painter.end()


class Card(QFrame):
    def __init__(self, title, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 18)
        self.layout.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        header.addWidget(title_label)
        if subtitle:
            sub_label = QLabel(subtitle)
            sub_label.setObjectName("cardSubtitle")
            header.addWidget(sub_label)
        self.layout.addLayout(header)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)


class StatusChip(QFrame):
    def __init__(self, icon, label, value="--", state="muted", parent=None):
        super().__init__(parent)
        self.setObjectName("statusChip")
        self.icon = IconGlyph(icon, "#9CA3AF", 19)
        self.label = QLabel(label)
        self.label.setObjectName("chipLabel")
        self.value = QLabel(value)
        self.value.setObjectName("chipValue")
        self.value.setMinimumWidth(72)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        text_col.addWidget(self.label)
        text_col.addWidget(self.value)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self.icon)
        layout.addLayout(text_col)
        layout.addStretch()
        self.set_state(state)

    def set_value(self, value, state="ok"):
        self.value.setText(value)
        self.set_state(state)

    def set_state(self, state):
        colors = {
            "ok": ("#22C55E", "rgba(34, 197, 94, 0.13)", "rgba(34, 197, 94, 0.35)"),
            "warn": ("#F59E0B", "rgba(245, 158, 11, 0.13)", "rgba(245, 158, 11, 0.35)"),
            "error": ("#EF4444", "rgba(239, 68, 68, 0.13)", "rgba(239, 68, 68, 0.35)"),
            "info": ("#3B82F6", "rgba(59, 130, 246, 0.13)", "rgba(59, 130, 246, 0.35)"),
            "muted": ("#9CA3AF", "rgba(156, 163, 175, 0.08)", "rgba(156, 163, 175, 0.16)"),
        }
        accent, bg, border = colors.get(state, colors["muted"])
        self.icon.set_color(accent)
        self.value.setStyleSheet(f"color: {accent};")
        self.setStyleSheet(
            "QFrame#statusChip {"
            f"background: {bg};"
            f"border: 1px solid {border};"
            "border-radius: 12px;"
            "}"
        )


class IconButton(QPushButton):
    def __init__(self, text, icon_kind, parent=None):
        super().__init__(text, parent)
        self.icon_kind = icon_kind
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(40)
        self.setProperty("pressing", False)
        self.spinner = None

    def mousePressEvent(self, event):
        self.setProperty("pressing", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setProperty("pressing", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().mouseReleaseEvent(event)


class DropZone(QFrame):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)

        self.icon = IconGlyph("upload", "#00D9C0", 46)
        self.title = QLabel("Drop STM32 .bin firmware here")
        self.title.setObjectName("dropTitle")
        self.detail = QLabel("or browse from disk")
        self.detail.setObjectName("dropDetail")
        self.path_label = QLabel("No file selected")
        self.path_label.setObjectName("dropPath")
        self.path_label.setWordWrap(True)
        self.path_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(8)
        layout.addWidget(self.icon, 0, Qt.AlignHCenter)
        layout.addWidget(self.title, 0, Qt.AlignHCenter)
        layout.addWidget(self.detail, 0, Qt.AlignHCenter)
        layout.addWidget(self.path_label)

    def set_file_name(self, text):
        self.path_label.setText(text)

    def mousePressEvent(self, event):
        del event
        self.window().choose_file()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragging", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        del event
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if urls:
            self.file_selected.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class Toast(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setVisible(False)
        self.icon = IconGlyph("check", "#22C55E", 19)
        self.label = QLabel("")
        self.label.setObjectName("toastText")
        self.label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, message, state="ok"):
        color = {"ok": "#22C55E", "error": "#EF4444", "warn": "#F59E0B"}.get(state, "#22C55E")
        self.icon.kind = "check" if state == "ok" else "warning"
        self.icon.set_color(color)
        self.label.setText(message)
        self.adjustSize()
        margin = 22
        self.move(self.parent().width() - self.width() - margin, self.parent().height() - self.height() - margin)
        self.show()
        self.raise_()
        self.timer.start(4200)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bin_path = None
        self.bin_info = None
        self.active_slot = None
        self.active_base = None
        self.target_slot = None
        self.worker = None
        self.slot_worker = None
        self.theme = "light"
        self.upload_percent = 0

        self.setWindowTitle("STM32 OTA Firmware Loader")
        self.setMinimumSize(820, 540)
        self.setAcceptDrops(True)

        self.logo = HeaderLogo()
        self.title_label = QLabel("STM32 OTA Firmware Loader")
        self.title_label.setObjectName("appTitle")
        self.subtitle_label = QLabel("MQTT firmware delivery for dual-slot STM32 targets")
        self.subtitle_label.setObjectName("appSubtitle")
        self.status_dot = PulseDot("#22C55E")
        self.connection_label = QLabel("Ready")
        self.connection_label.setObjectName("connectionLabel")
        self.theme_button = IconButton("Light", "sun")
        self.theme_button.setObjectName("ghostButton")

        self.active_chip = StatusChip("chip", "Active slot", "--", "muted")
        self.target_chip = StatusChip("drive", "OTA target slot", "--", "muted")
        self.linker_chip = StatusChip("chip", "Required linker", "--", "muted")
        self.device_chip = StatusChip("drive", "Device slot", "Awaiting scan", "warn")
        self.check_slot_button = IconButton("Check Active Slot", "refresh")
        self.check_slot_button.setObjectName("secondaryButton")
        self.check_spinner = Spinner("#00D9C0")

        self.drop_zone = DropZone()
        self.size_chip = StatusChip("drive", "File size", "--", "muted")
        self.target_check_chip = StatusChip("check", "Target check", "--", "muted")
        self.status_label = QLabel("Check active slot and select a valid BIN file")
        self.status_label.setObjectName("hintText")
        self.status_label.setWordWrap(True)
        self.hint_icon = IconGlyph("warning", "#F59E0B", 18)

        self.progress = QProgressBar()
        self.progress.setObjectName("uploadProgress")
        self.progress.setRange(0, 100)
        self.progress.setFormat("%p%")
        self.progress.setTextVisible(True)

        self.browse_button = IconButton("Browse BIN File", "upload")
        self.browse_button.setObjectName("secondaryButton")
        self.upload_button = IconButton("Upload Firmware", "upload")
        self.upload_button.setObjectName("primaryButton")
        self.upload_button.setEnabled(False)
        self.upload_spinner = Spinner("#14161C")
        self.cancel_button = IconButton("Cancel OTA", "warning")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setVisible(False)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_text.addWidget(self.title_label)
        header_text.addWidget(self.subtitle_label)

        header_left = QHBoxLayout()
        header_left.setSpacing(12)
        header_left.addWidget(self.logo)
        header_left.addLayout(header_text)

        connection_pill = QFrame()
        connection_pill.setObjectName("connectionPill")
        connection_layout = QHBoxLayout(connection_pill)
        connection_layout.setContentsMargins(12, 7, 12, 7)
        connection_layout.setSpacing(7)
        connection_layout.addWidget(self.status_dot)
        connection_layout.addWidget(self.connection_label)

        header = QHBoxLayout()
        header.setSpacing(14)
        header.addLayout(header_left)
        header.addStretch()
        header.addWidget(connection_pill)
        header.addWidget(self.theme_button)

        slot_grid_top = QHBoxLayout()
        slot_grid_top.setSpacing(12)
        slot_grid_top.addWidget(self.active_chip)
        slot_grid_top.addWidget(self.target_chip)

        slot_grid_bottom = QHBoxLayout()
        slot_grid_bottom.setSpacing(12)
        slot_grid_bottom.addWidget(self.linker_chip)
        slot_grid_bottom.addWidget(self.device_chip)

        check_button_row = QHBoxLayout()
        check_button_row.setSpacing(10)
        check_button_row.addWidget(self.check_slot_button)
        check_button_row.addWidget(self.check_spinner)
        check_button_row.addStretch()

        device_card = Card("Device Status", "Scan the target and verify the OTA destination")
        device_card.layout.addLayout(slot_grid_top)
        device_card.layout.addLayout(slot_grid_bottom)
        device_card.layout.addLayout(check_button_row)

        file_chip_row = QHBoxLayout()
        file_chip_row.setSpacing(12)
        file_chip_row.addWidget(self.size_chip)
        file_chip_row.addWidget(self.target_check_chip)

        browse_row = QHBoxLayout()
        browse_row.setSpacing(10)
        browse_row.addWidget(self.browse_button)
        browse_row.addStretch()

        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        hint_row.addWidget(self.hint_icon)
        hint_row.addWidget(self.status_label, 1)

        action_stack_container = QWidget()
        action_stack = QStackedLayout(action_stack_container)
        action_stack.setContentsMargins(0, 0, 0, 0)
        action_stack.addWidget(self.upload_button)
        action_stack.addWidget(self.cancel_button)
        self.action_stack = action_stack

        upload_button_row = QHBoxLayout()
        upload_button_row.setSpacing(10)
        upload_button_row.addWidget(action_stack_container)
        upload_button_row.addWidget(self.upload_spinner)

        firmware_card = Card("Firmware", "Select a linked image and stream it with ACK verification")
        firmware_card.layout.addWidget(self.drop_zone)
        firmware_card.layout.addLayout(file_chip_row)
        firmware_card.layout.addLayout(browse_row)
        firmware_card.layout.addWidget(self.progress)
        firmware_card.layout.addLayout(hint_row)
        firmware_card.layout.addLayout(upload_button_row)

        content = QHBoxLayout()
        content.setSpacing(18)
        content.addWidget(device_card, 1)
        content.addWidget(firmware_card, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(20)
        layout.addLayout(header)
        layout.addLayout(content)
        layout.addStretch()

        container = QWidget()
        container.setObjectName("root")
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.toast = Toast(container)

        self.check_slot_button.clicked.connect(self.check_active_slot)
        self.browse_button.clicked.connect(self.choose_file)
        self.drop_zone.file_selected.connect(self.handle_selected_path)
        self.upload_button.clicked.connect(self.start_ota)
        self.cancel_button.clicked.connect(self.cancel_ota)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.apply_theme()

    def check_active_slot(self):
        self.set_busy(True)
        self.check_spinner.start()
        self.set_status("Checking active slot...", "warn")
        self.slot_worker = SlotCheckWorker()
        self.slot_worker.connection_changed.connect(self.update_connection)
        self.slot_worker.status_changed.connect(lambda text: self.set_status(text, "warn"))
        self.slot_worker.slot_detected.connect(self.update_slot_info)
        self.slot_worker.check_finished.connect(self.finish_slot_check)
        self.slot_worker.start()

    def update_slot_info(self, active_slot, active_base):
        self.active_slot = active_slot
        self.active_base = active_base
        self.target_slot = get_target_slot(active_slot)
        target_base, _ = get_slot_range(self.target_slot)

        self.active_chip.set_value(f"Slot {active_slot}", "ok")
        self.target_chip.set_value(f"Slot {self.target_slot}", "info")
        self.linker_chip.set_value(format_address(target_base), "info")
        self.device_chip.set_value(f"Running\n{active_base}", "ok")
        self.set_status("Select a BIN file built for the OTA target slot", "warn")

        QMessageBox.information(
            self,
            "Device slot detected",
            f"Device is currently running slot {active_slot}.\n"
            f"Active base: {active_base}\n\n"
            f"OTA will write the new firmware to slot {self.target_slot}.\n"
            "Required BIN linker base:\n\n"
            f"{format_address(target_base)}\n\n"
            f"Please select a BIN file built for slot {self.target_slot}."
        )

        if self.bin_path:
            self.validate_selected_bin(show_success_popup=False)

    def finish_slot_check(self, success, message):
        self.set_busy(False)
        self.check_spinner.stop()
        self.set_status(message, "ok" if success else "error")
        if not success:
            self.device_chip.set_value("Scan failed", "error")
            self.toast.show_message(message, "error")
            QMessageBox.critical(self, "Slot check failed", message)
        self.update_upload_state()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select STM32 BIN firmware", "", "BIN files (*.bin)")
        if not path:
            return
        self.handle_selected_path(path)

    def handle_selected_path(self, path):
        selected_path = Path(path)
        if selected_path.suffix.lower() != ".bin":
            self.bin_path = None
            self.bin_info = None
            self.drop_zone.set_file_name("No file selected")
            self.size_chip.set_value("--", "muted")
            self.target_check_chip.set_value("Invalid format", "error")
            self.set_status("Please select a valid BIN file", "error")
            self.update_upload_state()
            self.toast.show_message("Only .bin firmware files are accepted.", "error")
            QMessageBox.critical(
                self,
                "Invalid file format",
                "Only STM32 firmware files with the .bin extension are accepted."
            )
            return

        self.bin_path = path
        self.drop_zone.set_file_name(selected_path.name)
        self.progress.setProperty("complete", False)
        self.progress.style().unpolish(self.progress)
        self.progress.style().polish(self.progress)
        self.progress.setValue(0)
        self.validate_selected_bin(show_success_popup=True)

    def validate_selected_bin(self, show_success_popup):
        self.bin_info = None
        self.size_chip.set_value("--", "muted")

        if not self.target_slot:
            self.target_check_chip.set_value("Scan device first", "warn")
            self.set_status("Check active slot before upload", "warn")
            self.update_upload_state()
            return

        try:
            self.bin_info = read_bin_info(self.bin_path, self.target_slot)
        except Exception as exc:
            self.target_check_chip.set_value(f"Wrong linker for slot {self.target_slot}", "error")
            self.set_status("Selected BIN does not match OTA target slot", "error")
            self.update_upload_state()
            self.toast.show_message("BIN linker does not match the OTA target.", "error")
            QMessageBox.critical(self, "Wrong BIN linker", str(exc))
            return

        size_kb = self.bin_info["size"] / 1024.0
        self.size_chip.set_value(f"{size_kb:.1f} KB", "ok")
        self.target_check_chip.set_value(f"Valid for slot {self.target_slot}", "ok")
        self.set_status("Ready to upload firmware", "ok")
        self.update_upload_state()

        if show_success_popup:
            self.toast.show_message(f"BIN verified for slot {self.target_slot}.", "ok")
            QMessageBox.information(
                self,
                "BIN linker verified",
                f"Selected BIN appears to be linked for slot {self.target_slot}.\n"
                f"Reset handler: {format_address(self.bin_info['reset_handler'])}\n\n"
                f"OTA target is slot {self.target_slot}.\n"
                "This firmware is ready to upload."
            )

    def start_ota(self):
        if not self.bin_path or not self.target_slot or not self.bin_info:
            return
        self.set_running(True)
        self.upload_percent = 0
        self.progress.setProperty("complete", False)
        self.progress.style().unpolish(self.progress)
        self.progress.style().polish(self.progress)
        self.progress.setValue(0)
        self.worker = OtaWorker(self.bin_path, self.target_slot)
        self.worker.connection_changed.connect(self.update_connection)
        self.worker.progress_changed.connect(self.update_upload_progress)
        self.worker.status_changed.connect(lambda text: self.set_status(text, "warn"))
        self.worker.ota_finished.connect(self.finish_ota)
        self.worker.start()

    def cancel_ota(self):
        if self.worker:
            self.worker.cancel()
            self.set_status("Cancelling OTA and sending ABORT...", "warn")

    def set_busy(self, busy):
        self.check_slot_button.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.drop_zone.setEnabled(not busy)
        self.upload_button.setEnabled(False if busy else self.can_upload())

    def set_running(self, running):
        self.check_slot_button.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.drop_zone.setEnabled(not running)
        self.action_stack.setCurrentWidget(self.cancel_button if running else self.upload_button)
        if running:
            self.upload_spinner.start()
            self.cancel_button.setVisible(True)
            self.cancel_button.setText("Cancel OTA")
        else:
            self.upload_spinner.stop()

    def can_upload(self):
        return self.bin_path is not None and self.target_slot is not None and self.bin_info is not None

    def update_upload_state(self):
        self.upload_button.setEnabled(self.can_upload())

    def update_connection(self, connected, message):
        self.status_dot.set_color("#22C55E" if connected else "#9CA3AF")
        self.connection_label.setText(message if message else ("Connected" if connected else "Ready"))

    def update_upload_progress(self, value):
        self.upload_percent = value
        self.progress.setValue(value)
        self.upload_button.setText(f"Uploading... {value}%")
        if value >= 100:
            self.progress.setProperty("complete", True)
            self.progress.style().unpolish(self.progress)
            self.progress.style().polish(self.progress)

    def set_status(self, message, state="warn"):
        self.status_label.setText(message)
        color = {"ok": "#22C55E", "warn": "#F59E0B", "error": "#EF4444"}.get(state, "#F59E0B")
        self.hint_icon.kind = "check" if state == "ok" else "warning"
        self.hint_icon.set_color(color)

    def finish_ota(self, success, message):
        self.set_running(False)
        self.upload_button.setText("Upload Firmware")
        self.update_upload_state()
        self.set_status(message, "ok" if success else "error")
        if success:
            self.progress.setValue(100)
            self.progress.setProperty("complete", True)
            self.progress.style().unpolish(self.progress)
            self.progress.style().polish(self.progress)
        self.toast.show_message(message, "ok" if success else "error")
        (QMessageBox.information if success else QMessageBox.critical)(
            self,
            "OTA successful" if success else "OTA failed",
            message
        )

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.apply_theme()

    def apply_theme(self):
        is_dark = self.theme == "dark"
        root_bg = "#14161C" if is_dark else "#F3F6FA"
        card_bg = "#1E1E2E" if is_dark else "#FFFFFF"
        panel_bg = "#202434" if is_dark else "#EEF3F8"
        chip_bg = "#252A3A" if is_dark else "#F6F8FB"
        border = "#343B4E" if is_dark else "#D8E0EA"
        text = "#E5E7EB" if is_dark else "#18202F"
        sub = "#9CA3AF" if is_dark else "#5D6878"
        input_bg = "#151923" if is_dark else "#FFFFFF"
        disabled_bg = "#2A2F3D" if is_dark else "#E2E8F0"
        disabled_text = "#707887" if is_dark else "#8A95A5"
        self.logo.update()
        self.theme_button.setText("Light" if is_dark else "Dark")
        self.theme_button.icon_kind = "sun" if is_dark else "moon"
        self.setStyleSheet(f"""
            QWidget#root {{
                background: {root_bg};
                color: {text};
                font-family: "Segoe UI Variable", "Inter", "Roboto", "Segoe UI", sans-serif;
                font-size: 13px;
            }}
            QLabel {{
                color: {text};
                letter-spacing: 0px;
            }}
            QLabel#appTitle {{
                font-size: 20px;
                font-weight: 700;
            }}
            QLabel#appSubtitle,
            QLabel#cardSubtitle,
            QLabel#chipLabel,
            QLabel#dropDetail {{
                color: {sub};
            }}
            QLabel#appSubtitle {{
                font-size: 12px;
            }}
            QFrame#card {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QLabel#cardTitle {{
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#cardSubtitle {{
                font-size: 12px;
            }}
            QLabel#chipLabel {{
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
            }}
            QLabel#chipValue {{
                font-size: 13px;
                font-weight: 700;
            }}
            QFrame#connectionPill {{
                background: {panel_bg};
                border: 1px solid {border};
                border-radius: 16px;
            }}
            QLabel#connectionLabel {{
                color: {text};
                font-weight: 700;
            }}
            QFrame#dropZone {{
                background: {input_bg};
                border: 1px dashed {border};
                border-radius: 14px;
                min-height: 150px;
            }}
            QFrame#dropZone[dragging="true"] {{
                border-color: #00D9C0;
                background: rgba(0, 217, 192, 0.08);
            }}
            QLabel#dropTitle {{
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#dropPath {{
                color: #00D9C0;
                font-weight: 700;
            }}
            QLabel#hintText {{
                color: {sub};
                font-weight: 600;
            }}
            QPushButton {{
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 9px 15px;
                font-weight: 700;
                min-height: 22px;
            }}
            QPushButton:hover {{
                border-color: rgba(0, 217, 192, 0.48);
            }}
            QPushButton[pressing="true"] {{
                padding-top: 10px;
                padding-bottom: 8px;
            }}
            QPushButton#primaryButton {{
                color: #071014;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00D9C0, stop:1 #3B82F6);
            }}
            QPushButton#primaryButton:disabled {{
                color: {disabled_text};
                background: {disabled_bg};
                border-color: {border};
            }}
            QPushButton#secondaryButton,
            QPushButton#ghostButton {{
                color: {text};
                background: {chip_bg};
                border-color: {border};
            }}
            QPushButton#secondaryButton:hover,
            QPushButton#ghostButton:hover {{
                background: rgba(0, 217, 192, 0.13);
            }}
            QPushButton#dangerButton {{
                color: #FEE2E2;
                background: rgba(239, 68, 68, 0.18);
                border-color: rgba(239, 68, 68, 0.42);
            }}
            QProgressBar#uploadProgress {{
                color: {text};
                background: {input_bg};
                border: 1px solid {border};
                border-radius: 9px;
                height: 18px;
                text-align: center;
                font-weight: 700;
            }}
            QProgressBar#uploadProgress::chunk {{
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00D9C0, stop:0.5 #3B82F6, stop:1 #00D9C0);
            }}
            QProgressBar#uploadProgress[complete="true"]::chunk {{
                background: #22C55E;
            }}
            QFrame#toast {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLabel#toastText {{
                color: {text};
                font-weight: 700;
            }}
            QMessageBox {{
                background: #FFFFFF;
            }}
            QMessageBox QLabel {{
                color: #18202F;
                font-size: 13px;
            }}
            QMessageBox QPushButton {{
                color: #071014;
                background: #F3F6FA;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 7px 18px;
                min-width: 64px;
                font-weight: 700;
            }}
            QMessageBox QPushButton:hover {{
                background: #E2E8F0;
                border-color: #94A3B8;
            }}
        """)

    def closeEvent(self, event):
        active_worker = self.worker and self.worker.isRunning()
        active_slot_worker = self.slot_worker and self.slot_worker.isRunning()
        if active_worker or active_slot_worker:
            QMessageBox.warning(self, "Operation in progress", "Wait for the current operation to finish")
            event.ignore()
        else:
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast") and self.toast.isVisible():
            margin = 22
            self.toast.move(
                self.centralWidget().width() - self.toast.width() - margin,
                self.centralWidget().height() - self.toast.height() - margin
            )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
