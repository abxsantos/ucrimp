import asyncio
import struct

import aioble
import bluetooth
import utime
from calculations import get_median_value
from hx711_spi import HX711
from machine import SPI, Pin

_DEFAULT_DEVICE_NAME = const("Progressor")
_DEVICE_ID = const("AAAAAA")
_DEVICE_VERSION = const("1.0.0")
_DEFAULT_RAW_SAMPLES = const(3)
_ADVERTISE_INTERVAL_US = const(50_000)
_WEIGHT_APPEARANCE = const(0x0C80)
_RECONNECT_DELAY_S = const(1)
_MEASUREMENT_INTERVAL_S = const(0)
_DISCONNECTED_DELAY_S = const(0.5)
_SERVICE_UUID = const("7e4e1701-1ea6-40c9-9dcc-13d34ffead57")
_DATA_POINT_CHARACTERISTIC_UUID = const("7e4e1702-1ea6-40c9-9dcc-13d34ffead57")
_CONTROL_POINT_CHARACTERISTIC_UUID = const("7e4e1703-1ea6-40c9-9dcc-13d34ffead57")

MAX_PAYLOAD_SIZE = const(10)
DEVICE_ID_SIZE = const(6)


class MeasurementTaskStatus:
    """Status of the weight measurement task"""

    DISABLED = 0
    ENABLED = 1
    CALIBRATION = 2
    TARE = 3
    DEFAULT_CALIBRATION = 4


class CommandCode:
    TARE_SCALE = 0x64
    START_MEASUREMENT = 0x65
    STOP_MEASUREMENT = 0x66
    SHUTDOWN = 0x6E
    SAMPLE_BATTERY = 0x6F
    GET_PROGRESSOR_ID = 0x70
    GET_APP_VERSION = 0x6B
    GET_CALIBRATION = 0x72
    ADD_CALIBRATION_POINT = 0x73
    DEFAULT_CALIBRATION = 0x74


class ResponseCode:
    """Data point response codes"""

    SAMPLE_BATTERY_VOLTAGE = 0x00
    WEIGHT_MEASUREMENT = 0x01
    LOW_POWER_WARNING = 0x04
    APP_VERSION = 0x00
    PROGRESSOR_ID = 0x00


class DataPoint:
    """Data point characteristic"""

    def __init__(self, response_code: int, length: int, data: bytes = b""):
        self.response_code = response_code
        self.length = length
        self.value = bytearray(MAX_PAYLOAD_SIZE)
        if data and length > 0:
            copy_len = min(length, len(data), MAX_PAYLOAD_SIZE)
            self.value[:copy_len] = data[:copy_len]

    def as_gatt(self) -> bytes:
        """Convert to GATT format"""
        result = bytearray([self.response_code, self.length])
        if self.length > 0:
            result.extend(self.value[: self.length])
        return bytes(result)

    @classmethod
    def weight_measurement(cls, weight: float, timestamp: int):
        """Create a weight measurement data point"""
        data = struct.pack("<fL", weight, timestamp)
        return cls(ResponseCode.WEIGHT_MEASUREMENT, 8, data)

    @classmethod
    def battery_voltage(cls, voltage: int):
        """Create a battery voltage data point"""
        data = struct.pack("<L", voltage)
        return cls(ResponseCode.SAMPLE_BATTERY_VOLTAGE, 4, data)

    @classmethod
    def app_version(cls, version: str):
        """Create an app version data point"""
        data = version.encode("utf-8")
        return cls(ResponseCode.APP_VERSION, len(data), data)

    @classmethod
    def progressor_id(cls, device_id: str):
        """Create a progressor ID data point"""
        # Convert hex string to bytes and reverse (as per Rust code)
        if len(device_id) >= 6:
            device_id_bytes = device_id.encode("utf-8")[:6]
        else:
            device_id_bytes = device_id.encode("utf-8").ljust(6, b"\x00")

        reversed_id = bytes(reversed(device_id_bytes))
        return cls(ResponseCode.PROGRESSOR_ID, DEVICE_ID_SIZE, reversed_id)


class Esp32HX711:
    def __init__(self, dt: int = 5, sck: int = 6, led_pin: int = 8) -> None:
        self.led = Pin(led_pin, Pin.OUT)
        pin_OUT = Pin(dt, Pin.IN, pull=Pin.PULL_DOWN)
        pin_SCK = Pin(sck, Pin.OUT)
        spi = SPI(
            1,
            baudrate=115_200,
            polarity=0,
            phase=0,
            sck=Pin(4),
            mosi=pin_SCK,
            miso=pin_OUT,
        )

        self.hx711 = HX711(pin_SCK, pin_OUT, spi)

    def setup(self) -> None:
        self.led.value(0)
        self.hx711.set_gain(128)
        self.hx711.get_value()

    def off(self) -> None:
        self.led.value(1)

    def read_raw_value(self) -> float:
        return self.hx711.get_value()

    def read_multiple_values(self, samples: int = 3) -> list[float]:
        return [self.read_raw_value() for _ in range(samples)]


class UCrimpDevice:
    data_point_characteristic: aioble.Characteristic
    control_point_characteristic: aioble.Characteristic

    def __init__(
        self,
        hx711: Esp32HX711,
        slope: float,
        intercept: float,
        device_name: str = _DEFAULT_DEVICE_NAME,
        raw_samples_amount: int = _DEFAULT_RAW_SAMPLES,
        service_uuid: bluetooth.UUID = bluetooth.UUID(_SERVICE_UUID),
        data_point_characteristic_uuid: bluetooth.UUID = bluetooth.UUID(
            _DATA_POINT_CHARACTERISTIC_UUID
        ),
        control_point_characteristic_uuid=bluetooth.UUID(
            _CONTROL_POINT_CHARACTERISTIC_UUID
        ),
    ) -> None:
        self.hx711 = hx711

        # State
        self.connected_device = None
        self.is_running = asyncio.Event()
        self.measurement_status = MeasurementTaskStatus.DISABLED
        self.start_time = 0
        self.calibration_weight = 0.0
        self.calibration_points = [None, None]
        self.tare_offset = 0.0
        self.shutdown_requested = asyncio.Event()

        # Device configuration
        self.raw_samples_amount = raw_samples_amount
        self.tare_samples_amount = 10
        self.device_name = device_name
        self.slope = slope
        self.intercept = intercept

        # BLE UUIDs
        self.service_uuid = service_uuid
        self.data_point_characteristic_uuid = data_point_characteristic_uuid
        self.control_point_characteristic_uuid = control_point_characteristic_uuid

        # Initialize BLE service and characteristic
        self.setup_ble_service()

    def start_measurement(self):
        """Start a measurement"""
        self.start_time = utime.ticks_us()
        self.measurement_status = MeasurementTaskStatus.ENABLED

    def stop_measurement(self):
        """Stop the current measurement"""
        self.measurement_status = MeasurementTaskStatus.DISABLED
        self.start_time = 0

    def shutdown(self):
        """Request device shutdown"""
        self.shutdown_requested.set()
        self.measurement_status = MeasurementTaskStatus.DISABLED

    def setup_ble_service(self) -> None:
        """Initialize BLE service and characteristics"""
        try:
            self.ble_service = aioble.Service(self.service_uuid)

            self.data_point_characteristic = aioble.Characteristic(
                self.ble_service,
                self.data_point_characteristic_uuid,
                notify=True,
                read=True,
            )

            self.control_point_characteristic = aioble.Characteristic(
                self.ble_service,
                self.control_point_characteristic_uuid,
                write=True,
                write_no_response=True,
            )
            aioble.register_services(self.ble_service)
            print("BLE service initialized successfully")
        except Exception as e:
            print(f"Failed to setup BLE service: {e}")
            raise

    def raw_to_kg(self, raw_value: float) -> float:
        """Convert raw sensor value to weight in kilograms"""
        if raw_value == self.tare_offset:
            calculated_weight = 0.0
        else:
            calculated_weight = (
                raw_value - self.tare_offset - self.intercept
            ) / self.slope

        final_weight = max(0.00, round(calculated_weight, 2))
        final_weight = final_weight if final_weight > 0.5 else 0.0
        return final_weight

    def perform_tare(self) -> bool:
        """Perform tare operation"""
        try:
            print("Performing tare operation...")
            raw_values = self.hx711.read_multiple_values(
                samples=self.tare_samples_amount
            )
            if not raw_values:
                print("Failed to get readings for tare")
                return False

            self.tare_offset = get_median_value(raw_values)

            print(f"Tare completed - offset set to: {self.tare_offset}")
            return True

        except Exception as e:
            print(f"Tare operation failed: {e}")
            return False

    async def send_weight_measurement(self) -> None:
        """Send a weight measurement data point with current timestamp"""
        if not self.connected_device:
            return
        try:
            raw_values = self.hx711.read_multiple_values(
                samples=self.raw_samples_amount
            )
            weight = self.raw_to_kg(get_median_value(raw_values))

            # Calculate timestamp since measurement started
            current_time = utime.ticks_us()
            timestamp = utime.ticks_diff(current_time, self.start_time)

            data_point = DataPoint.weight_measurement(weight, timestamp)

            # Send directly via BLE notification with error handling
            try:
                self.data_point_characteristic.notify(
                    connection=self.connected_device, data=data_point.as_gatt()
                )
            except Exception as ble_error:
                print(f"BLE notification failed: {ble_error}")
                self.connected_device = None
                self.stop_measurement()
                return
        except Exception as e:
            print(f"Error in send_weight_measurement: {e}")

    def read_battery_voltage(self) -> int:
        # TODO: Implement actual battery voltage reading
        return 4300

    def process_control_command(self, op_code: int, data: bytes = b""):
        print(f"Processing command: 0x{op_code:02x}")

        if op_code == CommandCode.TARE_SCALE:
            print("Tare command received")
            self.measurement_status = MeasurementTaskStatus.TARE

        elif op_code == CommandCode.START_MEASUREMENT:
            print("Start measurement command received")
            # Don't start measurement if shutdown is requested
            if not self.shutdown_requested.is_set():
                self.start_time = utime.ticks_us()
                self.measurement_status = MeasurementTaskStatus.ENABLED
            else:
                print("Ignoring start measurement - device is shut down")

        elif op_code == CommandCode.STOP_MEASUREMENT:
            print("Stop measurement command received")
            self.stop_measurement()

        elif op_code == CommandCode.GET_APP_VERSION:
            print("Get app version command received")
            response = DataPoint.app_version(_DEVICE_VERSION)
            self.send_data_point(response)

        elif op_code == CommandCode.GET_PROGRESSOR_ID:
            print("Get progressor ID command received")
            response = DataPoint.progressor_id(_DEVICE_ID)
            self.send_data_point(response)

        elif op_code == CommandCode.SAMPLE_BATTERY:
            print("Sample battery voltage command received")
            voltage = self.read_battery_voltage()
            response = DataPoint.battery_voltage(voltage)
            self.send_data_point(response)

        elif op_code == CommandCode.ADD_CALIBRATION_POINT:
            print("Add calibration point command received")
            # TODO: Implement calibration point logic

        elif op_code == CommandCode.DEFAULT_CALIBRATION:
            print("Reset to default calibration command received")
            # TODO: Implement calibration point logic

        elif op_code == CommandCode.SHUTDOWN:
            print(
                "Shutdown command received - stopping measurements and resetting state"
            )
            # Stop any ongoing measurements
            self.stop_measurement()
            # Set shutdown flag to prevent new measurements
            self.shutdown()
            print("Device shutdown complete - measurements disabled")

        else:
            print(f"Unknown command: 0x{op_code:02x}")

    def send_data_point(self, data_point: DataPoint) -> None:
        """Send data point via BLE"""
        if self.connected_device:
            try:
                self.data_point_characteristic.notify(
                    connection=self.connected_device, data=data_point.as_gatt()
                )
            except Exception as e:
                print(f"Failed to send data point: {e}")
                # Mark connection as lost on BLE error
                self.connected_device = None

    async def measurement_task(self) -> None:
        """Handle weight sensor readings based on device state"""
        print("Starting measurement task")
        while self.is_running.is_set():
            try:
                # Check if measurement should be stopped
                if (
                    self.measurement_status == MeasurementTaskStatus.ENABLED
                    and not self.connected_device
                    or self.shutdown_requested.is_set()
                ):
                    self.stop_measurement()
                    print("Stopping measurement")

                status = self.measurement_status
                if status == MeasurementTaskStatus.DISABLED:
                    await asyncio.sleep(0)

                elif status == MeasurementTaskStatus.TARE:
                    # Only perform tare if not shut down
                    if not self.shutdown_requested.is_set() and self.perform_tare():
                        self.measurement_status = MeasurementTaskStatus.DISABLED
                    else:
                        print("Ignoring tare operation - device is shut down")
                        self.measurement_status = MeasurementTaskStatus.DISABLED

                elif status == MeasurementTaskStatus.ENABLED:
                    if self.connected_device and not self.shutdown_requested.is_set():
                        await self.send_weight_measurement()
                        # Use measurement interval to allow other tasks to run
                        await asyncio.sleep(_MEASUREMENT_INTERVAL_S)
                    else:
                        self.stop_measurement()
                        print("Stopping measurement")

                elif status == MeasurementTaskStatus.CALIBRATION:
                    calibration_weight = self.calibration_weight
                    print(f"Calibration mode with weight: {calibration_weight}")
                    self.measurement_status = MeasurementTaskStatus.DISABLED

                elif status == MeasurementTaskStatus.DEFAULT_CALIBRATION:
                    # Reset calibration to default values
                    print("Resetting to default calibration")
                    self.measurement_status = MeasurementTaskStatus.DISABLED
            except asyncio.CancelledError:
                print("Measurement task cancelled")
                break
            except Exception as e:
                print(f"Critical measurement task error: {e}")
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def gatt_events_task(self) -> None:
        """Handle incoming control commands from BLE clients"""
        print("Starting GATT events task")

        while self.is_running.is_set():
            if not self.connected_device:
                await asyncio.sleep(_DISCONNECTED_DELAY_S)
                continue

            try:
                await self.control_point_characteristic.written()
                message = self.control_point_characteristic.read()

                if len(message) > 0:
                    op_code = message[0]
                    data = message[1:] if len(message) > 1 else b""
                    print(f"Control Point Received: 0x{op_code:02x}")
                    self.process_control_command(op_code, data)

            except asyncio.CancelledError:
                print("GATT events task cancelled")
                break
            except Exception as e:
                print(f"GATT events task error: {e}")
                # Reset connection state on error
                if self.connected_device:
                    print("Resetting connection state due to GATT error")
                    self.connected_device = None
                    self.stop_measurement()
                await asyncio.sleep(0.5)

    async def advertise(self) -> None:
        """Handle BLE advertising and connections"""
        print(f"Starting BLE advertising as '{self.device_name}'")

        while self.is_running.is_set():
            try:
                print(f"Advertising as {self.device_name}...")

                async with await aioble.advertise(
                    _ADVERTISE_INTERVAL_US,
                    name=self.device_name,
                    services=[self.service_uuid],
                    appearance=_WEIGHT_APPEARANCE,
                ) as connection:
                    print(f"Connected to {connection.device}")
                    self.connected_device = connection
                    # Reset shutdown flag when new connection is established
                    self.shutdown_requested.clear()
                    print("Shutdown flag reset - device ready for new session")

                    try:
                        await connection.disconnected()
                    except asyncio.CancelledError:
                        print("Connection handling cancelled")
                        break
                    finally:
                        print(f"Disconnected from {connection.device}")
                        # Reset device state when disconnected
                        self.connected_device = None
                        self.stop_measurement()
                        print("Device state reset due to disconnection")

            except asyncio.CancelledError:
                print("Advertise task cancelled")
                break
            except Exception as e:
                print(f"Advertising error: {e}")
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def start(self) -> None:
        """Start the BLE weight device"""
        self.hx711.setup()
        print(f"Starting BLE Weight Device: {self.device_name}")
        self.perform_tare()
        self.setup_ble_service()
        self.is_running.set()

        try:
            advertise_task = asyncio.create_task(self.advertise())
            measurement_task = asyncio.create_task(self.measurement_task())
            gatt_events_task = asyncio.create_task(self.gatt_events_task())

            # Run all tasks concurrently
            await asyncio.gather(advertise_task, measurement_task, gatt_events_task)

        except KeyboardInterrupt:
            print("Keyboard interrupt received")
        except Exception as e:
            print(f"Critical error in start(): {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stop the device"""
        print("Stopping BLE Weight Device...")
        self.is_running.clear()

        if self.connected_device:
            try:
                await self.connected_device.disconnect()
            except Exception as e:
                print(f"Error disconnecting: {e}")
        self.hx711.off()
        print("Device stopped successfully")
