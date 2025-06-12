import asyncio

from calculations import calculate_linear_regression, CalibrationPoint
from device import Esp32HX711, UCrimpDevice


async def main() -> None:
    calibration_points = [
        CalibrationPoint(weight=0.0, raw_value=137312.0),
        CalibrationPoint(weight=61.20, raw_value=1284359.0),
        CalibrationPoint(weight=81.50, raw_value=1734991.0),
    ]
    slope, intercept = calculate_linear_regression(calibration_points)
    hx711 = Esp32HX711(dt=5, sck=6)
    device = UCrimpDevice(
        hx711=hx711, slope=slope, intercept=intercept, raw_samples_amount=1
    )
    try:
        await device.start()
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        await device.stop()


if __name__ == "__main__":
    asyncio.run(main())
