_POSTCODE_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "UB": (51.4, 51.6, -0.5, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "TW": (51.4, 51.6, -0.6, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SE": (51.4, 51.5, -0.2, 0.1),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SW": (51.4, 51.6, -0.3, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "NW": (51.5, 51.6, -0.3, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "WC": (51.5, 51.6, -0.15, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "WN": (51.5, 51.6, -0.15, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "EC": (51.5, 51.6, -0.1, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "EN": (51.6, 51.7, -0.2, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "N": (51.5, 51.6, -0.2, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "E": (51.5, 51.6, -0.1, 0.1),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "W": (51.4, 51.6, -0.3, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "CR": (51.3, 51.5, -0.2, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "KT": (51.3, 51.5, -0.4, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SM": (51.3, 51.5, -0.3, -0.1),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "BR": (51.3, 51.5, -0.1, 0.1),
    "DA": (51.3, 51.5, 0.0, 0.3),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "HA": (51.5, 51.7, -0.4, -0.2),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "IG": (51.5, 51.7, -0.1, 0.1),
    "RM": (51.5, 51.6, 0.0, 0.3),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SL": (51.4, 51.6, -0.7, -0.4),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "RG": (51.3, 51.5, -1.0, -0.7),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "GU": (51.2, 51.4, -0.8, -0.4),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "LU": (51.8, 52.0, -0.6, -0.3),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "HP": (51.6, 51.8, -0.8, -0.4),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SG": (51.8, 52.1, -0.4, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "AL": (51.7, 51.9, -0.4, -0.1),
    "CM": (51.6, 51.9, 0.2, 0.6),
    "SS": (51.5, 51.6, 0.6, 0.9),
    "ME": (51.3, 51.5, 0.4, 0.7),
    "TN": (51.0, 51.3, 0.1, 0.5),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "RH": (51.0, 51.3, -0.3, 0.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "BN": (50.8, 51.0, -0.2, 0.2),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "OX": (51.5, 52.0, -1.5, -1.0),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "MK": (51.9, 52.1, -0.9, -0.6),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "PO": (50.7, 51.0, -1.2, -0.9),
    # lucidlint: ignore magic-number postcode bounds are tabular coordinate data, not operands
    "SO": (50.8, 51.0, -1.5, -1.2),
}


def valid_location(lat: float, lng: float, postcode: str) -> bool:
    if not postcode:
        return True
    area = postcode.strip().split()[0] if " " in postcode else postcode
    for prefix, (lat_min, lat_max, lng_min, lng_max) in _POSTCODE_BOUNDS.items():
        if area.startswith(prefix):
            return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max
    return True
