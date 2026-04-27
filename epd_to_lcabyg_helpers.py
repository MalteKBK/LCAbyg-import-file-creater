import io
import json
import re
import uuid
from datetime import datetime
from typing import Any

import pdfplumber

NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[Ee][+-]?\d+)?")
STAGE_TOKEN_RE = re.compile(r"\b(A1-A3|A4|A5|B1|B2|B3|B4|B5|B6|B7|C1|C2|C3|C4|D)\b", re.IGNORECASE)

STAGE_CODE_MAP = {
    "A1-A3": "A1to3", "A4": "A4", "A5": "A5",
    "B1": "B1", "B2": "B2", "B3": "B3", "B4": "B4", "B5": "B5", "B6": "B6", "B7": "B7",
    "C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4", "D": "D",
}

# Canonical short names used by LCAbyg-like JSON stage payloads.
# The parser stores everything it can recognise; unsupported indicators can be filtered in the Streamlit UI.
INDICATOR_KEYWORDS = [
    ("gwp-total", "GWP"),
    ("gwp fossil", "GWP_fossil"),
    ("gwp-fossil", "GWP_fossil"),
    ("gwp biogenic", "GWP_biogenic"),
    ("gwp-biogenic", "GWP_biogenic"),
    ("gwp luluc", "GWP_luluc"),
    ("gwp-luluc", "GWP_luluc"),
    ("gwp", "GWP"),
    ("odp", "ODP"),
    ("ap", "AP"),
    ("ep freshwater", "EP_freshwater"),
    ("ep-freshwater", "EP_freshwater"),
    ("ep marine", "EP_marine"),
    ("ep-marine", "EP_marine"),
    ("ep terrestrial", "EP_terrestrial"),
    ("ep-terrestrial", "EP_terrestrial"),
    ("ep", "EP"),
    ("pocp", "POCP"),
    ("adpe", "ADPE"),
    ("adpf", "ADPF"),
    ("pere", "PER"),
    ("perm", "PERM"),
    ("pert", "PERT"),
    ("penre", "PENR"),
    ("penrm", "PENRM"),
    ("penrt", "PENRT"),
    ("sm", "SM"),
    ("rsf", "RSF"),
    ("nrsf", "NRSF"),
    ("fw", "FW"),
    ("hwd", "HWD"),
    ("nhwd", "NHWD"),
    ("rwd", "RWD"),
    ("cru", "CRU"),
    ("mfr", "MFR"),
    ("mer", "MER"),
    ("eee", "EEE"),
    ("eet", "EET"),
]

# Keep this conservative for LCAbyg import. Extra indicators are preserved in metadata but not included by default.
LCABYG_SAFE_INDICATORS = {"GWP", "ODP", "AP", "EP", "POCP", "ADPE", "ADPF"}

UNIT_MAP = {
    "kg/m3": "KG", "kg/m³": "KG", "kg/m2": "M2", "kg/m²": "M2",
    "m³": "M3", "m3": "M3", "m2": "M2", "kg": "KG", "l": "L", "liter": "L",
}

METADATA_PATTERNS = {
    "product_name": [
        r"^product name\s*[:\-–]?\s*(.*)$",
        r"^produktnavn\s*[:\-–]?\s*(.*)$",
        r"^declared product\s*[:\-–]?\s*(.*)$",
        r"^this environmental product declaration \(EPD\) covers\s*(.*)$",
        r"^epd\s*for\s*[:\-–]?(.*)$",
    ],
    "producer": [
        r"^manufacturer\s*[:\-–]?\s*(.*)$",
        r"^producer\s*[:\-–]?\s*(.*)$",
        r"^producent\s*[:\-–]?\s*(.*)$",
        r"^owner of the declaration\s*[:\-–]?\s*(.*)$",
        r"^company information\s*[:\-–]?\s*(.*)$",
    ],
    "epd_id": [
        r"^no\.?\s*[:\-–]?\s*([A-Z0-9\-]+)$",
        r"\b(MD-[0-9A-Z\-]+)\b",
        r"^declaration number\s*[:\-–]?\s*([A-Z0-9\-]+)$",
    ],
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def parse_float(value: str) -> float | None:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def safe_iso_date(value: str) -> str | None:
    value = (value or "").replace("/", "-").strip()
    parts = value.split("-")
    if len(parts) != 3:
        return None
    day, month, year = parts
    if len(year) == 2:
        year = "20" + year
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def extract_lines_from_pdf(pdf_bytes: bytes, max_pages: int | None = None) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[: min(max_pages, len(pdf.pages))]
        lines: list[str] = []
        for page in pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            for raw_line in text.splitlines():
                line = normalize_text(raw_line)
                if line:
                    lines.append(line)
        return lines


def find_first_pattern(lines: list[str], patterns: list[str]) -> str | None:
    for line in lines:
        for pattern in patterns:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                value = normalize_text(m.group(1))
                if value:
                    return value
    return None


def find_metadata(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "product_name": None,
        "producer": None,
        "epd_id": None,
        "issue_date": None,
        "valid_to": None,
        "declared_unit": None,
        "density": None,
        "raw_matches": {},
    }

    for i, line in enumerate(lines):
        lower = line.lower()

        if result["product_name"] is None and "declared products" not in lower and any(k in lower for k in ["product name", "produktnavn", "declared product", "epd for", "this environmental product declaration"]):
            result["product_name"] = normalize_text(line.split(":", 1)[1]) if ":" in line else (normalize_text(lines[i + 1]) if i + 1 < len(lines) else None)

        if result["producer"] is None and any(k in lower for k in ["manufacturer", "producer", "producent", "owner of the declaration", "company information"]):
            result["producer"] = normalize_text(line.split(":", 1)[1]) if ":" in line else (normalize_text(lines[i + 1]) if i + 1 < len(lines) else None)

        if result["epd_id"] is None:
            result["epd_id"] = find_first_pattern([line], METADATA_PATTERNS["epd_id"])

        if result["issue_date"] is None and "issued" in lower:
            m = re.search(r"issued\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m:
                result["issue_date"] = safe_iso_date(m.group(1))

        if result["valid_to"] is None and "valid to" in lower:
            m = re.search(r"valid to\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m:
                result["valid_to"] = safe_iso_date(m.group(1))

        if result["declared_unit"] is None and "declared unit" in lower:
            unit_re = r"([0-9.,]+\s*(?:kg/m3|kg/m³|kg/m²|kg/m2|m2|m3|m³|kg|l|liter)\b)"
            m = re.search(r"declared unit\s*[:\-–]?\s*" + unit_re, line, re.IGNORECASE)
            if m:
                result["declared_unit"] = normalize_text(m.group(1))
            else:
                for j in range(i + 1, min(i + 5, len(lines))):
                    m2 = re.search(unit_re, lines[j], re.IGNORECASE)
                    if m2:
                        result["declared_unit"] = normalize_text(m2.group(1))
                        break

        if result["product_name"] is None and "declared products" in lower:
            candidate_lines: list[str] = []
            for j in range(i + 1, min(i + 8, len(lines))):
                next_line = normalize_text(lines[j])
                if next_line.lower().startswith(("epd type", "declared unit", "issued", "valid to", "owner", "programme", "use ")):
                    break
                if not next_line.lower().startswith(("the ", "for ", "construction products", "this ", "compared ")):
                    candidate_lines.append(next_line)
            if candidate_lines:
                result["product_name"] = " ".join(candidate_lines)

        if result["density"] is None and "density" in lower:
            m = re.search(r"density\s*[:\-–]?\s*([0-9.,]+\s*(?:kg/m3|kg/m²|kg/m³|kg/m 3|kg/m2)\b)", line, re.IGNORECASE)
            if m:
                result["density"] = normalize_text(m.group(1))

    for key in ["product_name", "producer", "epd_id"]:
        if result[key] is None:
            result[key] = find_first_pattern(lines, METADATA_PATTERNS[key])

    if result["product_name"] and result["product_name"].lower().startswith("declaration"):
        result["product_name"] = None

    return result


def parse_stage_tokens(text: str) -> list[str]:
    tokens = STAGE_TOKEN_RE.findall(text or "")
    out: list[str] = []
    for token in tokens:
        key = token.upper()
        if key in STAGE_CODE_MAP and STAGE_CODE_MAP[key] not in out:
            out.append(STAGE_CODE_MAP[key])
    return out


def build_indicator_name(label: str) -> str | None:
    normalized = label.lower().strip()
    normalized = re.sub(r"\s*\[.*?\]", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\-]+", " ", normalized).strip()
    for keyword, mapped in INDICATOR_KEYWORDS:
        if keyword in normalized:
            return mapped
    return None


def line_prefix_before_values(line: str, value_count: int) -> str:
    matches = list(NUMBER_RE.finditer(line))
    if not matches:
        return line
    value_matches = matches[-value_count:] if value_count > 0 else matches
    return normalize_text(line[: value_matches[0].start()])


def likely_stage_header(lines: list[str], i: int) -> tuple[int, list[str]] | None:
    window = " ".join(lines[i : min(i + 3, len(lines))])
    codes = parse_stage_tokens(window)
    if "A1to3" in codes and len(codes) >= 2:
        return i, codes
    return None


def parse_stage_table(lines: list[str]) -> dict[str, dict[str, float]]:
    stage_values: dict[str, dict[str, float]] = {}
    header_index = None
    stage_codes: list[str] = []

    for i in range(len(lines)):
        candidate = likely_stage_header(lines, i)
        if candidate:
            header_index, stage_codes = candidate
            break

    if header_index is None or not stage_codes:
        return stage_values

    i = header_index + 1
    merged_lines: list[str] = []
    stop_words = [
        "resource use", "waste categories", "output flows", "life cycle stages", "construction beyond the system",
        "additional environmental", "scenario", "references", "interpretation",
    ]
    while i < len(lines):
        line = lines[i]
        lower = line.lower()
        if any(stop in lower for stop in stop_words):
            break
        if parse_stage_tokens(line) and "a1" in lower and i > header_index + 3:
            break
        if not NUMBER_RE.search(line) and i + 1 < len(lines) and NUMBER_RE.search(lines[i + 1]):
            merged_lines.append(f"{line} {lines[i + 1]}")
            i += 2
            continue
        merged_lines.append(line)
        i += 1

    keywords = [k for k, _ in INDICATOR_KEYWORDS]
    for line in merged_lines:
        lower = line.lower()
        if not any(k.split()[0] in lower for k in keywords):
            continue
        numbers = NUMBER_RE.findall(line)
        if len(numbers) < 1:
            continue
        numbers = numbers[-len(stage_codes):]
        label = line_prefix_before_values(line, len(numbers))
        indicator = build_indicator_name(label)
        if not indicator:
            continue
        for idx, number in enumerate(numbers):
            if idx >= len(stage_codes):
                break
            value = parse_float(number)
            if value is not None:
                stage_values.setdefault(stage_codes[idx], {})[indicator] = value
    return stage_values


def unit_from_declared_unit(declared_unit: str | None) -> str:
    lower = (declared_unit or "").lower()
    for token, mapped in UNIT_MAP.items():
        if token in lower:
            return mapped
    return "KG"


def localized(value: str) -> dict[str, str]:
    return {"English": value, "Danish": value, "German": value, "Norwegian": value}


def empty_comment() -> dict[str, str]:
    return {"English": "", "Danish": "", "German": "", "Norwegian": ""}


def new_id() -> str:
    return str(uuid.uuid4())


def node(type_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"Node": {type_name: payload}}


def edge(type_name: str, from_id: str, to_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"id": new_id(), "excluded_scenarios": [], "enabled": True}
    if extra:
        payload.update(extra)
    return {"Edge": [{type_name: payload}, from_id, to_id]}


def build_stage_nodes(
    product_name: str,
    producer: str | None,
    epd_id: str | None,
    valid_to: str | None,
    declared_unit: str | None,
    stage_values: dict[str, dict[str, float]],
    include_extra_indicators: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    stage_ids: list[str] = []
    stage_unit = unit_from_declared_unit(declared_unit)
    for stage_code, indicators in stage_values.items():
        filtered = indicators if include_extra_indicators else {k: v for k, v in indicators.items() if k in LCABYG_SAFE_INDICATORS}
        if not filtered:
            continue
        sid = new_id()
        stage_ids.append(sid)
        items.append(node("Stage", {
            "id": sid,
            "name": localized(f"{product_name} - {stage_code}"),
            "comment": empty_comment(),
            "source": "User",
            "valid_to": valid_to or "",
            "stage": stage_code,
            "stage_unit": stage_unit,
            "indicator_unit": stage_unit,
            "stage_factor": 1.0,
            "mass_factor": 1.0,
            "indicator_factor": 1.0,
            "scale_factor": 1.0,
            "external_source": producer or "",
            "external_id": epd_id or "",
            "external_version": "",
            "external_url": "",
            "compliance": stage_code,
            "data_type": "Specific",
            "indicators": filtered,
        }))
    return items, stage_ids


def build_lcabyg_project_payload(
    product_name: str,
    producer: str | None,
    epd_id: str | None,
    valid_to: str | None,
    declared_unit: str | None,
    stage_values: dict[str, dict[str, float]],
    include_extra_indicators: bool = False,
) -> list[dict[str, Any]]:
    """Build a fuller LCAbyg-style import model: Building -> BuildingPart -> Construction -> Product -> Stages.

    This is intentionally a minimal project with one dummy building part and quantity 1.0. After import, the user can
    move/copy the product/stages into their real library/project and adjust quantities.
    """
    display = product_name or epd_id or "User EPD product"
    building_id = new_id()
    part_id = new_id()
    construction_id = new_id()
    product_id = new_id()

    payload: list[dict[str, Any]] = [
        node("Building", {
            "id": building_id,
            "name": localized("EPD import project"),
            "comment": empty_comment(),
            "source": "User",
        }),
        node("BuildingPart", {
            "id": part_id,
            "name": localized(f"Imported EPD - {display}"),
            "comment": empty_comment(),
            "source": "User",
            "category": "Other",
        }),
        edge("BuildingToBuildingPart", building_id, part_id, {"amount": 1.0, "unit": unit_from_declared_unit(declared_unit)}),
        node("Construction", {
            "id": construction_id,
            "name": localized(display),
            "comment": empty_comment(),
            "source": "User",
        }),
        edge("BuildingPartToConstruction", part_id, construction_id, {"amount": 1.0, "unit": unit_from_declared_unit(declared_unit)}),
        node("Product", {
            "id": product_id,
            "name": localized(display),
            "source": "User",
            "comment": empty_comment(),
            "uncertainty_factor": 1.0,
        }),
        edge("ConstructionToProduct", construction_id, product_id, {"amount": 1.0, "unit": unit_from_declared_unit(declared_unit)}),
    ]

    stage_nodes, stage_ids = build_stage_nodes(display, producer, epd_id, valid_to, declared_unit, stage_values, include_extra_indicators)
    payload.extend(stage_nodes)
    for sid in stage_ids:
        payload.append(edge("ProductToStage", product_id, sid))
    return payload


def build_lcabyg_library_payload(
    product_name: str,
    producer: str | None,
    epd_id: str | None,
    valid_to: str | None,
    declared_unit: str | None,
    stage_values: dict[str, dict[str, float]],
    include_extra_indicators: bool = False,
) -> list[dict[str, Any]]:
    display = product_name or epd_id or "User EPD product"
    product_id = new_id()
    payload = [node("Product", {
        "id": product_id,
        "name": localized(display),
        "source": "User",
        "comment": empty_comment(),
        "uncertainty_factor": 1.0,
    })]
    stage_nodes, stage_ids = build_stage_nodes(display, producer, epd_id, valid_to, declared_unit, stage_values, include_extra_indicators)
    payload.extend(stage_nodes)
    for sid in stage_ids:
        payload.append(edge("ProductToStage", product_id, sid))
    return payload


def parse_epd_pdf_bytes(pdf_bytes: bytes, max_pages: int | None = None) -> dict[str, Any]:
    lines = extract_lines_from_pdf(pdf_bytes, max_pages=max_pages)
    metadata = find_metadata(lines)
    stage_values = parse_stage_table(lines)
    return {
        "metadata": metadata,
        "stage_values": stage_values,
        "stage_code": next(iter(stage_values), None),
        "stage_indicators": next(iter(stage_values.values()), {}),
        "lines_count": len(lines),
        "lines_preview": lines[:200],
    }


def build_json_text(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
