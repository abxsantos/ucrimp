# MicroPython aioble module
# MIT license; Copyright (c) 2021 Jim Mussared

from micropython import const

from .core import GattError, config, log_error, log_info, log_warn, stop
from .device import Device, DeviceDisconnectedError

try:
    from .peripheral import advertise
except:
    log_info("Peripheral support disabled")

try:
    from .central import scan
except:
    log_info("Central support disabled")

try:
    from .server import (BufferedCharacteristic, Characteristic, Descriptor,
                         Service, register_services)
except:
    log_info("GATT server support disabled")


ADDR_PUBLIC = const(0)
ADDR_RANDOM = const(1)
