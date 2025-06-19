from machine import Pin, SPI
import time


class HX711:
    def __init__(self, clock_pin: Pin, data_pin: Pin, spi_instance: SPI, gain: int=128) -> None:
        # Initialize pins and SPI
        self.clock_pin = clock_pin
        self.data_pin = data_pin
        self.spi = spi_instance
        
        # Ensure clock starts in correct state
        self.clock_pin.value(0)
        
        # Power state tracking
        self.is_powered_down = False
        self.stabilization_time = 0.4  # Time needed after power-up (400ms)
        
        # Initialize other attributes (keeping original structure)
        self.clock_25 = b"\xaa\xaa\xaa\xaa\xaa\xaa\x80"
        self.clock_26 = b"\xaa\xaa\xaa\xaa\xaa\xaa\xa0" 
        self.clock_27 = b"\xaa\xaa\xaa\xaa\xaa\xaa\xa8"
        self.clock = self.clock_25
        
        # Lookup table for data conversion
        self.lookup = (
            b"\x00\x01\x00\x00\x02\x03\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x04\x05\x00\x00\x06\x07\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x08\x09\x00\x00\x0a\x0b\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x0c\x0d\x00\x00"
            b"\x0e\x0f"
        )
        
        self.in_data = bytearray(7)
        self.OFFSET = 0
        self.SCALE = 1
        self.time_constant = 0.1
        self.filtered = 0
        
        # Set initial gain and get first reading
        self.set_gain(gain)

    def power_down(self) -> None:
        """
        Put HX711 into low-power sleep mode.
        Clock pin must stay high for >60μs to trigger power-down.
        """
        if self.is_powered_down:
            return  # Already powered down
            
        # Set clock high and hold for sufficient time
        self.clock_pin.value(1)
        time.sleep_us(100)  # Hold high for 100μs (well above 60μs minimum)
        
        self.is_powered_down = True
        print("HX711 powered down - now in sleep mode")

    def power_up(self) -> None:
        """
        Wake up HX711 from power-down mode.
        Requires setting clock low and waiting for stabilization.
        """
        if not self.is_powered_down:
            return  # Already powered up
            
        # Pull clock low to wake up the chip
        self.clock_pin.value(0)
        
        # Wait for internal oscillator and circuits to stabilize
        print("Waking up HX711... waiting for stabilization")
        time.sleep(self.stabilization_time)
        
        # Clear the powered down flag
        self.is_powered_down = False
        
        # Take a dummy reading to ensure chip is responding
        try:
            self.read()
            print("HX711 powered up and ready")
        except OSError:
            print("Warning: HX711 may not be fully ready yet")

    def safe_read(self) -> int:
        """
        Read data with automatic power-up if needed.
        This ensures readings work even if chip was powered down.
        """
        if self.is_powered_down:
            self.power_up()
            
        return self.read()

    def read(self) -> int:
        """
        Read 24-bit value from HX711
        """
        if self.is_powered_down:
            raise OSError("Cannot read - HX711 is powered down. Call power_up() first.")
            
        # Wait for the device to get ready (data pin goes low)
        for _ in range(500):
            if self.data_pin.value() == 0:
                break
            time.sleep_ms(1)
        else:
            raise OSError("Sensor does not respond - check connections")

        # Get the data and set channel/gain for next reading
        self.spi.write_readinto(self.clock, self.in_data)

        # Convert received bytes to single 24-bit value
        result = 0
        for i in range(6):
            result = (result << 4) + self.lookup[self.in_data[i] & 0x55]

        # Handle two's complement (convert to signed value)
        return result - ((result & 0x800000) << 1)

    def graceful_shutdown(self):
        """
        Complete shutdown sequence for clean device disconnection.
        Call this before turning off main power or disconnecting.
        """
        print("Initiating graceful HX711 shutdown...")
        
        # Put chip to sleep first
        self.power_down()
        
        # Small delay to ensure power-down is complete
        time.sleep_ms(50)
        
        # Optional: You could add code here to disable power supply
        # if you have control over it through another pin
        
        print("HX711 shutdown complete - safe to disconnect")

    def set_gain(self, gain):
        """Set gain with power state awareness"""
        if self.is_powered_down:
            self.power_up()
            
        if gain == 128:
            self.clock = self.clock_25
        elif gain == 64:
            self.clock = self.clock_27
        elif gain == 32:
            self.clock = self.clock_26
        else:
            raise ValueError("Gain must be 128, 64, or 32")

        # Take readings to apply the new gain setting
        self.read()
        self.filtered = self.read()
