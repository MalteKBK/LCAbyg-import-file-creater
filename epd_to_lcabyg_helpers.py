import io
import json
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any
from pathlib import Path

import pdfplumber

NUMBER_RE = re.compile(r"[+-]?\d+(?:[.,]\d+)?(?:[Ee][+-]?\d+)?")
STAGE_TOKEN_RE = re.compile(r"\b(A1-A3|A4|C1|C2|C3|C4|D)\b")
INDICATOR_KEYWORDS = {
    "gwp-fossil": "GWP", "gwp-total": "GWP", "gwp": "GWP", "odp": "ODP",
    "ap": "AP", "ep": "EP", "pocp": "POCP", "adpe": "ADPE", "adpf": "ADPF",
    "pere": "PER", "pert": "PER", "penre": "PENR", "penrt": "PENR", "penrm": "PENR",
    "fw": "FW", "sm": "SM", "ser": "SER", "senr": "SENR",
}
STAGE_CODE_MAP = {"A1-A3": "A1to3", "A4": "A4", "C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4", "D": "D"}
UNIT_MAP = {"kg/m3": "M3", "kg/m³": "M3", "kg/m2": "M2", "kg/m²": "M2", "m³": "M3", "m3": "M3", "m2": "M2", "kg": "KG", "l": "L"}
ALL_INDICATORS = ["SER", "EP", "ODP", "POCP", "PER", "ADPE", "AP", "GWP", "ADPF", "PENR", "SENR"]

METADATA_PATTERNS = {
    "product_name": [r"^product name\s*[:\-–]?\s*(.*)$", r"^produktnavn\s*[:\-–]?\s*(.*)$", r"^declared product\s*[:\-–]?\s*(.*)$", r"^epd\s*for\s*[:\-–]?(.*)$"],
    "producer": [r"^manufacturer\s*[:\-–]?\s*(.*)$", r"^producer\s*[:\-–]?\s*(.*)$", r"^producent\s*[:\-–]?\s*(.*)$", r"^owner of the declaration\s*[:\-–]?\s*(.*)$"],
    "epd_id": [r"^no\.?\s*[:\-–]?\s*([A-Z0-9\-]+)$", r"^(md-[0-9A-Z\-]+)\b", r"^declaration number\s*[:\-–]?\s*([A-Z0-9\-]+)$"],
}

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()

def parse_float(value: str) -> float | None:
    try:
        return float((value or "").replace(" ", "").replace(",", "."))
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

def extract_lines_from_pdf(pdf_bytes: bytes, max_pages: int | None = 20) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:min(max_pages, len(pdf.pages))]
        lines = []
        for page in pages:
            for raw_line in (page.extract_text() or "").splitlines():
                line = normalize_text(raw_line)
                if line:
                    lines.append(line)
        return lines

def find_first_pattern(lines: list[str], patterns: list[str]) -> str | None:
    for line in lines:
        for pattern in patterns:
            m = re.match(pattern, normalize_text(line), re.IGNORECASE)
            if m and normalize_text(m.group(1)):
                return normalize_text(m.group(1))
    return None

def _looks_like_bad_producer(value: str | None) -> bool:
    if not value:
        return True
    lower = value.lower()
    bad = ["use the", "smaller inputs", "the ", "this ", "declared", "valid", "issued", "programme", "construction products"]
    return len(value) > 80 or any(lower.startswith(x) for x in bad)

def find_metadata(lines: list[str]) -> dict[str, Any]:
    result = {"product_name": None, "producer": None, "epd_id": None, "issue_date": None, "valid_to": None, "declared_unit": None, "density": None}
    for i, line in enumerate(lines):
        lower = line.lower()
        if result["product_name"] is None and "declared products" not in lower and any(k in lower for k in ["product name", "produktnavn", "declared product", "epd for"]):
            result["product_name"] = normalize_text(line.split(":", 1)[1]) if ":" in line else (normalize_text(lines[i + 1]) if i + 1 < len(lines) else None)
        if result["producer"] is None and any(k in lower for k in ["manufacturer", "producer", "producent", "owner of the declaration"]):
            candidate = normalize_text(line.split(":", 1)[1]) if ":" in line else (normalize_text(lines[i + 1]) if i + 1 < len(lines) else None)
            if not _looks_like_bad_producer(candidate):
                result["producer"] = candidate
        if result["epd_id"] is None:
            for pattern in METADATA_PATTERNS["epd_id"]:
                m = re.match(pattern, line, re.IGNORECASE)
                if m:
                    result["epd_id"] = normalize_text(m.group(1)).upper()
                    break
        if result["issue_date"] is None and "issued" in lower:
            m = re.search(r"issued\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m: result["issue_date"] = safe_iso_date(m.group(1))
        if result["valid_to"] is None and "valid to" in lower:
            m = re.search(r"valid to\s*[:\-–]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", line, re.IGNORECASE)
            if m: result["valid_to"] = safe_iso_date(m.group(1))
        if result["declared_unit"] is None and "declared unit" in lower:
            pat = r"declared unit\s*[:\-–]?\s*([0-9.,]+\s*(?:kg/m3|kg/m³|kg/m2|kg/m²|m2|m3|m³|kg|l|liter)\b)"
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                result["declared_unit"] = normalize_text(m.group(1))
            else:
                for j in range(i + 1, min(i + 4, len(lines))):
                    maybe = normalize_text(lines[j])
                    if re.match(r"^[0-9.,]+\s*(kg/m3|kg/m³|kg/m2|kg/m²|m2|m3|m³|kg|l|liter)\b", maybe, re.IGNORECASE):
                        result["declared_unit"] = maybe; break
        if result["product_name"] is None and "declared products" in lower:
            cand = []
            for j in range(i + 1, min(i + 8, len(lines))):
                nxt = normalize_text(lines[j])
                if nxt.lower().startswith(("epd type", "declared unit", "issued", "valid to", "owner", "programme", "use ")): break
                if not nxt.lower().startswith(("the ", "for ", "construction products", "this ", "compared ")): cand.append(nxt)
            if cand: result["product_name"] = " ".join(cand)
        if result["density"] is None and "density" in lower:
            m = re.search(r"density\s*[:\-–]?\s*([0-9.,]+\s*(?:kg/m3|kg/m²|kg/m³|kg/m 3|kg/m2)\b)", line, re.IGNORECASE)
            if m: result["density"] = normalize_text(m.group(1))
    for key in ["product_name", "producer", "epd_id"]:
        if result[key] is None:
            result[key] = find_first_pattern(lines, METADATA_PATTERNS[key])
    if _looks_like_bad_producer(result["producer"]):
        result["producer"] = None
    return result

def parse_stage_tokens(header_line: str) -> list[str]:
    return [STAGE_CODE_MAP[t] for t in STAGE_TOKEN_RE.findall(header_line) if t in STAGE_CODE_MAP]

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
    return normalize_text(line[:match.start()]) if match else line

def parse_stage_table(lines: list[str]) -> dict[str, dict[str, float]]:
    stage_values: dict[str, dict[str, float]] = {}
    header_index = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if "a1-a3" in lower and ("unit" in lower or any(tok in lower for tok in ["a4", "c1", "c2", "c3", "c4", "d"])):
            header_index = i; break
    if header_index is None:
        return stage_values
    stage_codes = parse_stage_tokens(lines[header_index])
    if not stage_codes:
        # Try combining header with previous/next line
        for combo in [lines[header_index] + " " + lines[min(header_index+1, len(lines)-1)], lines[max(header_index-1,0)] + " " + lines[header_index]]:
            stage_codes = parse_stage_tokens(combo)
            if stage_codes: break
    if not stage_codes:
        return stage_values
    merged_lines = []
    i = header_index + 1
    while i < len(lines):
        line = lines[i]; lower = line.lower()
        if any(stop in lower for stop in ["resource use", "waste categories", "life cycle stages", "construction beyond", "additional environmental"]):
            break
        if not NUMBER_RE.search(line) and i + 1 < len(lines) and NUMBER_RE.search(lines[i + 1]):
            merged_lines.append(f"{line} {lines[i + 1]}"); i += 2; continue
        merged_lines.append(line); i += 1
    for line in merged_lines:
        lower = line.lower()
        if not any(k in lower for k in INDICATOR_KEYWORDS):
            continue
        nums = NUMBER_RE.findall(line)[-len(stage_codes):]
        indicator = build_indicator_name(line_prefix_before_number(line))
        if not indicator:
            continue
        for idx, stage in enumerate(stage_codes):
            if idx < len(nums):
                val = parse_float(nums[idx])
                if val is not None:
                    stage_values.setdefault(stage, {})[indicator] = val
    return stage_values

def unit_from_declared_unit(declared_unit: str | None) -> str:
    lower = (declared_unit or "").lower()
    for token, mapped in UNIT_MAP.items():
        if token in lower:
            return mapped
    return "KG"

def mass_factor_from_density(density: str | None) -> float:
    if not density:
        return 1.0
    m = NUMBER_RE.search(density)
    val = parse_float(m.group(0)) if m else None
    return float(val) if val and val > 0 else 1.0

def local_name(text: str) -> dict[str, str]:
    return {"English": text, "Danish": text, "German": text, "Norwegian": text}

def comment(extra: str = "") -> dict[str, str]:
    return {"English": extra, "Danish": extra, "German": extra, "Norwegian": extra}

def node(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"Node": {kind: data}}

def edge(kind: str, payload: Any, src: str, dst: str) -> dict[str, Any]:
    return {"Edge": [{kind: payload}, src, dst]}

def complete_indicators(values: dict[str, float]) -> dict[str, float]:
    return {k: float(values.get(k, 0.0)) for k in ALL_INDICATORS}

def parse_epd_pdf_bytes(pdf_bytes: bytes) -> dict[str, Any]:
    lines = extract_lines_from_pdf(pdf_bytes)
    metadata = find_metadata(lines)
    stage_values = parse_stage_table(lines)
    return {"metadata": metadata, "stage_values": stage_values, "lines_count": len(lines)}


TEMPLATE_DIR = Path(__file__).resolve().parent / "template_lcabyg"

def _load_template_files() -> dict[str, list[dict[str, Any]]]:
    files: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            files[path.name] = json.load(f)
    return files

def _set_localized_name(obj: dict[str, Any], text: str) -> None:
    if "name" in obj:
        current = obj.get("name")
        if isinstance(current, dict):
            for lang in list(current.keys()):
                current[lang] = text
        else:
            obj["name"] = local_name(text)

def _walk_nodes(files: dict[str, list[dict[str, Any]]], kind: str):
    for filename, entries in files.items():
        for entry in entries:
            node_obj = entry.get("Node", {}) if isinstance(entry, dict) else {}
            if kind in node_obj:
                yield filename, node_obj[kind]

def build_lcabyg_import_files(parsed: dict[str, Any], project_name: str = "EPD import project", amount: float = 1.0, lifespan: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Build import files by modifying the known-working LCAbyg 5.3.1 template.

    This intentionally preserves all template UUIDs and reference edges. Earlier dynamic
    generation created fresh UUIDs and occasionally missed reference nodes that LCAbyg
    expects. The working test package imported successfully, so this function only swaps
    the product/stage payload values inside that exact graph.
    """
    md = parsed.get("metadata", {})
    stages = parsed.get("stage_values", {}) or {}
    a1to3 = stages.get("A1to3", {})

    product_name = md.get("product_name") or md.get("epd_id") or "Imported EPD product"
    producer = md.get("producer") or ""
    epd_id = md.get("epd_id") or ""
    unit = unit_from_declared_unit(md.get("declared_unit"))
    mass_factor = mass_factor_from_density(md.get("density")) if unit == "M3" else 1.0

    files = _load_template_files()

    # Project metadata
    for _, project in _walk_nodes(files, "Project"):
        project["name"] = {"Danish": project_name}
        project["building_regulation_version"] = "BR2023"

    # Keep building as a harmless minimal project, but update timespan.
    for _, building in _walk_nodes(files, "Building"):
        building["calculation_timespan"] = lifespan
        building["initial_year"] = datetime.now().year

    # Product, construction and element naming.
    for _, product in _walk_nodes(files, "Product"):
        _set_localized_name(product, product_name)
        product["source"] = "User"
        product["comment"] = comment(f"Generated from EPD PDF. Producer: {producer}; EPD id: {epd_id}")
        product["uncertainty_factor"] = 1.0

    for _, construction in _walk_nodes(files, "Construction"):
        _set_localized_name(construction, product_name)
        construction["unit"] = unit
        construction["source"] = "User"
        construction["comment"] = comment(f"EPD id: {epd_id}; declared unit: {md.get('declared_unit') or ''}")
        construction["locked"] = False

    for _, element in _walk_nodes(files, "Element"):
        _set_localized_name(element, "Imported EPD element")
        element["source"] = "User"

    # Update construction/product edge amount and unit without changing IDs or endpoints.
    for entry in files.get("Construction.json", []):
        edge_obj = entry.get("Edge") if isinstance(entry, dict) else None
        if edge_obj and isinstance(edge_obj[0], dict) and "ConstructionToProduct" in edge_obj[0]:
            payload = edge_obj[0]["ConstructionToProduct"]
            if isinstance(payload, dict):
                payload["amount"] = amount
                payload["unit"] = unit
                payload["lifespan"] = lifespan
                payload["enabled"] = True

    # Update exactly the one working stage from the template. Do not add C2/C4/D.
    for _, stage in _walk_nodes(files, "Stage"):
        _set_localized_name(stage, f"{product_name} - A1to3")
        stage["comment"] = comment("Extracted automatically from EPD PDF. Check values before use.")
        stage["source"] = "User"
        stage["valid_to"] = md.get("valid_to") or ""
        stage["stage"] = "A1to3"
        stage["stage_unit"] = unit
        stage["indicator_unit"] = unit
        stage["stage_factor"] = 1.0
        stage["mass_factor"] = mass_factor
        stage["indicator_factor"] = 1.0
        stage["scale_factor"] = 1.0
        stage["external_source"] = producer
        stage["external_id"] = epd_id
        stage["external_version"] = ""
        stage["external_url"] = ""
        stage["compliance"] = "A1"
        stage["data_type"] = "Specific"
        stage["indicators"] = complete_indicators(a1to3)

    return files

def build_import_zip_bytes(files: dict[str, list[dict[str, Any]]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, data in sorted(files.items()):
            zf.writestr(filename, json.dumps(data, indent=2, ensure_ascii=False))
    return out.getvalue()
