from calculations import (CalibrationPoint, calculate_linear_regression,
                          get_median_value)
from device import Esp32HX711
from machine import Pin

led = Pin(8, Pin.OUT)


def read_calibration_values(
    hx711: Esp32HX711, known_weight_kg: str, samples: int = 1_000
) -> float:
    """
    Reads the given number of samples from a H711 and returns the median value.

    This will also print the read value for the given known weight.
    """
    raw_values = hx711.read_multiple_values(samples=samples)

    raw_median_value = get_median_value(raw_values)
    print("#" * 10)
    print(
        f"Raw middle reading value for known weight {known_weight_kg}kg is: {raw_median_value}. Raw values: {raw_values}"
    )
    print("#" * 10)
    return raw_median_value


def calibrate():
    """
    Function that will execute the calibration logic.

    This will wait for the user to input the current weight value and will print the median of the raw 
    values based on the samples amount.

    A good starting point of weights to be used for calibration is:
    0% -> No additional load attached to the load cell
    25% -> 25% of the maximum load of the load cell
    50% -> 50% of the maximum load of the load cell

    To end the calibration, iterrupt with keyboard.
    """
    hx711 = Esp32HX711(dt=5, sck=6)
    hx711.setup()

    calibration_points: list[CalibrationPoint] = []
    while True:
        try:
            samples_number = (
                int(
                    input(
                        "Place the number of samples that will be used during the calibration process (default 1000): "
                    )
                )
                or 1_000
            )
            inputed_weight = input(
                "Place a known weight on the scale, wait it to stabilize: "
            )
            if not inputed_weight or not inputed_weight.isnumeric():
                print("No values provided...")
                continue
            calibration_points.append(
                CalibrationPoint(
                    weight=inputed_weight,
                    raw_value=read_calibration_values(
                        hx711=hx711,
                        known_weight_kg=inputed_weight,
                        samples=samples_number,
                    ),
                )
            )
        except KeyboardInterrupt:
            break

    hx711.off()
    slope, intercept = calculate_linear_regression(calibration_points)
    print("\n")
    print("#" * 10)
    print("Finished calibration.")
    print(f"Slope: {slope} | Intercept: {intercept}")
    print("Calibration points")
    print(calibration_points)
    print("#" * 10)


if __name__ == "calibration":
    calibrate()
