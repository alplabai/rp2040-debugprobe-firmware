# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alp Lab AB
"""CMSIS-DAP v2 bulk transport (pyusb): one request, one response."""
import sys

VID, PID = 0x2E8A, 0x000C
PACKET = 64
UDEV_HINT = (' On Linux add a udev rule, e.g. SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", '
             'ATTRS{idProduct}=="000c", MODE="0660", TAG+="uaccess", then replug the probe.')


class TransportError(Exception):
    pass


class Probe:
    def __init__(self, serial=None, timeout_ms=1000):
        try:
            import usb.core
            import usb.util
        except ImportError:
            raise TransportError("pyusb is not installed (pip install pyusb)")
        self.timeout = timeout_ms
        try:
            devs = list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))
        except usb.core.NoBackendError:
            raise TransportError("no libusb backend found; install libusb-1.0")
        if serial is not None:
            devs = [d for d in devs if _serial(d) == serial]
        if not devs:
            raise TransportError(f"probe {VID:04x}:{PID:04x}{' serial ' + serial if serial else ''} not found."
                                 + (UDEV_HINT if sys.platform.startswith("linux") else ""))
        if len(devs) > 1:
            raise TransportError("several probes found, pick one with --serial: "
                                 + ", ".join(str(_serial(d)) for d in devs))
        dev = devs[0]
        try:
            for cfg in dev:
                for intf in cfg:
                    name = usb.util.get_string(dev, intf.iInterface) if intf.iInterface else ""
                    if name and "CMSIS-DAP" in name:
                        self._open(dev, intf, usb)
                        return
        except usb.core.USBError as e:
            raise TransportError(f"USB access failed: {e}." + (UDEV_HINT if sys.platform.startswith("linux") else ""))
        raise TransportError("no CMSIS-DAP interface on the probe")

    def _open(self, dev, intf, usb):
        def ep(direction):
            return usb.util.find_descriptor(
                intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction)
        self.out, self.inp = ep(usb.util.ENDPOINT_OUT), ep(usb.util.ENDPOINT_IN)
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
        self.usb_error = usb.core.USBError

    def xfer(self, req):
        if len(req) > PACKET:  # the probe runs each 64-byte packet as its own command
            raise TransportError(f"request is {len(req)} bytes, the probe takes at most {PACKET}")
        try:
            self.out.write(req, self.timeout)
            return bytes(self.inp.read(PACKET, self.timeout))
        except self.usb_error as e:
            raise TransportError(f"USB transfer failed: {e}")


def _serial(dev):
    try:
        return dev.serial_number
    except Exception:  # no permission to read string descriptors
        return None
