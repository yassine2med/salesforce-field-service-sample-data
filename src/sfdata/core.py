"""Validate Field Service sample CSVs and convert them to `sf data import tree` files."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# A subset of the time zones Salesforce accepts. Extend it if you need others.
TIME_ZONES = {
    "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
    "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "UTC", "GMT",
}
DURATION_TYPES = ["Minutes", "Hours"]
REF = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

FILES = {
    "operating_hours": ("operating_hours.csv", ["ref", "Name", "TimeZone"]),
    "time_slots": ("time_slots.csv", ["operating_hours_ref", "DayOfWeek", "StartTime", "EndTime"]),
    "territories": ("service_territories.csv", [
        "ref", "Name", "operating_hours_ref", "IsActive", "City", "State", "Country", "PostalCode", "Latitude", "Longitude"]),
    "work_types": ("work_types.csv", ["ref", "Name", "EstimatedDuration", "DurationType"]),
}


@dataclass(frozen=True)
class Issue:
    file: str
    line: int
    message: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{where}: {self.message}"


def read(folder: Path, key: str) -> tuple[list[dict], list[Issue]]:
    """Read one CSV. Returns (rows with a '_line' key, header issues)."""
    name, columns = FILES[key]
    path = folder / name
    if not path.exists():
        return [], [Issue(name, 0, "file not found")]
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            return [], [Issue(name, 1, f"missing column(s): {', '.join(missing)}")]
        rows = []
        for n, row in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            row["_line"] = n
            rows.append(row)
    return rows, []


def _unique_refs(rows: list[dict], name: str, issues: list[Issue]) -> set[str]:
    seen: set[str] = set()
    for r in rows:
        ref = r["ref"]
        if not REF.match(ref):
            issues.append(Issue(name, r["_line"], f"ref '{ref}' must be letters, digits and underscores, starting with a letter"))
        elif ref in seen:
            issues.append(Issue(name, r["_line"], f"duplicate ref '{ref}'"))
        seen.add(ref)
    return seen


def _num(text: str):
    try:
        return float(text)
    except ValueError:
        return None


def check(folder: Path) -> tuple[list[Issue], dict]:
    """Check all four files and the links between them. Returns (issues, counts)."""
    issues: list[Issue] = []
    data = {}
    for key in FILES:
        data[key], problems = read(folder, key)
        issues += problems
    if any(i.message == "file not found" or i.message.startswith("missing column") for i in issues):
        return issues, {k: len(v) for k, v in data.items()}

    hours_file, slots_file = FILES["operating_hours"][0], FILES["time_slots"][0]
    hour_refs = _unique_refs(data["operating_hours"], hours_file, issues)
    names = set()
    for r in data["operating_hours"]:
        if r["TimeZone"] not in TIME_ZONES:
            issues.append(Issue(hours_file, r["_line"], f"TimeZone '{r['TimeZone']}' is not in the known list"))
        if not r["Name"] or len(r["Name"]) > 80:
            issues.append(Issue(hours_file, r["_line"], "Name is required and at most 80 characters"))
        elif r["Name"] in names:
            issues.append(Issue(hours_file, r["_line"], f"duplicate Name '{r['Name']}'"))
        names.add(r["Name"])

    used_hours: dict[str, list[tuple[str, str, str]]] = {}
    for r in data["time_slots"]:
        line = r["_line"]
        if r["operating_hours_ref"] not in hour_refs:
            issues.append(Issue(slots_file, line, f"operating_hours_ref '{r['operating_hours_ref']}' is not defined in {hours_file}"))
        if r["DayOfWeek"] not in DAYS:
            issues.append(Issue(slots_file, line, f"DayOfWeek '{r['DayOfWeek']}' must be one of {DAYS}"))
        if not HHMM.match(r["StartTime"]) or not HHMM.match(r["EndTime"]):
            issues.append(Issue(slots_file, line, "StartTime and EndTime must look like 08:00"))
        elif r["StartTime"] >= r["EndTime"]:
            issues.append(Issue(slots_file, line, "EndTime must be after StartTime"))
        else:
            for day, start, end in used_hours.get(r["operating_hours_ref"], []):
                if day == r["DayOfWeek"] and r["StartTime"] < end and start < r["EndTime"]:
                    issues.append(Issue(slots_file, line, f"overlaps another {day} slot for '{r['operating_hours_ref']}'"))
            used_hours.setdefault(r["operating_hours_ref"], []).append((r["DayOfWeek"], r["StartTime"], r["EndTime"]))
    for ref in sorted(hour_refs - set(used_hours)):
        issues.append(Issue(slots_file, 0, f"operating hours '{ref}' has no time slots"))

    terr_file = FILES["territories"][0]
    _unique_refs(data["territories"], terr_file, issues)
    terr_names = set()
    for r in data["territories"]:
        line = r["_line"]
        if not r["Name"] or len(r["Name"]) > 80:
            issues.append(Issue(terr_file, line, "Name is required and at most 80 characters"))
        elif r["Name"] in terr_names:
            issues.append(Issue(terr_file, line, f"duplicate Name '{r['Name']}'"))
        terr_names.add(r["Name"])
        if r["operating_hours_ref"] not in hour_refs:
            issues.append(Issue(terr_file, line, f"operating_hours_ref '{r['operating_hours_ref']}' is not defined in {hours_file}"))
        if r["IsActive"] not in ("true", "false"):
            issues.append(Issue(terr_file, line, "IsActive must be true or false"))
        lat, lon = _num(r["Latitude"]), _num(r["Longitude"])
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            issues.append(Issue(terr_file, line, "Latitude must be -90 to 90 and Longitude -180 to 180"))
        if r["Country"] == "United States":
            if not re.fullmatch(r"[A-Z]{2}", r["State"]):
                issues.append(Issue(terr_file, line, "State must be a two letter code for United States"))
            if not re.fullmatch(r"\d{5}", r["PostalCode"]):
                issues.append(Issue(terr_file, line, "PostalCode must be five digits for United States"))

    wt_file = FILES["work_types"][0]
    _unique_refs(data["work_types"], wt_file, issues)
    wt_names = set()
    for r in data["work_types"]:
        line = r["_line"]
        if not r["Name"] or r["Name"] in wt_names:
            issues.append(Issue(wt_file, line, "Name is required and must be unique"))
        wt_names.add(r["Name"])
        duration = _num(r["EstimatedDuration"])
        if r["DurationType"] not in DURATION_TYPES:
            issues.append(Issue(wt_file, line, f"DurationType must be one of {DURATION_TYPES}"))
        elif duration is None or duration <= 0:
            issues.append(Issue(wt_file, line, "EstimatedDuration must be a number above zero"))
        elif duration * (60 if r["DurationType"] == "Hours" else 1) > 1440:
            issues.append(Issue(wt_file, line, "EstimatedDuration is longer than 24 hours"))
    return issues, {k: len(v) for k, v in data.items()}


def _rec(obj: str, ref: str, fields: dict) -> dict:
    return {"attributes": {"type": obj, "referenceId": ref}, **fields}


def _clock(hhmm: str) -> str:
    return f"{hhmm}:00.000Z"


def build_tree(folder: Path) -> dict[str, dict | list]:
    """Return {file name: content} for `sf data import tree --plan plan.json`. Call check() first."""
    data = {k: read(folder, k)[0] for k in FILES}
    hours = [_rec("OperatingHours", f"{r['ref']}", {"Name": r["Name"], "TimeZone": r["TimeZone"]})
             for r in data["operating_hours"]]
    slots = [_rec("TimeSlot", f"TimeSlotRef{n}", {
        "OperatingHoursId": f"@{r['operating_hours_ref']}", "DayOfWeek": r["DayOfWeek"],
        "StartTime": _clock(r["StartTime"]), "EndTime": _clock(r["EndTime"]), "Type": "Normal"})
        for n, r in enumerate(data["time_slots"], start=1)]
    terrs = [_rec("ServiceTerritory", r["ref"], {
        "Name": r["Name"], "OperatingHoursId": f"@{r['operating_hours_ref']}", "IsActive": r["IsActive"] == "true",
        "City": r["City"], "State": r["State"], "Country": r["Country"], "PostalCode": r["PostalCode"],
        "Latitude": float(r["Latitude"]), "Longitude": float(r["Longitude"])})
        for r in data["territories"]]
    types = [_rec("WorkType", r["ref"], {
        "Name": r["Name"], "EstimatedDuration": float(r["EstimatedDuration"]), "DurationType": r["DurationType"]})
        for r in data["work_types"]]
    return {
        "OperatingHours.json": {"records": hours},
        "TimeSlot.json": {"records": slots},
        "ServiceTerritory.json": {"records": terrs},
        "WorkType.json": {"records": types},
        "plan.json": [
            {"sobject": "OperatingHours", "saveRefs": True, "resolveRefs": False, "files": ["OperatingHours.json"]},
            {"sobject": "TimeSlot", "saveRefs": False, "resolveRefs": True, "files": ["TimeSlot.json"]},
            {"sobject": "ServiceTerritory", "saveRefs": False, "resolveRefs": True, "files": ["ServiceTerritory.json"]},
            {"sobject": "WorkType", "saveRefs": False, "resolveRefs": False, "files": ["WorkType.json"]},
        ],
    }


def write_tree(folder: Path, out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in build_tree(folder).items():
        (out / name).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        written.append(name)
    return written
