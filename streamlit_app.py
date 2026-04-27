"""
EPD PDF -> LCAbyg JSON project/library exporter.

This app extracts as much data as possible from a verified EPD PDF and exports a fuller
LCAbyg-style JSON package. It creates either:
1) a minimal import project: Building -> BuildingPart -> Construction -> Product -> Stages, or
2) a product/stage library payload.
"""

import json
import re
from pathlib import Path

import streamlit as st

from epd_to_lcabyg_helpers_v2 import (
    LCABYG_SAFE_INDICATORS,
    build_json_text,
    build_lcabyg_library_payload,
    build_lcabyg_project_payload,
    parse_epd_pdf_bytes,
)

st.set_page_config(page_title="EPD -> LCAbyg JSON", layout="wide")
st.title("EPD -> LCAbyg JSON exporter")

st.markdown(
    "Upload en verificeret EPD PDF. Appen forsøger at udtrække metadata og alle fundne miljøindikatorer "
    "for alle livscyklusfaser, og bygger en LCAbyg JSON-pakke."
)

with st.sidebar:
    st.header("Eksportindstillinger")
    export_mode = st.radio(
        "JSON-type",
        options=["Minimal project", "Product/stage library"],
        help="Minimal project laver Building -> BuildingPart -> Construction -> Product -> Stage. Library laver kun Product -> Stage.",
    )
    max_pages = st.number_input("Maks. PDF-sider der læses (0 = alle)", min_value=0, max_value=500, value=0, step=1)
    include_extra = st.checkbox(
        "Medtag ekstra indikatorer ud over konservativ LCAbyg-liste",
        value=False,
        help="Slå kun til hvis din LCAbyg-version accepterer nyere/ekstra EN15804+A2 indikatornavne.",
    )
    st.caption("Konservativ liste: " + ", ".join(sorted(LCABYG_SAFE_INDICATORS)))

uploaded_pdf = st.file_uploader("Upload EPD PDF", type=["pdf"])

if uploaded_pdf is not None:
    pdf_bytes = uploaded_pdf.read()
    with st.spinner("Parser PDF og udtrækker EPD-data..."):
        parsed = parse_epd_pdf_bytes(pdf_bytes, max_pages=None if max_pages == 0 else int(max_pages))

    metadata = parsed["metadata"]
    stage_values = parsed["stage_values"]

    product_name = metadata.get("product_name") or "EPD product"
    producer = metadata.get("producer")
    epd_id = metadata.get("epd_id")
    valid_to = metadata.get("valid_to")
    declared_unit = metadata.get("declared_unit")

    st.subheader("Udtrukne metadata")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Product name:** {product_name or '—'}")
        st.markdown(f"**Producer:** {producer or '—'}")
        st.markdown(f"**EPD id:** {epd_id or '—'}")
        st.markdown(f"**Declared unit:** {declared_unit or '—'}")
    with c2:
        st.markdown(f"**Issued:** {metadata.get('issue_date') or '—'}")
        st.markdown(f"**Valid to:** {valid_to or '—'}")
        st.markdown(f"**Density:** {metadata.get('density') or '—'}")
        st.markdown(f"**Linjer læst:** {parsed['lines_count']}")

    st.subheader("Fundne indikatorer pr. livscyklusfase")
    if not stage_values:
        st.error("Ingen stage-tabel blev fundet. Prøv at læse alle sider, eller tjek PDF-formatet.")
        with st.expander("PDF tekst-preview til fejlfinding"):
            st.code("\n".join(parsed.get("lines_preview", [])))
        st.stop()

    st.json(stage_values)

    if export_mode == "Minimal project":
        payload = build_lcabyg_project_payload(
            product_name=product_name,
            producer=producer,
            epd_id=epd_id,
            valid_to=valid_to,
            declared_unit=declared_unit,
            stage_values=stage_values,
            include_extra_indicators=include_extra,
        )
    else:
        payload = build_lcabyg_library_payload(
            product_name=product_name,
            producer=producer,
            epd_id=epd_id,
            valid_to=valid_to,
            declared_unit=declared_unit,
            stage_values=stage_values,
            include_extra_indicators=include_extra,
        )

    json_text = build_json_text(payload)
    filename_safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", epd_id or product_name or "epd")
    suffix = "project" if export_mode == "Minimal project" else "library"
    file_name = f"lcabyg_{filename_safe}_{suffix}.json"

    st.download_button(
        label=f"Download {export_mode} JSON",
        data=json_text.encode("utf-8"),
        file_name=file_name,
        mime="application/json",
    )

    with st.expander("Preview generated JSON payload"):
        st.code(json_text, language="json")

    with st.expander("PDF tekst-preview til fejlfinding"):
        st.code("\n".join(parsed.get("lines_preview", [])))
