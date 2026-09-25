"""Scoring must follow the ARRL 10 GHz and Up rules, section 5.2:
10 GHz x1, 24 GHz x2, 47 GHz x3, 75 GHz x4, 122 GHz and up x5."""
import os
import sys

import pandas as pd
import pytest

from conftest import APP_DIR

sys.path.insert(0, os.path.join(APP_DIR, "analyzer"))

import arrl_10ghz_cabrillo  # noqa: E402
from data_source import BAND_BUCKETS, band_multiplier  # noqa: E402

RULES_MULTIPLIERS = {
    "10 GHz": 1, "24 GHz": 2, "47 GHz": 3, "78 GHz": 4,
    "122 GHz": 5, "142 GHz": 5, "241 GHz": 5, "300 GHz": 5,
}


def test_band_table_matches_rules():
    assert {name: mult for name, _, mult, _ in BAND_BUCKETS} == RULES_MULTIPLIERS


@pytest.mark.parametrize("band, expected", [
    ("10", 1), ("10G", 1), ("24", 2), ("47G", 3), ("75G", 4), ("78", 4),
    ("122", 5), ("123G", 5), ("142", 5), ("142G", 5), ("241", 5), ("241G", 5),
    ("300", 5), ("300G", 5),
])
def test_band_multiplier_aliases(band, expected):
    assert band_multiplier(band) == expected


def test_upper_band_score():
    # FN32 to FN33 is a little over 111 km; the same QSO on 241 GHz scores
    # 5x distance plus the 100-point per-call-per-band bonus.
    row = {"date": "2026-09-20", "time": "1200", "call": "W1AAA",
           "sourcegrid": "FN32kp", "grid": "FN33kp"}
    df = pd.DataFrame([dict(row, band="10"), dict(row, band="241")])
    km = max(1, -(-arrl_10ghz_cabrillo.calculate_distance("FN32kp", "FN33kp") // 1))
    assert arrl_10ghz_cabrillo.calculate_score(df) == int(km * 1 + km * 5 + 200)
