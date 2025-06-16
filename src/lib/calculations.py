from collections import namedtuple

CalibrationPoint = namedtuple("CalibrationPoint", ("weight", "raw_value"))


def calculate_linear_regression(
    calibration_points: list[CalibrationPoint],
) -> tuple[float, float]:
    """Calculate slope and intercept for linear conversion using least squares"""
    n = len(calibration_points)
    sum_x = sum(point[0] for point in calibration_points)
    sum_y = sum(point[1] for point in calibration_points)
    sum_xy = sum(point[0] * point[1] for point in calibration_points)
    sum_x2 = sum(point[0] ** 2 for point in calibration_points)

    slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x**2)
    intercept = (sum_y - slope * sum_x) / n

    return slope, intercept


def get_median_value(values: list) -> float:
    """Get the median value from a list of readings"""
    if not values:
        return 0.0

    sorted_values = sorted(values)
    n = len(sorted_values)

    if n % 2 == 0:
        return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0
    else:
        return sorted_values[n // 2]
