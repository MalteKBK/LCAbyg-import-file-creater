import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber

NUMBER_RE = re.compile(r"[+-]?\d+(?:[.,]\d+)?(?:[Ee][+-]?\d+)?")
SCI_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?(?:[Ee][+-]?\d+)?$")
STAGE_TOKEN_RE = re.compile(r"\b(A1\s*[-–]\s*A3|A1\s*[-–]\s*3|A1|A2|A3|A4|A5|B1(?:\s*[-–]\s*B7)?|B2\s*[-–]\s*B7|C1|C2|C3|C4|D)\b", re.I)

STAGE_CODE_MAP = {
    "A1-A3": "A1to3", "A1-3": "A1to3", "A1": "A1", "A2": "A2", "A3": "A3",
    "A4": "A4", "A5": "A5", "B1": "B1", "B1-B7": "B1to7", "B2-B7": "B2to7",
    "C1": "C1", "C2": "C2", "C3": "C3", "C4": "C4", "D": "D",
}
UNIT_MAP = {"kg/m3": "M3", "kg/m³": "M3", "kg/m2": "M2", "kg/m²": "M2", "m³": "M3", "m3": "M3", "m2": "M2", "kg": "KG", "l": "L"}
ALL_INDICATORS = ["SER", "EP", "ODP", "POCP", "PER", "ADPE", "AP", "GWP", "ADPF", "PENR", "SENR"]

METADATA_PATTERNS = {
    "product_name": [
        r"^product\s*name\s*[:\-–]?\s*(.*)$", r"^produktnavn\s*[:\-–]?\s*(.*)$",
        r"^product\s*[:\-–]?\s*(.*)$", r"^declared product\s*[:\-–]?\s*(.*)$",
        r"^deklareret produkt\s*[:\-–]?\s*(.*)$", r"^epd\s*for\s*[:\-–]?(.*)$",
    ],
    "producer": [
        r"^manufacturer\s*[:\-–]?\s*(.*)$", r"^producer\s*[:\-–]?\s*(.*)$", r"^producent\s*[:\-–]?\s*(.*)$",
        r"^owner of the declaration\s*[:\-–]?\s*(.*)$", r"^ejer\s*[:\-–]?\s*(.*)$",
        r"^deklarationens ejer\s*[:\-–]?\s*(.*)$",
    ],
    "epd_id": [
        r"^no\.?\s*[:\-–]?\s*([A-Z0-9\-_.]+)$", r"^nr\.?\s*[:\-–]?\s*([A-Z0-9\-_.]+)$",
        r"^(md-[0-9A-Z\-_.]+)\b", r"^declaration number\s*[:\-–]?\s*([A-Z0-9\-_.]+)$",
        r"^registration number\s*[:\-–]?\s*([A-Z0-9\-_.]+)$", r"^epd hub,\s*([A-Z0-9\-_.]+)$",
    ],
}


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\xa0", " ").replace("\u00ad", "").replace("\ufffe", "-")
    return re.sub(r"\s+", " ", text).strip()


def norm_key(value: Any) -> str:
    s = normalize_text(value).lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9+\-/ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    s = normalize_text(value)
    if not s:
        return None
    s = s.replace(" ", "").replace("\u00a0", "")
    # Handles both Danish decimal comma and ordinary decimal dot.
    if "," in s and "." in s:
        # Assume last punctuation is decimal separator.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def safe_iso_date(value: str) -> str | None:
    value = (value or "").replace("/", "-").replace(".", "-").strip()
    m = re.search(r"(\d{1,2})[-](\d{1,2})[-](\d{2,4})", value)
    if not m:
        return None
    day, month, year = m.groups()
    if len(year) == 2:
        year = "20" + year
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def _looks_like_bad_producer(value: str | None) -> bool:
    if not value:
        return True
    lower = value.lower().strip()
    bad_prefixes = ["use the", "smaller inputs", "the ", "this ", "declared", "valid", "issued", "programme", "construction products", "basis of"]
    return len(value) > 90 or any(lower.startswith(x) for x in bad_prefixes)


def extract_lines_from_pdf(pdf_bytes: bytes, max_pages: int | None = 25) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[: min(max_pages, len(pdf.pages))]
        lines: list[str] = []
        for page in pages:
            for raw_line in (page.extract_text() or "").splitlines():
                line = normalize_text(raw_line)
                if line:
                    lines.append(line)
        return lines


def extract_tables_from_pdf(pdf_bytes: bytes, max_pages: int | None = 25) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[: min(max_pages, len(pdf.pages))]
        for page_index, page in enumerate(pages, start=1):
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            for table in tables:
                if table and len(table) >= 2:
                    clean = [[normalize_text(c) for c in row] for row in table if row]
                    out.append({"page": page_index, "rows": clean})
    return out


def find_first_pattern(lines: list[str], patterns: list[str]) -> str | None:
    for line in lines:
        for pattern in patterns:
            m = re.match(pattern, normalize_text(line), re.IGNORECASE)
            if m and normalize_text(m.group(1)):
                return normalize_text(m.group(1))
    return None


def find_metadata(lines: list[str]) -> dict[str, Any]:
    result = {"product_name": None, "producer": None, "epd_id": None, "issue_date": None, "valid_to": None, "declared_unit": None, "density": None, "conversion_factor_to_kg": None}
    for i, line in enumerate(lines):
        lower = line.lower()
        if result["product_name"] is None and any(k in lower for k in ["product name", "produktnavn", "declared product", "deklareret produkt", "epd for"]):
            if ":" in line:
                cand = normalize_text(line.split(":", 1)[1])
            else:
                cand = normalize_text(lines[i + 1]) if i + 1 < len(lines) else ""
            if cand and not cand.lower().startswith(("is ", "are ", "1 ", "1m", "1 m")):
                result["product_name"] = cand
        if result["producer"] is None and any(k in lower for k in ["manufacturer", "producer", "producent", "owner of the declaration", "deklarationens ejer", "ejer:"]):
            if ":" in line:
                cand = normalize_text(line.split(":", 1)[1])
            else:
                cand = normalize_text(lines[i + 1]) if i + 1 < len(lines) else ""
            if not _looks_like_bad_producer(cand):
                result["producer"] = cand
        if result["epd_id"] is None:
            for pattern in METADATA_PATTERNS["epd_id"]:
                m = re.match(pattern, line, re.IGNORECASE)
                if m:
                    cand = normalize_text(m.group(1)).upper()
                    if len(cand) > 2 and not cand.lower().startswith(("version", "rev")):
                        result["epd_id"] = cand
                    break
        if result["issue_date"] is None and any(k in lower for k in ["issued", "issue date", "udstedt"]):
            result["issue_date"] = safe_iso_date(line)
        if result["valid_to"] is None and any(k in lower for k in ["valid to", "valid until", "gyldig til"]):
            result["valid_to"] = safe_iso_date(line)
        if result["declared_unit"] is None and any(k in lower for k in ["declared unit", "deklareret enhed", "functional unit"]):
            m = re.search(r"(?:declared unit|deklareret enhed|functional unit)[^0-9a-zA-Z]*(?:is|er)?\s*([0-9.,]+\s*(?:kg/m3|kg/m³|kg/m2|kg/m²|m2|m3|m³|kg|l|liter)\b)", line, re.IGNORECASE)
            if m:
                result["declared_unit"] = normalize_text(m.group(1))
            else:
                for j in range(i + 1, min(i + 5, len(lines))):
                    maybe = normalize_text(lines[j])
                    m2 = re.search(r"([0-9.,]+\s*(?:kg/m3|kg/m³|kg/m2|kg/m²|m2|m3|m³|kg|l|liter)\b)", maybe, re.IGNORECASE)
                    if m2:
                        result["declared_unit"] = normalize_text(m2.group(1)); break
        if result["density"] is None and any(k in lower for k in ["density", "massefylde", "masse "]):
            m = re.search(r"([0-9.,]+\s*(?:kg/m3|kg/m³|kg/m2|kg/m²)\b)", line, re.IGNORECASE)
            if m:
                result["density"] = normalize_text(m.group(1))
        if result["conversion_factor_to_kg"] is None and any(k in lower for k in ["conversion factor to 1 kg", "omregningsfaktor til 1 kg"]):
            nums = NUMBER_RE.findall(line)
            if nums:
                val = parse_float(nums[-1])
                if val:
                    result["conversion_factor_to_kg"] = val

    for key in ["product_name", "producer", "epd_id"]:
        if result[key] is None:
            result[key] = find_first_pattern(lines, METADATA_PATTERNS[key])
    if _looks_like_bad_producer(result["producer"]):
        result["producer"] = None
    return result


def canonical_stage(s: str) -> str | None:
    k = norm_key(s).upper().replace(" ", "")
    k = k.replace("A1–A3", "A1-A3").replace("A1-A3", "A1-A3")
    k = k.replace("A1-3", "A1-A3")
    if k in STAGE_CODE_MAP:
        return STAGE_CODE_MAP[k]
    return None


def stage_tokens_from_text(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for m in STAGE_TOKEN_RE.finditer(text or ""):
        raw = m.group(1).upper().replace(" ", "").replace("–", "-")
        raw = raw.replace("A1-3", "A1-A3")
        code = STAGE_CODE_MAP.get(raw)
        if code:
            tokens.append((raw, code))
    return tokens


def indicator_from_label(label: str) -> str | None:
    l = norm_key(label)
    # Exact/priority matching. Crucially, GWP must be GWP-total, not fossil/bio/luluc.
    if re.search(r"\bgwp\s*[- ]?total\b", l) or l in {"gwp", "global warming potential", "global warming potential total"}:
        return "GWP"
    if re.search(r"\bgwp\s*[- ]?(fossil|bio|biogenic|luluc|ghg)\b", l):
        return None
    if re.search(r"\bodp\b|ozone depletion", l): return "ODP"
    if re.search(r"\bap\b|acidification", l): return "AP"
    if re.search(r"\bep\s*[- ]?(fw|freshwater|fresh water)\b|eutrophication.*fresh", l): return "EP"
    # Old EN15804 may have one generic EP row. Avoid EP-mar/ter if no EP-fw exists.
    if re.fullmatch(r"ep", l) or re.search(r"\beutrophication potential\b", l): return "EP"
    if re.search(r"\b(ep\s*[- ]?(mar|marine|ter|terrestrial))\b", l): return None
    if re.search(r"\bpocp\b|photochemical", l): return "POCP"
    if re.search(r"adp\s*[- ]?(mm|m&m|mineral|minerals)|\badpe\b|abiotic depletion.*(mineral|metal|non fossil)", l): return "ADPE"
    if re.search(r"adp\s*[- ]?(fos|fossil)|\badpf\b|abiotic depletion.*fossil", l): return "ADPF"
    if re.search(r"\bpert\b|total use of renewable primary energy|\bper\b", l): return "PER"
    if re.search(r"\bpenrt\b|total use of non renewable primary energy|\bpenr\b", l): return "PENR"
    if re.search(r"\brsf\b|renewable secondary fuels", l): return "SER"
    if re.search(r"\bnrsf\b|non renewable secondary fuels", l): return "SENR"
    if re.search(r"\bfw\b|net use of fresh water", l): return "FW"  # not imported by current LCAbyg template, but useful in preview
    if re.search(r"\bsm\b|secondary material", l): return "SM"   # not imported by current LCAbyg template, but useful in preview
    return None


def _first_numeric_cols(row: list[str]) -> list[int]:
    cols = []
    for i, cell in enumerate(row):
        if SCI_RE.match(normalize_text(cell).replace(" ", "")):
            cols.append(i)
    return cols


def _nearest_header(row: list[str], col: int, start: int = 0, end: int | None = None) -> str:
    end = len(row) if end is None else min(end, len(row))
    best = ""
    best_dist = 999
    for j in range(start, end):
        val = normalize_text(row[j]) if j < len(row) else ""
        if val and not SCI_RE.match(val.replace(" ", "")):
            d = abs(j - col)
            if d < best_dist:
                best, best_dist = val, d
    return best


def _set_indicator(target: dict[str, float], ind: str, val: float) -> None:
    # Do not let GWP-fossil/bio overwrite GWP-total, and generally keep first trusted value.
    if ind not in target:
        target[ind] = val


def parse_tables_for_stages(pdf_bytes: bytes) -> tuple[dict[str, dict[str, dict[str, float]]], list[dict[str, Any]]]:
    """Return variant -> stage -> indicators.

    Handles two common EPD layouts:
    1) Columns are stages (A1-A3, A4, ...), optionally with product variants below each stage.
    2) Rows are modules/stages and product variants are columns (common EPD Norway layout).
    If A1, A2 and A3 are separated, they are summed to A1to3.
    """
    tables = extract_tables_from_pdf(pdf_bytes)
    variants: dict[str, dict[str, dict[str, float]]] = {}
    diagnostics: list[dict[str, Any]] = []

    def put(variant: str, stage: str, ind: str, val: float, page: int, layout: str):
        v = normalize_text(variant) or "Default"
        variants.setdefault(v, {}).setdefault(stage, {})
        _set_indicator(variants[v][stage], ind, val)
        diagnostics.append({"page": page, "layout": layout, "variant": v, "stage": stage, "indicator": ind, "value": val})

    for item in tables:
        page = item["page"]
        rows = item["rows"]
        if not rows:
            continue
        table_text = " ".join(" ".join(r) for r in rows).lower()
        if not any(x in table_text for x in ["gwp", "global warming", "odp", "acidification", "resource consumption", "ressource consumption"]):
            continue

        # Layout 1: header row contains stage columns.
        for hi, hrow in enumerate(rows[:8]):
            stage_positions: list[tuple[int, str]] = []
            for ci, cell in enumerate(hrow):
                code = canonical_stage(cell)
                if code:
                    # Keep only one occurrence per starting col.
                    stage_positions.append((ci, code))
            if not stage_positions:
                continue
            stage_positions = sorted(stage_positions, key=lambda x: x[0])
            for ri in range(hi + 1, len(rows)):
                row = rows[ri]
                label = " ".join([c for c in row[:4] if c])
                ind = indicator_from_label(label)
                if not ind:
                    # Sometimes label is split over two rows: join previous label with this row's first cells.
                    if ri > 0:
                        label2 = " ".join([c for c in rows[ri-1][:4] + row[:4] if c])
                        ind = indicator_from_label(label2)
                if not ind:
                    continue
                numeric_cols = _first_numeric_cols(row)
                if not numeric_cols:
                    continue
                for sp_i, (start_col, stage) in enumerate(stage_positions):
                    end_col = stage_positions[sp_i + 1][0] if sp_i + 1 < len(stage_positions) else len(row)
                    in_group = [c for c in numeric_cols if start_col <= c < end_col]
                    for c in in_group:
                        val = parse_float(row[c])
                        if val is None:
                            continue
                        variant = "Default"
                        # Look around row(s) below/above the stage header for a variant label.
                        for hdr_i in [hi + 1, hi + 2, hi - 1]:
                            if 0 <= hdr_i < len(rows):
                                cand = _nearest_header(rows[hdr_i], c, start_col, end_col)
                                if cand and canonical_stage(cand) is None and indicator_from_label(cand) is None and "unit" not in cand.lower() and "parameter" not in cand.lower():
                                    variant = cand
                                    break
                        put(variant, stage, ind, val, page, "stage_columns")
            break

        # Layout 2: a column named Module contains A1-3/A4/etc. Product variants are other columns.
        header_index = None
        module_col = None
        for ri, row in enumerate(rows[:8]):
            for ci, cell in enumerate(row):
                if norm_key(cell) in {"module", "modules", "modul"}:
                    header_index, module_col = ri, ci
                    break
            if header_index is not None:
                break
        if header_index is not None and module_col is not None:
            header = rows[header_index]
            current_indicator: str | None = None
            for ri in range(header_index + 1, len(rows)):
                row = rows[ri]
                left_label = " ".join([c for c in row[: max(module_col, 1)] if c])
                maybe_ind = indicator_from_label(left_label)
                if maybe_ind:
                    current_indicator = maybe_ind
                stage = canonical_stage(row[module_col] if module_col < len(row) else "")
                if not stage or not current_indicator:
                    continue
                for ci in _first_numeric_cols(row):
                    if ci <= module_col:
                        continue
                    val = parse_float(row[ci])
                    if val is None:
                        continue
                    variant = _nearest_header(header, ci, module_col + 1, len(header)) or "Default"
                    put(variant, stage, current_indicator, val, page, "module_rows")

        # Layout 3: stages are A1, A2, A3 columns. Sum to A1to3 for Default.
        # Works for One Click / sector EPDs with one product per table.
        header_i = None
        col_stage: dict[int, str] = {}
        for ri, row in enumerate(rows[:8]):
            for ci, cell in enumerate(row):
                code = canonical_stage(cell)
                if code in {"A1", "A2", "A3"}:
                    col_stage[ci] = code
            if {"A1", "A2", "A3"}.issubset(set(col_stage.values())):
                header_i = ri
                break
        if header_i is not None:
            for ri in range(header_i + 1, len(rows)):
                row = rows[ri]
                label = " ".join([c for c in row[:3] if c])
                ind = indicator_from_label(label)
                if not ind:
                    continue
                subtotal = 0.0
                ok = False
                for ci, st in col_stage.items():
                    if st in {"A1", "A2", "A3"} and ci < len(row):
                        val = parse_float(row[ci])
                        if val is not None:
                            subtotal += val; ok = True
                if ok:
                    put("Default", "A1to3", ind, subtotal, page, "sum_A1_A2_A3")

    # Create synthetic A1to3 from A1+A2+A3 when needed.
    for vname, stages in list(variants.items()):
        if "A1to3" not in stages and all(s in stages for s in ["A1", "A2", "A3"]):
            merged: dict[str, float] = {}
            for ind in set(stages["A1"]) | set(stages["A2"]) | set(stages["A3"]):
                merged[ind] = float(stages["A1"].get(ind, 0.0) + stages["A2"].get(ind, 0.0) + stages["A3"].get(ind, 0.0))
            stages["A1to3"] = merged
    return variants, diagnostics


def choose_default_variant(variants: dict[str, dict[str, dict[str, float]]], metadata: dict[str, Any], filename_hint: str | None = None) -> str | None:
    if not variants:
        return None
    names = list(variants.keys())
    if len(names) == 1:
        return names[0]
    hay = " ".join([str(metadata.get("product_name") or ""), str(filename_hint or "")]).lower()
    # Prefer variant numbers mentioned in product/file name, e.g. AAC 535.
    nums = re.findall(r"\b\d{2,4}\b", hay)
    for num in nums:
        for n in names:
            if num in n:
                return n
    # Prefer non-default with GWP in A1to3; otherwise last product variant often is desired in multi-column EPDs.
    candidates = [n for n in names if variants[n].get("A1to3", {}).get("GWP") is not None]
    if candidates:
        return candidates[-1] if len(candidates) <= 3 else candidates[0]
    return names[0]


def unit_from_declared_unit(declared_unit: str | None) -> str:
    lower = (declared_unit or "").lower()
    for token, mapped in UNIT_MAP.items():
        if token in lower:
            return mapped
    return "KG"


def mass_factor_from_metadata(md: dict[str, Any], unit: str) -> float:
    # LCAbyg's mass_factor can be used for m3/m2 units. Keep conservative if unsure.
    if unit == "KG":
        return 1.0
    if md.get("conversion_factor_to_kg"):
        # The EPD value is usually declared-unit per kg; density = 1 / factor.
        try:
            factor = float(md["conversion_factor_to_kg"])
            if factor > 0:
                return 1.0 / factor
        except Exception:
            pass
    density = md.get("density")
    if density:
        m = NUMBER_RE.search(str(density))
        val = parse_float(m.group(0)) if m else None
        if val and val > 0:
            return float(val)
    return 1.0


def local_name(text: str) -> dict[str, str]:
    return {"English": text, "Danish": text, "German": text, "Norwegian": text}


def comment(extra: str = "") -> dict[str, str]:
    return {"English": extra, "Danish": extra, "German": extra, "Norwegian": extra}


def complete_indicators(values: dict[str, float]) -> dict[str, float]:
    return {k: float(values.get(k, 0.0)) for k in ALL_INDICATORS}


def parse_epd_pdf_bytes(pdf_bytes: bytes, filename_hint: str | None = None) -> dict[str, Any]:
    lines = extract_lines_from_pdf(pdf_bytes)
    metadata = find_metadata(lines)
    variants, diagnostics = parse_tables_for_stages(pdf_bytes)
    selected = choose_default_variant(variants, metadata, filename_hint)
    selected_values = variants.get(selected, {}) if selected else {}
    return {
        "metadata": metadata,
        "variants": variants,
        "selected_variant": selected,
        "stage_values": selected_values,
        "diagnostics": diagnostics,
        "lines_count": len(lines),
    }


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


def build_lcabyg_import_files(parsed: dict[str, Any], project_name: str = "EPD import project", amount: float = 1.0, lifespan: int = 50, variant: str | None = None) -> dict[str, list[dict[str, Any]]]:
    md = parsed.get("metadata", {})
    variants = parsed.get("variants", {}) or {}
    variant_name = variant or parsed.get("selected_variant")
    stages = variants.get(variant_name, parsed.get("stage_values", {}) or {})
    a1to3 = stages.get("A1to3", {})

    product_name = md.get("product_name") or variant_name or md.get("epd_id") or "Imported EPD product"
    if variant_name and variant_name != "Default" and variant_name.lower() not in product_name.lower():
        product_name = f"{product_name} ({variant_name})"
    producer = md.get("producer") or ""
    epd_id = md.get("epd_id") or ""
    unit = unit_from_declared_unit(md.get("declared_unit"))
    mass_factor = mass_factor_from_metadata(md, unit)

    files = _load_template_files()

    for _, project in _walk_nodes(files, "Project"):
        project["name"] = {"Danish": project_name}
        project["building_regulation_version"] = "BR2023"

    for _, building in _walk_nodes(files, "Building"):
        building["calculation_timespan"] = lifespan
        building["initial_year"] = datetime.now().year

    for _, product in _walk_nodes(files, "Product"):
        _set_localized_name(product, product_name)
        product["source"] = "User"
        product["comment"] = comment(f"Generated from EPD PDF. Producer: {producer}; EPD id: {epd_id}; variant: {variant_name or ''}")
        product["uncertainty_factor"] = 1.0

    for _, construction in _walk_nodes(files, "Construction"):
        _set_localized_name(construction, product_name)
        construction["unit"] = unit
        construction["source"] = "User"
        construction["comment"] = comment(f"EPD id: {epd_id}; declared unit: {md.get('declared_unit') or ''}; variant: {variant_name or ''}")
        construction["locked"] = False

    for _, element in _walk_nodes(files, "Element"):
        _set_localized_name(element, "Imported EPD element")
        element["source"] = "User"

    for entry in files.get("Construction.json", []):
        edge_obj = entry.get("Edge") if isinstance(entry, dict) else None
        if edge_obj and isinstance(edge_obj[0], dict) and "ConstructionToProduct" in edge_obj[0]:
            payload = edge_obj[0]["ConstructionToProduct"]
            if isinstance(payload, dict):
                payload["amount"] = amount
                payload["unit"] = unit
                payload["lifespan"] = lifespan
                payload["enabled"] = True

    for _, stage in _walk_nodes(files, "Stage"):
        _set_localized_name(stage, f"{product_name} - A1-A3")
        stage["comment"] = comment("A1-A3 extracted automatically from EPD PDF. Check values before use.")
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
        stage["external_version"] = variant_name or ""
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
