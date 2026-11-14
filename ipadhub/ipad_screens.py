"""Offline iPad screen sizes for pointer calibration.

Apple's model list https://support.apple.com/en-us/108043 maps each A-number
to a technical specification. Source IDs beside the entries below resolve as
https://support.apple.com/en-us/<id>. Each row uses native pixels and the
published pixels per inch. The resulting dimensions are approximate sides of
the full display rectangle in landscape. Rounded corners reduce the lit area.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IPadScreen:
    label: str
    pixels: tuple[int, int]
    ppi: int
    model_numbers: tuple[str, ...]

    @property
    def width_mm(self) -> float:
        return self.pixels[0] * 25.4 / self.ppi

    @property
    def height_mm(self) -> float:
        return self.pixels[1] * 25.4 / self.ppi


def _screen(label, width, height, ppi, model_numbers):
    return IPadScreen(label, (width, height), ppi, tuple(model_numbers.split()))


IPAD_SCREENS = (
    _screen("iPad Pro 11 英寸（M5）", 2420, 1668, 264, "A3357 A3358 A3359"),  # 125406
    _screen("iPad Pro 11 英寸（M4）", 2420, 1668, 264, "A2836 A2837 A3006"),  # 119892
    # 111974, 118452, 111897, 111842
    _screen("iPad Pro 11 英寸（第 1–4 代）", 2388, 1668, 264,
            "A1980 A2013 A1934 A1979 A2228 A2068 A2230 A2231 A2377 A2459 A2301 A2460 A2759 A2761 A2435 A2762"),
    # 125407, 119891
    _screen("iPad Pro 13 英寸（M4、M5）", 2752, 2064, 264, "A2925 A2926 A3007 A3360 A3361 A3362"),
    # 112024, 111964, 111979, 111977, 111896, 111841
    _screen("iPad Pro 12.9 英寸（第 1–6 代）", 2732, 2048, 264,
            "A1584 A1652 A1670 A1671 A1821 A1876 A2014 A1895 A1983 A2229 A2069 A2232 A2233 "
            "A2378 A2461 A2379 A2462 A2436 A2437 A2764 A2766"),
    _screen("iPad Pro 10.5 英寸", 2224, 1668, 264, "A1701 A1709 A1852"),  # 111927
    _screen("iPad Pro 9.7 英寸", 2048, 1536, 264, "A1673 A1674 A1675"),  # 111965
    # 119894, 122241, 126471
    _screen("iPad Air 11 英寸（M2、M3、M4）", 2360, 1640, 264,
            "A2902 A2903 A2904 A3266 A3267 A3270 A3459 A3460 A3463"),
    # 119893, 122242, 126472
    _screen("iPad Air 13 英寸（M2、M3、M4）", 2732, 2048, 264,
            "A2898 A2899 A2900 A3268 A3269 A3271 A3461 A3462 A3464"),
    # 111905, 111887
    _screen("iPad Air 10.9 英寸（第 4、5 代）", 2360, 1640, 264, "A2316 A2324 A2325 A2072 A2588 A2589 A2591"),
    _screen("iPad Air 10.5 英寸（第 3 代）", 2224, 1668, 264, "A2152 A2123 A2153 A2154"),  # 111939
    # 112020, 112017
    _screen("iPad Air 9.7 英寸（第 1、2 代）", 2048, 1536, 264, "A1474 A1475 A1476 A1566 A1567"),
    _screen("iPad 11 英寸（A16）", 2360, 1640, 264, "A3354 A3355 A3356"),  # 122240
    _screen("iPad 10.9 英寸（第 10 代）", 2360, 1640, 264, "A2696 A2757 A2777 A3162"),  # 111840
    # 111911, 118451, 111898
    _screen("iPad 10.2 英寸（第 7–9 代）", 2160, 1620, 264, "A2197 A2200 A2198 A2270 A2428 A2429 A2430 A2602 A2604 A2603 A2605"),
    # 111992, 111993, 111960, 111957
    _screen("iPad 9.7 英寸（第 3–6 代）", 2048, 1536, 264, "A1416 A1430 A1403 A1458 A1459 A1460 A1822 A1823 A1893 A1954"),
    _screen("iPad 9.7 英寸（第 1、2 代）", 1024, 768, 132, "A1219 A1337 A1395 A1396 A1397"),  # 112438, 111990
    _screen("iPad mini 8.5 英寸（A20 Pro）", 2316, 1512, 326, "A3595 A3596 A3597"),  # 149482
    # 111886, 121456
    _screen("iPad mini 8.3 英寸（第 6 代、A17 Pro）", 2266, 1488, 326, "A2567 A2568 A2569 A2993 A2995 A2996"),
    # 112019, 112018, 112002, 111904
    _screen("iPad mini 7.9 英寸（第 2–5 代）", 2048, 1536, 326, "A1489 A1490 A1491 A1599 A1600 A1538 A1550 A2133 A2124 A2126 A2125"),
    _screen("iPad mini 7.9 英寸（第 1 代）", 1024, 768, 163, "A1432 A1454 A1455"),  # 111978
)
