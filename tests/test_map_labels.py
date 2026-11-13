"""Call labels on the path maps and controls on the contest movie.

Stations worked at the same spot used to get one label each, drawn on top of
each other (VE3FN over VE2ZAZ read as a station that wasn't in the log), and
the movie had Region/Area/Local buttons where people expected plus/minus.
"""
import json
import re

from gridmapper import maidenhead_map as mm


def test_stations_at_one_spot_share_one_label():
    groups = mm._colocated_contact_groups("FN03CN", [
        ("VE2ZAZ", "FN03HV"), ("VE3FN", "FN03HV"), ("VE3KH", "FN03FT"), ("VE2ZAZ", "FN03HV"),
    ])
    assert [g["calls"] for g in groups] == [["VE2ZAZ", "VE3FN"], ["VE3KH"]]
    assert mm._group_label(groups[0]).split("\n")[:2] == ["VE2ZAZ", "VE3FN"]
    assert mm._group_label(groups[0]).endswith(" km")


def test_a_crowded_spot_lists_the_first_calls_then_a_count():
    contacts = [(f"W{i}AA", "FN03HV") for i in range(9)]
    label = mm._group_label(mm._colocated_contact_groups("FN03CN", contacts)[0])
    lines = label.split("\n")
    assert lines[:mm._MAX_CALLS_PER_LABEL] == [c for c, _ in contacts[:mm._MAX_CALLS_PER_LABEL]]
    assert lines[mm._MAX_CALLS_PER_LABEL] == f"+{9 - mm._MAX_CALLS_PER_LABEL} more"


def _overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def test_overlapping_labels_are_moved_apart_and_others_stay_put():
    boxes = [(0, 100, 60, 120), (10, 95, 70, 115), (300, 100, 360, 120)]
    home = (20, 60, 120, 75)
    offsets = mm._declutter_offsets(boxes, fixed=[home])
    moved = [(b[0], b[1] + d, b[2], b[3] + d) for b, d in zip(boxes, offsets)]
    assert offsets[0] == 0 and offsets[2] == 0
    for i in range(len(moved)):
        assert not _overlap(moved[i], home)
        for j in range(i + 1, len(moved)):
            assert not _overlap(moved[i], moved[j])


def test_path_maps_label_a_shared_spot_once(tmp_path):
    contacts = [("VE2ZAZ", "FN03HV"), ("VE3FN", "FN03HV"), ("VA3HES", "FN03FS"),
                ("VE3SMA", "FN03FS"), ("VE3KH", "FN03FT")]
    records = [{"call": c, "grid": g} for c, g in contacts]
    html_file = tmp_path / "paths.html"
    mm.create_path_map_html("FN03CN", contacts, records, "VA3ELE", "10 GHz", output_file=str(html_file))
    data = json.loads(re.search(r"const DATA = (.*?);\n", html_file.read_text(encoding="utf-8")).group(1))
    labels = sorted(m["label"] for m in data["markers"])
    assert labels[0].startswith("VA3HES\nVE3SMA\n")
    assert labels[1].startswith("VE2ZAZ\nVE3FN\n")
    assert len(data["markers"]) == 3
    # One popup per spot lists both stations.
    popup = next(m["popup"] for m in data["markers"] if m["label"].startswith("VE2ZAZ"))
    assert "VE2ZAZ" in popup and "VE3FN" in popup

    png_file = tmp_path / "paths.png"
    mm.create_path_map("FN03CN", contacts, "VA3ELE", "10 GHz", output_file=str(png_file))
    assert png_file.stat().st_size > 0


def test_movie_zooms_with_plus_and_minus():
    page = mm._ANIMATION_PAGE_TEMPLATE
    assert 'id="zoom-in"' in page and 'id="zoom-out"' in page
    assert "data-zoom=" not in page and ">Region<" not in page
