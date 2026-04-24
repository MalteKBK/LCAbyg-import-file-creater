"""
EPD -> LCAbyg JSON exporter.
Upload an EPD PDF and download a JSON import package for LCAbyg.
"""

import json
import streamlit as st
from pathlib import Path

from epd_to_lcabyg_helpers import (
    build_json_text,
    build_lcabyg_payload,
    parse_epd_pdf_bytes,
)

st.set_page_config(page_title="EPD → LCAbyg", layout="wide")
st.title("EPD → LCAbyg JSON Exporter")

st.markdown(
    "Upload a verified EPD PDF and the app will extract the product metadata and primary stage indicators. "
    "You can download a JSON file suitable for import into LCAbyg."
)

uploaded_pdf = st.file_uploader("Upload EPD PDF", type=["pdf"])

if uploaded_pdf is not None:
    pdf_bytes = uploaded_pdf.read()
    with st.spinner("Parsing PDF and extracting EPD data..."):
        parsed = parse_epd_pdf_bytes(pdf_bytes)

    metadata = parsed["metadata"]
    stage_code = parsed["stage_code"]
    stage_indicators = parsed["stage_indicators"]

    st.subheader("Extracted EPD metadata")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**Product name:** {metadata.get('product_name') or '—'}")
        st.markdown(f"**Producer:** {metadata.get('producer') or '—'}")
        st.markdown(f"**EPD id:** {metadata.get('epd_id') or '—'}")
        st.markdown(f"**Declared unit:** {metadata.get('declared_unit') or '—'}")

    with col2:
        st.markdown(f"**Issued:** {metadata.get('issue_date') or '—'}")
        st.markdown(f"**Valid to:** {metadata.get('valid_to') or '—'}")
        st.markdown(f"**Density:** {metadata.get('density') or '—'}")
        st.markdown(f"**Extracted stage:** {stage_code or 'None'}")

    st.markdown("---")
    st.subheader("Stage indicators extracted for LCAbyg")

    if stage_indicators:
        st.json(stage_indicators)
    else:
        st.warning(
            "No stage indicators were extracted automatically. "
            "Try a different EPD PDF or check the PDF formatting."
        )

    if stage_code and stage_indicators:
        payload = build_lcabyg_payload(
            product_name=metadata.get("product_name") or "EPD product",
            producer=metadata.get("producer"),
            epd_id=metadata.get("epd_id"),
            valid_to=metadata.get("valid_to"),
            declared_unit=metadata.get("declared_unit"),
            stage_code=stage_code,
            indicators=stage_indicators,
        )

        json_text = build_json_text(payload)
        filename_safe = metadata.get("epd_id") or metadata.get("product_name") or "epd"
        filename_safe = filename_safe.replace(" ", "_").replace("/", "_")
        file_name = f"lcabyg_{filename_safe}.json"

        st.download_button(
            label="Download LCAbyg JSON",
            data=json_text.encode("utf-8"),
            file_name=file_name,
            mime="application/json",
        )

        with st.expander("Preview generated JSON payload"):
            st.code(json_text, language="json")
    else:
        st.info("The EPD was parsed, but the JSON package cannot be generated until stage indicators are found.")
