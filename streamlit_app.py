"""EPD PDF -> LCAbyg 5.3.1 project import folder/zip."""

from pathlib import Path
import json
import streamlit as st

from epd_to_lcabyg_helpers import (
    build_import_zip_bytes,
    build_lcabyg_import_files,
    parse_epd_pdf_bytes,
)

st.set_page_config(page_title="EPD → LCAbyg import", layout="wide")
st.title("EPD → LCAbyg project import")

st.markdown(
    "Upload en EPD-PDF. Appen udtrækker metadata og miljøindikatorer og laver en ZIP med "
    "flere JSON-filer efter LCAbyg project import-strukturen. Importér den udpakkede mappe i LCAbyg."
)

uploaded_pdf = st.file_uploader("Upload EPD PDF", type=["pdf"])

with st.sidebar:
    st.header("Projektindstillinger")
    project_name = st.text_input("Projektnavn", "EPD import project")
    amount = st.number_input("Mængde i konstruktion", min_value=0.0, value=1.0, step=0.1)
    lifespan = st.number_input("Levetid", min_value=1, value=50, step=1)

if uploaded_pdf is not None:
    pdf_bytes = uploaded_pdf.read()
    with st.spinner("Parser PDF og bygger LCAbyg importpakke..."):
        parsed = parse_epd_pdf_bytes(pdf_bytes)
        files = build_lcabyg_import_files(parsed, project_name=project_name, amount=amount, lifespan=int(lifespan))
        zip_bytes = build_import_zip_bytes(files)

    metadata = parsed["metadata"]
    stage_values = parsed["stage_values"]

    st.subheader("Udtrukket metadata")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Produktnavn:** {metadata.get('product_name') or '—'}")
        st.markdown(f"**Producent:** {metadata.get('producer') or '—'}")
        st.markdown(f"**EPD id:** {metadata.get('epd_id') or '—'}")
        st.markdown(f"**Declared unit:** {metadata.get('declared_unit') or '—'}")
    with col2:
        st.markdown(f"**Issued:** {metadata.get('issue_date') or '—'}")
        st.markdown(f"**Valid to:** {metadata.get('valid_to') or '—'}")
        st.markdown(f"**Density:** {metadata.get('density') or '—'}")
        st.markdown(f"**Linjer læst:** {parsed.get('lines_count')}")

    st.subheader("Faser og indikatorer")
    if stage_values:
        st.json(stage_values)
    else:
        st.warning("Ingen indikatorer fundet. ZIP'en indeholder kun en placeholder-stage.")

    st.subheader("LCAbyg importpakke")
    st.info(
        "Download ZIP'en, pak den ud i en tom mappe, og vælg selve mappen i LCAbyg's JSON/projektimport. "
        "Der må kun ligge de udpakkede JSON-filer i mappen."
    )

    safe_id = metadata.get("epd_id") or metadata.get("product_name") or "epd_import"
    safe_id = str(safe_id).replace(" ", "_").replace("/", "_")

    st.download_button(
        label="Download LCAbyg import ZIP",
        data=zip_bytes,
        file_name=f"lcabyg_import_{safe_id}.zip",
        mime="application/zip",
    )

    with st.expander("Vis filerne i ZIP'en"):
        st.write(sorted(files.keys()))

    with st.expander("Preview Stage.json"):
        st.code(json.dumps(files.get("Stage.json", []), indent=2, ensure_ascii=False), language="json")
