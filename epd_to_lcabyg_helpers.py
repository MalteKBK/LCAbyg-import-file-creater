import io
import json
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any

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

def build_lcabyg_import_files(parsed: dict[str, Any], project_name: str = "EPD import project", amount: float = 1.0, lifespan: int = 50) -> dict[str, list[dict[str, Any]]]:
    md = parsed.get("metadata", {})
    stages = parsed.get("stage_values", {}) or {}
    product_name = md.get("product_name") or md.get("epd_id") or "Imported EPD product"
    producer = md.get("producer") or ""
    epd_id = md.get("epd_id") or ""
    unit = unit_from_declared_unit(md.get("declared_unit"))
    mass_factor = mass_factor_from_density(md.get("density")) if unit == "M3" else 1.0

    ids = {k: str(uuid.uuid4()) for k in ["project", "building", "root", "model", "element", "construction", "product", "operation", "dgnb", "transport_root", "process", "installation", "fuel"]}
    category_id = "59ab59a5-2482-45ae-85f1-d0e39e640712"  # same generic category as official guide example
    stage_category_id = "d8143098-5ed7-42db-8bea-996b0cb3d271"

    files: dict[str, list[dict[str, Any]]] = {}
    files["Project.json"] = [
        node("Project", {"id": ids["project"], "name": {"Danish": project_name}, "address": "", "owner": "", "lca_advisor": "", "building_regulation_version": "BR2023"}),
        edge("MainBuilding", str(uuid.uuid4()), ids["project"], ids["building"]),
    ]
    files["Building.json"] = [
        node("Building", {"id": ids["building"], "scenario_name": "Original bygningsmodel", "locked": "Unlocked", "description": comment(), "building_type": "Office", "heated_floor_area": 1.0, "gross_area": 1.0, "integrated_garage": 0.0, "external_area": 0.0, "gross_area_above_ground": 0.0, "person_count": 0, "storeys_above_ground": 0, "storeys_below_ground": 0, "storey_height": 0.0, "initial_year": datetime.now().year, "calculation_timespan": lifespan, "calculation_mode": "BR23", "outside_area": 0.0, "plot_area": 0.0, "energy_class": "LowEnergy"}),
        edge("BuildingToRoot", str(uuid.uuid4()), ids["building"], ids["root"]),
        edge("BuildingToOperation", str(uuid.uuid4()), ids["building"], ids["operation"]),
        edge("BuildingToDGNBOperation", str(uuid.uuid4()), ids["building"], ids["dgnb"]),
    ]
    files["EmbodiedRoot.json"] = [node("EmbodiedRoot", {"id": ids["root"]}), edge("RootToModel", str(uuid.uuid4()), ids["root"], ids["model"]), edge("RootToConstructionProcess", str(uuid.uuid4()), ids["root"], ids["process"])]
    files["ElementModel.json"] = [node("ElementModel", {"id": ids["model"]}), edge("ParentTo", str(uuid.uuid4()), ids["model"], ids["element"])]
    files["Element.json"] = [node("Element", {"id": ids["element"], "name": local_name("Imported EPD element"), "source": "User", "comment": comment(), "enabled": True, "excluded_scenarios": []}), edge("ElementToConstruction", {"id": str(uuid.uuid4()), "amount": amount, "enabled": True, "special_conditions": False, "excluded_scenarios": []}, ids["element"], ids["construction"])]
    files["Construction.json"] = [node("Construction", {"id": ids["construction"], "name": local_name(product_name), "unit": unit, "source": "User", "comment": comment(f"EPD id: {epd_id}; declared unit: {md.get('declared_unit') or ''}"), "locked": False}), edge("ConstructionToProduct", {"id": str(uuid.uuid4()), "amount": amount, "unit": unit, "lifespan": lifespan, "demolition": False, "delayed_start": 0, "enabled": True, "excluded_scenarios": []}, ids["construction"], ids["product"])]
    product_entries = [node("Product", {"id": ids["product"], "name": local_name(product_name), "source": "User", "comment": comment(f"Generated from EPD PDF. Producer: {producer}; EPD id: {epd_id}"), "uncertainty_factor": 1.0})]
    stage_entries = []
    cat_stage_entries = []
    for stage_code, vals in stages.items():
        sid = str(uuid.uuid4())
        product_entries.append(edge("ProductToStage", {"id": str(uuid.uuid4()), "excluded_scenarios": [], "enabled": True}, ids["product"], sid))
        stage_entries.append(node("Stage", {"id": sid, "name": local_name(f"{product_name} - {stage_code}"), "comment": comment(f"Extracted automatically from PDF. Check values before use. Declared unit: {md.get('declared_unit') or ''}"), "source": "User", "valid_to": md.get("valid_to") or "", "stage": stage_code, "stage_unit": unit, "indicator_unit": unit, "stage_factor": 1.0, "mass_factor": mass_factor, "indicator_factor": 1.0, "scale_factor": 1.0, "external_source": producer, "external_id": epd_id, "external_version": "", "external_url": "", "compliance": stage_code, "data_type": "Specific", "indicators": complete_indicators(vals)}))
        cat_stage_entries.append(edge("CategoryToStage", str(uuid.uuid4()), stage_category_id, sid))
    if not stage_entries:
        sid = str(uuid.uuid4())
        product_entries.append(edge("ProductToStage", {"id": str(uuid.uuid4()), "excluded_scenarios": [], "enabled": True}, ids["product"], sid))
        stage_entries.append(node("Stage", {"id": sid, "name": local_name(f"{product_name} - A1to3"), "comment": comment("No indicators were extracted; placeholder only."), "source": "User", "valid_to": md.get("valid_to") or "", "stage": "A1to3", "stage_unit": unit, "indicator_unit": unit, "stage_factor": 1.0, "mass_factor": mass_factor, "indicator_factor": 1.0, "scale_factor": 1.0, "external_source": producer, "external_id": epd_id, "external_version": "", "external_url": "", "compliance": "A1to3", "data_type": "Specific", "indicators": complete_indicators({})}))
        cat_stage_entries.append(edge("CategoryToStage", str(uuid.uuid4()), stage_category_id, sid))
    files["Product.json"] = product_entries
    files["Stage.json"] = stage_entries
    files["CategoryToStage.json"] = cat_stage_entries
    files["CategoryToElement.json"] = [edge("CategoryToElement", {"id": str(uuid.uuid4())}, category_id, ids["element"])]
    files["CategoryToConstruction.json"] = [edge("CategoryToConstruction", {"id": str(uuid.uuid4()), "layers": [1]}, category_id, ids["construction"])]
    files["Operation.json"] = [node("Operation", {"id": ids["operation"], "electricity_usage": 0.0, "heat_usage": 0.0, "electricity_production": 0.0})]
    files["DGNBOperationReference.json"] = [node("DGNBOperationReference", {"id": ids["dgnb"], "heat_supplement": 0.0, "electricity_supplement": 0.0})]
    files["ProductTransportRoot.json"] = [node("ProductTransportRoot", {"id": ids["transport_root"]})]
    files["ConstructionProcess.json"] = [node("ConstructionProcess", {"id": ids["process"]}), edge("ProcessToInstallation", str(uuid.uuid4()), ids["process"], ids["installation"]), edge("ProcessToTransport", str(uuid.uuid4()), ids["process"], ids["transport_root"])]
    files["ConstructionInstallation.json"] = [node("ConstructionInstallation", {"id": ids["installation"]}), edge("FuelUsage", str(uuid.uuid4()), ids["installation"], ids["fuel"])]
    files["FuelConsumption.json"] = [node("FuelConsumption", {"id": ids["fuel"]})]
    return files

def build_import_zip_bytes(files: dict[str, list[dict[str, Any]]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, data in sorted(files.items()):
            zf.writestr(filename, json.dumps(data, indent=2, ensure_ascii=False))
    return out.getvalue()
