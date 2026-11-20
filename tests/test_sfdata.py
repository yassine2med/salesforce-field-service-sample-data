import json
import shutil
from pathlib import Path

import pytest

from sfdata.cli import main
from sfdata.core import build_tree, check, write_tree

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def data(tmp_path):
    dest = tmp_path / "data"
    shutil.copytree(ROOT / "data", dest)
    return dest


def msgs(folder):
    return [str(i) for i in check(folder)[0]]


def replace(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_shipped_data_is_clean():
    issues, counts = check(ROOT / "data")
    assert issues == []
    assert counts == {"operating_hours": 3, "time_slots": 12, "territories": 4, "work_types": 5}


def test_bad_example_reports_every_family_of_problem():
    out = " | ".join(msgs(ROOT / "examples" / "bad_data"))
    for needle in ("TimeZone", "overlaps", "Funday", "EndTime must be after", "not defined", "no time slots",
                   "IsActive", "PostalCode", "duplicate Name", "Latitude", "EstimatedDuration", "DurationType"):
        assert needle in out


def test_missing_file(data):
    (data / "work_types.csv").unlink()
    assert any("file not found" in m for m in msgs(data))


def test_missing_column(data):
    replace(data / "work_types.csv", "DurationType", "Unit")
    assert any("missing column(s): DurationType" in m for m in msgs(data))


def test_duplicate_ref(data):
    replace(data / "operating_hours.csv", "hoursExt", "hoursStd")
    assert any("duplicate ref 'hoursStd'" in m for m in msgs(data))


def test_bad_ref_format(data):
    replace(data / "operating_hours.csv", "hoursWknd", "1bad-ref")
    assert any("ref '1bad-ref'" in m for m in msgs(data))


def test_slot_end_before_start(data):
    replace(data / "time_slots.csv", "hoursStd,Monday,08:00,17:00", "hoursStd,Monday,18:00,17:00")
    assert any("EndTime must be after StartTime" in m for m in msgs(data))


def test_slot_overlap(data):
    with open(data / "time_slots.csv", "a", encoding="utf-8") as fh:
        fh.write("hoursStd,Monday,12:00,13:00\n")
    assert any("overlaps another Monday slot" in m for m in msgs(data))


def test_back_to_back_slots_are_allowed(data):
    with open(data / "time_slots.csv", "a", encoding="utf-8") as fh:
        fh.write("hoursStd,Monday,17:00,18:00\n")
    assert msgs(data) == []


def test_territory_with_unknown_hours(data):
    replace(data / "service_territories.csv", "terrBAL,Baltimore Metro,hoursStd", "terrBAL,Baltimore Metro,hoursNone")
    assert any("'hoursNone' is not defined" in m for m in msgs(data))


def test_latitude_range(data):
    replace(data / "service_territories.csv", "38.9072", "98.9072")
    assert any("Latitude" in m for m in msgs(data))


def test_work_type_duration_rules(data):
    replace(data / "work_types.csv", "30,Minutes", "-5,Minutes")
    assert any("above zero" in m for m in msgs(data))
    replace(data / "work_types.csv", "2,Hours", "25,Hours")
    assert any("longer than 24 hours" in m for m in msgs(data))


def test_tree_records_and_references():
    tree = build_tree(ROOT / "data")
    slot = tree["TimeSlot.json"]["records"][0]
    assert slot["OperatingHoursId"] == "@hoursStd" and slot["StartTime"] == "08:00:00.000Z"
    terr = tree["ServiceTerritory.json"]["records"]
    assert terr[0]["IsActive"] is True and terr[3]["IsActive"] is False
    assert isinstance(terr[0]["Latitude"], float)


def test_every_reference_points_at_a_saved_ref():
    tree = build_tree(ROOT / "data")
    saved = {r["attributes"]["referenceId"] for r in tree["OperatingHours.json"]["records"]}
    for name in ("TimeSlot.json", "ServiceTerritory.json"):
        for rec in tree[name]["records"]:
            assert rec["OperatingHoursId"].lstrip("@") in saved


def test_plan_loads_parents_before_children():
    plan = build_tree(ROOT / "data")["plan.json"]
    order = [p["sobject"] for p in plan]
    assert order.index("OperatingHours") < order.index("TimeSlot") < order.index("ServiceTerritory")
    assert plan[0]["saveRefs"] is True and plan[1]["resolveRefs"] is True


def test_committed_tree_matches_a_fresh_build(tmp_path):
    write_tree(ROOT / "data", tmp_path)
    for f in tmp_path.iterdir():
        assert json.loads(f.read_text()) == json.loads((ROOT / "tree" / f.name).read_text()), f.name


def test_cli_codes_and_build(tmp_path, capsys):
    assert main(["check", str(ROOT / "data")]) == 0
    assert main(["check", str(ROOT / "examples" / "bad_data")]) == 1
    assert main(["check", str(tmp_path / "nope")]) == 2
    out = tmp_path / "out"
    assert main(["build", str(ROOT / "data"), "--out", str(out)]) == 0
    assert (out / "plan.json").exists()
    assert main(["build", str(ROOT / "examples" / "bad_data"), "--out", str(tmp_path / "x")]) == 1
    assert not (tmp_path / "x").exists()
