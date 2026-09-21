"""Self-contained USB serial DFU for the XIAO nRF52840 bootloader."""

from __future__ import annotations

import asyncio
import json
import struct
import time
import zipfile
import os
import re
import binascii
from dataclasses import dataclass
from collections.abc import Callable

import serial  # type: ignore[import-untyped]
from serial.tools import list_ports  # type: ignore[import-untyped]


class DfuError(RuntimeError):
    """The bootloader could not be entered or rejected the transfer."""


ProgressCallback = Callable[[int, str], None]
_END, _ESC, _ESC_END, _ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
_DFU_INIT, _DFU_START, _DFU_DATA, _DFU_STOP = 1, 3, 4, 5
_APPLICATION_MODE, _HCI_DFU_PACKET, _MAX_PAYLOAD = 4, 14, 512


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = ((crc >> 8) | ((crc << 8) & 0xFFFF)) & 0xFFFF
        crc ^= byte
        crc ^= (crc & 0xFF) >> 4
        crc ^= crc << 12
        crc ^= ((crc & 0xFF) << 5) & 0xFFFF
        crc &= 0xFFFF
    return crc & 0xFFFF


def _slip_encode(data: bytes) -> bytes:
    out = bytearray((_END,))
    for byte in data:
        if byte == _END:
            out.extend((_ESC, _ESC_END))
        elif byte == _ESC:
            out.extend((_ESC, _ESC_ESC))
        else:
            out.append(byte)
    out.append(_END)
    return bytes(out)


def _slip_decode(data: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        index += 1
        if byte != _ESC:
            out.append(byte)
            continue
        if index >= len(data):
            raise DfuError("Malformed escaped bootloader response")
        escaped = data[index]
        index += 1
        if escaped == _ESC_END:
            out.append(_END)
        elif escaped == _ESC_ESC:
            out.append(_ESC)
        else:
            raise DfuError("Malformed escaped bootloader response")
    return bytes(out)


class _SerialDfu:
    """The small legacy Nordic/Adafruit serial-DFU HCI protocol."""

    def __init__(self, port: str) -> None:
        self._name = port
        self._port: serial.Serial | None = None
        self._sequence = 0

    def open(self) -> None:
        last_error: Exception | None = None
        for _ in range(20):
            try:
                self._port = serial.Serial(self._name, 115200, timeout=1, write_timeout=3)
                self._port.dtr = False
                time.sleep(0.05)
                self._port.dtr = True
                time.sleep(0.10)
                return
            except serial.SerialException as err:
                last_error = err
                time.sleep(0.25)
        raise DfuError(f"Unable to open DFU serial port {self._name}: {last_error}")

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None

    def _read_ack(self) -> None:
        if self._port is None:
            raise DfuError("DFU serial port is closed")
        deadline = time.monotonic() + 2
        collecting = False
        frame = bytearray()
        while time.monotonic() < deadline:
            received = self._port.read(1)
            if not received:
                continue
            byte = received[0]
            if byte == _END:
                if collecting and frame:
                    ack = _slip_decode(bytes(frame))
                    if len(ack) != 4 or sum(ack) & 255 or ack[1] or ack[2]:
                        raise DfuError("Invalid bootloader acknowledgement header")
                    if (ack[0] >> 3) & 7 != (self._sequence + 1) % 8:
                        raise DfuError("Unexpected bootloader acknowledgement sequence")
                    return
                collecting = True
                frame.clear()
            elif collecting:
                frame.append(byte)
                if len(frame) > 32:
                    raise DfuError("Oversized bootloader acknowledgement")
        raise DfuError("Timed out waiting for bootloader acknowledgement")

    def send(self, payload: bytes) -> None:
        if self._port is None:
            raise DfuError("DFU serial port is closed")
        self._sequence = (self._sequence + 1) % 8
        length = len(payload)
        h0 = self._sequence | (((self._sequence + 1) % 8) << 3) | 0xC0
        h1 = _HCI_DFU_PACKET | ((length & 0x0F) << 4)
        h2 = (length >> 4) & 0xFF
        h3 = (-((h0 + h1 + h2) & 0xFF)) & 0xFF
        body = bytes((h0, h1, h2, h3)) + payload
        self._port.write(_slip_encode(body + struct.pack("<H", _crc16(body))))
        self._port.flush()
        self._read_ack()

    def send_application(self, firmware: bytes, init_packet: bytes, progress: ProgressCallback) -> None:
        self.send(struct.pack("<IIIII", _DFU_START, _APPLICATION_MODE, 0, 0, len(firmware)))
        # START acknowledges before the bootloader erases the destination bank.
        # An nRF52840 flash page can take roughly 90 ms to erase.
        erase_wait = max(0.5, ((len(firmware) + 4095) // 4096 + 1) * 0.09)
        time.sleep(erase_wait)
        self.send(struct.pack("<I", _DFU_INIT) + init_packet + b"\0\0")
        for offset in range(0, len(firmware), _MAX_PAYLOAD):
            self.send(struct.pack("<I", _DFU_DATA) + firmware[offset : offset + _MAX_PAYLOAD])
            if offset % 4096 == 0:
                time.sleep(0.11)
            written = min(offset + _MAX_PAYLOAD, len(firmware))
            progress(8 + int(87 * written / len(firmware)), "Writing firmware")
        time.sleep(0.11)
        self.send(struct.pack("<I", _DFU_STOP))


async def async_touch_1200(port: str) -> None:
    """Ask the running application to enter its resident USB bootloader."""
    def _touch() -> None:
        try:
            with serial.Serial(port=port, baudrate=1200, timeout=1) as handle:
                handle.dtr = False
                handle.rts = False
        except serial.SerialException as err:
            raise DfuError(f"Unable to enter bootloader on {port}: {err}") from err
    await asyncio.to_thread(_touch)


@dataclass(frozen=True)
class UsbIdentity:
    serial_number: str
    location: str
    port: str
    pid: int


def _location(port) -> str:
    # Linux appends the USB interface (e.g. ':1.0'); bootloader/app
    # interface counts differ but the physical USB topology must match.
    return (port.location or "").split(":", 1)[0]


def identify_device(port: str) -> UsbIdentity:
    original = next((p for p in list_ports.comports() if os.path.realpath(p.device) == os.path.realpath(port)), None)
    if original is None or original.vid != 0x2886 or not original.serial_number:
        raise DfuError("Cannot establish the selected XIAO's USB identity")
    return UsbIdentity(original.serial_number, _location(original), original.device, original.pid)


async def async_application_port(identity: UsbIdentity, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [p for p in await asyncio.to_thread(list_ports.comports)
                      if p.vid == 0x2886 and p.pid == identity.pid
                      and p.serial_number == identity.serial_number
                      and (not identity.location or _location(p) == identity.location)]
        if len(candidates) == 2:
            # Firmware advertises main JSON CDC first and debug CDC second.
            return sorted((p.device for p in candidates), key=lambda name: [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", name)])[0]
        await asyncio.sleep(.5)
    raise DfuError("Updated controller's application USB interfaces did not appear")


async def async_wait_for_bootloader_port(original_port: UsbIdentity, timeout: float = 15.0) -> str:
    """Locate the re-enumerated XIAO port after the 1200-baud touch."""
    await asyncio.sleep(1.5)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [p for p in await asyncio.to_thread(list_ports.comports)
                      if p.vid == 0x2886 and p.serial_number == original_port.serial_number
                      and (not original_port.location or _location(p) == original_port.location)]
        # The bootloader has one CDC interface; the running app has two.
        if len(candidates) == 1:
            return candidates[0].device
        await asyncio.sleep(0.25)
    raise DfuError("Timed out waiting for the XIAO DFU serial port")


def _read_package(package: str) -> tuple[bytes, bytes]:
    try:
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            root = manifest["manifest"]
            if any(root.get(k) for k in ("bootloader", "softdevice", "softdevice_bootloader")):
                raise DfuError("Only application firmware packages are supported")
            if root.get("dfu_version") != 0.5:
                raise DfuError("Unsupported DFU package version")
            application = manifest["manifest"]["application"]
            if archive.getinfo(application["bin_file"]).file_size > 0x61000 or archive.getinfo(application["dat_file"]).file_size > 512:
                raise DfuError("Firmware exceeds this controller's application bank")
            firmware = archive.read(application["bin_file"])
            init_packet = archive.read(application["dat_file"])
    except (KeyError, TypeError, AttributeError, OSError, ValueError, zipfile.BadZipFile) as err:
        raise DfuError(f"Invalid application DFU package: {err}") from err
    if not firmware or not init_packet:
        raise DfuError("DFU package contains an empty application or init packet")
    if len(firmware) < 8 or len(init_packet) < 12:
        raise DfuError("Truncated firmware package")
    msp, reset = struct.unpack_from("<II", firmware)
    if not 0x20000000 < msp <= 0x20040000 or not reset & 1 or not 0x27000 <= (reset & ~1) < 0x27000 + len(firmware):
        raise DfuError("Image is not linked for the controller's application address")
    count = struct.unpack_from("<H", init_packet, 8)[0]
    if len(init_packet) != 12 + 2 * count:
        raise DfuError("Unsupported DFU init packet")
    if struct.unpack_from("<H", init_packet, len(init_packet)-2)[0] != binascii.crc_hqx(firmware, 65535):
        raise DfuError("Firmware CRC does not match init packet")
    return firmware, init_packet


async def async_flash(package: str, port: str, progress: ProgressCallback) -> None:
    """Flash a PlatformIO DFU ZIP without an external tool or package."""
    firmware, init_packet = await asyncio.to_thread(_read_package, package)
    loop = asyncio.get_running_loop()
    def report(value: int, message: str) -> None:
        loop.call_soon_threadsafe(progress, value, message)
    def _flash() -> None:
        transport = _SerialDfu(port)
        try:
            report(8, "Opening XIAO DFU serial port")
            transport.open()
            transport.send_application(firmware, init_packet, report)
        finally:
            transport.close()
    worker = asyncio.create_task(asyncio.to_thread(_flash))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A Python cancellation cannot stop the serial-writing thread. Never
        # release the port for polling while that thread is still flashing.
        await worker
        raise
    progress(96, "Firmware transfer complete; waiting for application restart")
