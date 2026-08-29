"""Responzivní vzhled pro telefon a tablet.

Streamlit kreslí sloupce vedle sebe i na úzkém displeji a tabulky
přetékají. Tuhle CSS tomu maže padding, skládá dvou-sloupcové bloky
pod sebe a tabulky nechá posouvat vodorovně.
"""

STYLY = """
<style>
/* Míň prázdného okraje na všech šířkách – na telefonu to žere obrazovku. */
.block-container {
  padding-top: 2.2rem;
  padding-bottom: 3rem;
  padding-left: 2rem;
  padding-right: 2rem;
}
[data-testid="stHeader"] {
  background: transparent;
}

/* Tabulky se na úzkém displeji posouvají, místo aby uřízly sloupce. */
[data-testid="stTable"],
[data-testid="stDataFrame"],
div[data-testid="stTableContainer"] {
  overflow-x: auto !important;
  max-width: 100%;
}
[data-testid="stTable"] {
  display: block;
}
[data-testid="stTable"] table {
  min-width: 36rem;
  width: max-content;
}

/* Tablet */
@media (max-width: 1100px) {
  .block-container {
    padding-left: 1.2rem;
    padding-right: 1.2rem;
    max-width: 100% !important;
  }
  h1 { font-size: 1.7rem !important; line-height: 1.25 !important; }
  h2 { font-size: 1.3rem !important; }
  h3 { font-size: 1.15rem !important; }
}

/* Telefon */
@media (max-width: 768px) {
  .block-container {
    padding: 0.9rem 0.7rem 4.5rem !important;
  }
  h1 { font-size: 1.4rem !important; }
  h2 { font-size: 1.15rem !important; }
  h3 { font-size: 1.05rem !important; }
  p, label, .stMarkdown, .stCaption {
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }
  /* Dva sloupce (zápas | predikce, domácí | hosté) pod sebe.
     Tři úzké sloupce 1/X/2 necháme v řadě. */
  div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2):last-child) {
    flex-direction: column !important;
    gap: 0.4rem !important;
  }
  div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2):last-child) > div {
    width: 100% !important;
    min-width: 100% !important;
  }
  [data-testid="stTable"] table {
    min-width: 34rem;
    font-size: 0.85rem;
  }
  [data-testid="stSidebar"] [data-testid="stDataFrame"],
  [data-testid="stSidebar"] [data-testid="stTable"] {
    overflow-x: auto;
  }
  .stButton > button {
    width: 100%;
    white-space: normal;
    height: auto;
    padding-top: 0.55rem;
    padding-bottom: 0.55rem;
  }
  /* Trojice 1/X/2 ať se vejde vedle sebe, ne přeteče. */
  div[data-testid="stHorizontalBlock"]:has(> div:nth-child(3):last-child) > div {
    min-width: 0 !important;
    flex: 1 1 0 !important;
  }
}

/* Široký telefon / malý tablet naležato */
@media (min-width: 769px) and (max-width: 1100px) {
  div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2):last-child) {
    gap: 0.8rem !important;
  }
}
</style>
"""


def vloz_styly():
    """Vloží CSS. Volat hned po ``set_page_config``."""
    import streamlit as st

    st.markdown(STYLY, unsafe_allow_html=True)


def siroka_tabulka(df):
    """Široká tabulka s vodorovným posunem na telefonu."""
    import streamlit as st

    st.dataframe(df, width="stretch")
