"""EPD PDF -> LCAbyg 5.3.1 project import folder/zip."""

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
    "Upload en EPD-PDF. Appen udtrækker metadata og A1-A3-miljøindikatorer og laver en ZIP med "
    "JSON-filer efter den LCAbyg project import-struktur, som vi har testet virker."
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
        parsed = parse_epd_pdf_bytes(pdf_bytes, filename_hint=uploaded_pdf.name)

    metadata = parsed["metadata"]
    variants = parsed.get("variants", {}) or {}
    variant_names = list(variants.keys()) or ["Default"]
    default_variant = parsed.get("selected_variant") if parsed.get("selected_variant") in variant_names else variant_names[0]

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
        st.markdown(f"**Density/masse:** {metadata.get('density') or '—'}")
        st.markdown(f"**Linjer læst:** {parsed.get('lines_count')}")

    st.subheader("Vælg produktvariant")
    selected_variant = st.selectbox(
        "Produktvariant fundet i EPD-tabellen",
        variant_names,
        index=variant_names.index(default_variant),
        help="Vælg den produktkolonne, som svarer til det produkt du vil importere. Fx AAC 535 for H+H Multiplade 535.",
    )

    selected_values = variants.get(selected_variant, {})
    a1to3 = selected_values.get("A1to3", {})

    st.subheader("A1-A3-indikatorer der importeres")
    if a1to3:
        st.json(a1to3)
        if "GWP" in a1to3:
            st.success(f"GWP-total A1-A3: {a1to3['GWP']:g}")
    else:
        st.warning("Ingen A1-A3-indikatorer fundet for den valgte variant. ZIP'en indeholder derfor placeholder-værdier.")

    with st.expander("Alle fundne varianter/faser"):
        st.json(variants)

    with st.expander("Parser-diagnostik"):
        st.dataframe(parsed.get("diagnostics", []), use_container_width=True)

    files = build_lcabyg_import_files(
        parsed,
        project_name=project_name,
        amount=amount,
        lifespan=int(lifespan),
        variant=selected_variant,
    )
    zip_bytes = build_import_zip_bytes(files)

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

    with st.expander("Preview Stage.json"):
        st.code(json.dumps(files.get("Stage.json", []), indent=2, ensure_ascii=False), language="json")
