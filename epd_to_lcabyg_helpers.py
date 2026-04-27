import io
import re
import uuid
from datetime import datetime
from typing import Any

import pdfplumber

NUMBER_RE = re.compile(r"[+-]?\d+(?:[.,]\d+)?(?:[Ee][+-]?\d+)?")
STAGE_TOKEN_RE = re.compile(r"\b(A1-A3|A4|C1|C2|C3|C4|D)\b")
INDICATOR_KEYWORDS = {
    "gwp-fossil": "GWP",
    "gwp": "GWP",
    "odp": "ODP",
    "ap": "AP",
    "ep": "EP",
    "pocp": "POCP",
    "adpe": "ADPE",
    "adpf": "ADPF",
    "pere": "PER",
    "penre": "PENR",
    "penrm": "PENRM",
    "penrt": "PENR",
    "fw": "FW",
    "sm": "SM",
}
STAGE_CODE_MAP = {
    "A1-A3": "A1to3",
    "A4": "A4",
    "C1": "C1",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
    "D": "D",
}
UNIT_MAP = {
    "kg": "KG",
    "m2": "M2",
    "m³": "M3",
    "m3": "M3",
    "l": "L",
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
        r"^(md-[0-9A-Z\-]+)\b",
        r"^declaration number\s*[:\-–]?\s*([A-Z0-9\-]+)$",
    ],
    "issue_date": [
        r"^issued\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})$",
    ],
    "valid_to": [
        r"^valid to\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})$",
    ],
}


def normalize_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_float(value: str) -> float | None:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def safe_iso_date(value: str) -> str | None:
    if not value:
        return None
    value = value.replace("/", "-").strip()
    parts = value.split("-")
    if len(parts) != 3:
        return None
    day, month, year = parts
    if len(year) == 2:
        year = "20" + year
    try:
        dt = datetime(int(year), int(month), int(day))
        return dt.date().isoformat()
    except ValueError:
        return None


def extract_lines_from_pdf(pdf_bytes: bytes, max_pages: int = 15) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        lines: list[str] = []
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                normalized = normalize_text(raw_line)
                if normalized:
                    lines.append(normalized)
    return lines


def find_first_pattern(lines: list[str], patterns: list[str]) -> str | None:
    for line in lines:
        normalized = normalize_text(line)
        for pattern in patterns:
            m = re.match(pattern, normalized, re.IGNORECASE)
            if m:
                value = normalize_text(m.group(1))
                if value:
                    return value
    return None


def find_metadata(lines: list[str]) -> dict[str, Any]:
    result = {
        "product_name": None,
        "producer": None,
        "epd_id": None,
        "issue_date": None,
        "valid_to": None,
        "declared_unit": None,
        "density": None,
    }

    for i, line in enumerate(lines):
        lower = line.lower()
        if result["product_name"] is None and "declared products" not in lower and any(k in lower for k in ["product name", "produktnavn", "declared product", "epd for", "this environmental product declaration"]):
            if ":" in line:
                result["product_name"] = normalize_text(line.split(":", 1)[1])
            elif i + 1 < len(lines):
                candidate = normalize_text(lines[i + 1])
                if candidate:
                    result["product_name"] = candidate

        if result["producer"] is None and any(k in lower for k in ["manufacturer", "producer", "producent", "owner of the declaration", "company information"]):
            if ":" in line:
                result["producer"] = normalize_text(line.split(":", 1)[1])
            elif i + 1 < len(lines):
                result["producer"] = normalize_text(lines[i + 1])

        if result["epd_id"] is None:
            for pattern in METADATA_PATTERNS["epd_id"]:
                m = re.match(pattern, line, re.IGNORECASE)
                if m:
                    result["epd_id"] = normalize_text(m.group(1))
                    break

        if result["issue_date"] is None and "issued" in lower:
            m = re.search(r"issued\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m:
                result["issue_date"] = safe_iso_date(m.group(1))

        if result["valid_to"] is None and "valid to" in lower:
            m = re.search(r"valid to\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m:
                result["valid_to"] = safe_iso_date(m.group(1))

        if result["declared_unit"] is None and "declared unit" in lower:
            m = re.search(r"declared unit\s*[:\-–]?\s*([0-9.,]+\s*(?:kg|m2|m3|m³|l|liter|kg/m3|kg/m²|kg/m3)\b)", line, re.IGNORECASE)
            if m:
                result["declared_unit"] = normalize_text(m.group(1))
            else:
                for j in range(i + 1, min(i + 4, len(lines))):
                    maybe = normalize_text(lines[j])
                    if re.match(r"^[0-9.,]+\s*(kg|m2|m3|m³|l|liter|kg/m3|kg/m²|kg/m3)\b", maybe, re.IGNORECASE):
                        result["declared_unit"] = maybe
                        break

        if result["producer"] is None:
            owner_match = re.match(r"^(owner|owner of declaration)\s*[:\-–]?\s*(.*)$", line, re.IGNORECASE)
            if owner_match:
                captured = normalize_text(owner_match.group(2))
                if captured:
                    result["producer"] = captured

        if result["epd_id"] is None:
            no_match = re.match(r"^no\.?\s*[:\-–]?\s*([A-Z0-9\-]+)$", line, re.IGNORECASE)
            if no_match:
                result["epd_id"] = normalize_text(no_match.group(1))

        if result["product_name"] is None and "declared products" in lower:
            candidate_lines: list[str] = []
            for j in range(i + 1, min(i + 8, len(lines))):
                next_line = normalize_text(lines[j])
                if not next_line:
                    continue
                if next_line.lower().startswith((
                    "epd type",
                    "declared unit",
                    "issued",
                    "valid to",
                    "owner",
                    "programme",
                    "programme operator",
                    "use ",
                )):
                    break
                if next_line.lower().startswith((
                    "the ",
                    "for ",
                    "construction products",
                    "this ",
                    "compared ",
                )):
                    continue
                candidate_lines.append(next_line)
            if candidate_lines:
                result["product_name"] = " ".join(candidate_lines)

        if result["density"] is None and "density" in lower:
            m = re.search(r"density\s*[:\-–]?\s*([0-9.,]+\s*(?:kg/m3|kg/m²|kg/m³|kg/m 3|kg/m2)\b)", line, re.IGNORECASE)
            if m:
                result["density"] = normalize_text(m.group(1))

    if result["product_name"] and result["product_name"].lower().startswith("declaration"):
        result["product_name"] = None

    if result["product_name"] is None:
        result["product_name"] = find_first_pattern(lines, METADATA_PATTERNS["product_name"]) or None
    if result["producer"] is None:
        result["producer"] = find_first_pattern(lines, METADATA_PATTERNS["producer"]) or None
    if result["epd_id"] is None:
        result["epd_id"] = find_first_pattern(lines, METADATA_PATTERNS["epd_id"]) or None

    return result


def parse_stage_tokens(header_line: str) -> list[str]:
    tokens = STAGE_TOKEN_RE.findall(header_line)
    return [STAGE_CODE_MAP[token] for token in tokens if token in STAGE_CODE_MAP]


def build_indicator_name(label: str) -> str | None:
    normalized = label.lower().strip()
    normalized = re.sub(r"\s*\[.*\]$", "", normalized)
    normalized = re.sub(r"[^a-z0-9\-]+", " ", normalized).strip()
    for keyword, mapped in INDICATOR_KEYWORDS.items():
        if keyword in normalized:
            return mapped
    return None


def line_prefix_before_number(line: str) -> str:
    match = NUMBER_RE.search(line)
    if not match:
        return line
    return normalize_text(line[: match.start()])


def parse_stage_table(lines: list[str]) -> dict[str, dict[str, float]]:
    stage_values: dict[str, dict[str, float]] = {}
    header_index = None
    for i, line in enumerate(lines):
        if "a1-a3" in line.lower() and "unit" in line.lower():
            header_index = i
            break
    if header_index is None:
        for i, line in enumerate(lines):
            if "a1-a3" in line.lower() and re.search(r"\d", line):
                header_index = i
                break
    if header_index is None:
        return stage_values

    stage_codes = parse_stage_tokens(lines[header_index])
    if not stage_codes:
        return stage_values

    merged_lines: list[str] = []
    i = header_index + 1
    while i < len(lines):
        line = lines[i]
        lower = line.lower()
        if not lower or any(stop in lower for stop in ["resource use per kg", "waste categories", "waste categories and output", "life cycle stages", "construction beyond the system"]):
            break

        if not NUMBER_RE.search(line) and i + 1 < len(lines) and NUMBER_RE.search(lines[i + 1]):
            merged_lines.append(f"{line} {lines[i + 1]}")
            i += 2
            continue

        merged_lines.append(line)
        i += 1

    for line in merged_lines:
        lower = line.lower()
        if not any(keyword in lower for keyword in ["gwp", "ap", "ep", "pocp", "adpe", "adpf", "pere", "penre", "penrm", "penrt", "odp", "fw", "sm"]):
            continue

        numbers = NUMBER_RE.findall(line)
        if not numbers:
            continue
        label = line_prefix_before_number(line)
        indicator = build_indicator_name(label)
        if not indicator:
            continue

        for index, stage in enumerate(stage_codes):
            if index >= len(numbers):
                break
            value = parse_float(numbers[index])
            if value is None:
                continue
            stage_values.setdefault(stage, {})[indicator] = value
    return stage_values


def choose_best_stage(stage_values: dict[str, dict[str, float]]) -> tuple[str, dict[str, float]] | tuple[None, None]:
    for preferred in ["A1to3", "A4", "C2", "C3", "C4", "D"]:
        values = stage_values.get(preferred)
        if values:
            return preferred, values
    if stage_values:
        first = next(iter(stage_values.items()))
        return first[0], first[1]
    return None, None


def unit_from_declared_unit(declared_unit: str | None) -> str:
    if not declared_unit:
        return "KG"
    lower = declared_unit.lower()
    for token, mapped in UNIT_MAP.items():
        if token in lower:
            return mapped
    return "KG"


def build_lcabyg_payload(
    product_name: str,
    producer: str | None,
    epd_id: str | None,
    valid_to: str | None,
    declared_unit: str | None,
    stage_code: str,
    indicators: dict[str, float],
) -> list[dict[str, Any]]:
    product_id = str(uuid.uuid4())
    stage_id = str(uuid.uuid4())
    edge_id = str(uuid.uuid4())

    display_name = product_name or (epd_id or "User EPD")
    localized_name = {
        "English": display_name,
        "Danish": display_name,
        "German": display_name,
        "Norwegian": display_name,
    }
    comment = {"English": "", "Danish": "", "German": "", "Norwegian": ""}

    product_node = {
        "Node": {
            "Product": {
                "id": product_id,
                "name": localized_name,
                "source": "User",
                "comment": comment,
                "uncertainty_factor": 1.0,
            }
        }
    }

    stage_node = {
        "Node": {
            "Stage": {
                "id": stage_id,
                "name": localized_name,
                "comment": comment,
                "source": "User",
                "valid_to": valid_to or "",
                "stage": stage_code,
                "stage_unit": unit_from_declared_unit(declared_unit),
                "indicator_unit": unit_from_declared_unit(declared_unit),
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
                "indicators": indicators,
            }
        }
    }

    edge = {
        "Edge": [
            {
                "ProductToStage": {
                    "id": edge_id,
                    "excluded_scenarios": [],
                    "enabled": True,
                }
            },
            product_id,
            stage_id,
        ]
    }

    return [product_node, edge, stage_node]


def parse_epd_pdf_bytes(pdf_bytes: bytes) -> dict[str, Any]:
    lines = extract_lines_from_pdf(pdf_bytes)
    metadata = find_metadata(lines)
    stage_values = parse_stage_table(lines)
    stage_code, indicators = choose_best_stage(stage_values)
    return {
        "metadata": metadata,
        "stage_code": stage_code,
        "stage_indicators": indicators or {},
        "lines_count": len(lines),
    }


def build_json_text(payload: list[dict[str, Any]]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)
