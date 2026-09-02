import streamlit as st
import pandas as pd
from datetime import datetime
import io
import os
from google import genai


# ============================================================
# CONFIGURAZIONE
# ============================================================

st.set_page_config(
    page_title="Affittacamere Gestore IA",
    page_icon="🛏️",
    layout="wide"
)


# ============================================================
# CLIENT GEMINI
# ============================================================

def get_gemini_client():
    """
    Recupera la chiave Gemini dai Secrets di Streamlit.
    NON inserire mai la chiave direttamente nel codice.
    """
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        return genai.Client(api_key=api_key)
    except Exception:
        return None


client = get_gemini_client()


MODELLO = "gemini-3.7-flash"


# ============================================================
# MEMORIA TEMPORANEA
# ============================================================

if "prenotazioni" not in st.session_state:
    st.session_state.prenotazioni = []


if "camere_stato" not in st.session_state:
    st.session_state.camere_stato = {
        "Baia di Budoni": {
            "stato": "Disponibile",
            "ospite": "-"
        },
        "La Cinta": {
            "stato": "Disponibile",
            "ospite": "-"
        },
        "Cala Brandinchi": {
            "stato": "Disponibile",
            "ospite": "-"
        },
        "Capo Comino": {
            "stato": "Disponibile",
            "ospite": "-"
        }
    }


if "documenti_caricati" not in st.session_state:
    st.session_state.documenti_caricati = []


if "messaggi_chat" not in st.session_state:
    st.session_state.messaggi_chat = [
        {
            "ruolo": "assistant",
            "contenuto": (
                "Ciao! 👋 Sono l'assistente IA dell'affittacamere.\n\n"
                "Puoi farmi domande sulle camere, sulle prenotazioni "
                "e sui documenti che hai caricato.\n\n"
                "Puoi scrivere normalmente, proprio come con ChatGPT o Gemini."
            )
        }
    ]


# ============================================================
# FUNZIONI
# ============================================================

def crea_contesto_gestionale():
    """
    Crea un riepilogo dei dati presenti nella memoria temporanea.
    Questo viene fornito a Gemini insieme alla domanda dell'utente.
    """

    testo = []

    testo.append("DATI DELL'AFFITTACAMERE")
    testo.append("")

    # Camere
    testo.append("STATO CAMERE:")

    for camera, info in st.session_state.camere_stato.items():
        testo.append(
            f"- {camera}: {info['stato']}; ospite: {info['ospite']}"
        )

    testo.append("")

    # Prenotazioni
    testo.append("PRENOTAZIONI:")

    if st.session_state.prenotazioni:
        for p in st.session_state.prenotazioni:
            testo.append(
                f"- Ospite: {p['Ospite']}; "
                f"Camera: {p['Camera']}; "
                f"Arrivo: {p['Arrivo']}; "
                f"Partenza: {p['Partenza']}; "
                f"Prezzo: €{p['Prezzo (€)']:.2f}"
            )
    else:
        testo.append("- Nessuna prenotazione presente.")

    testo.append("")

    # Documenti
    testo.append("DOCUMENTI CARICATI:")

    if st.session_state.documenti_caricati:
        for doc in st.session_state.documenti_caricati:

            testo.append("")
            testo.append(
                f"DOCUMENTO: {doc['nome']} "
                f"({doc['tipo']})"
            )

            contenuto = doc.get("contenuto", "")

            # Limite prudenziale per non creare prompt enormi
            testo.append(contenuto[:15000])

    else:
        testo.append("- Nessun documento caricato.")

    return "\n".join(testo)


def invia_a_gemini(domanda):
    """
    Invia domanda + contesto gestionale a Gemini.
    """

    if client is None:
        return (
            "⚠️ Non riesco a collegarmi a Gemini.\n\n"
            "Controlla che GEMINI_API_KEY sia configurata "
            "correttamente nei Secrets di Streamlit."
        )

    contesto = crea_contesto_gestionale()

    istruzioni = """
Sei l'assistente amministrativo e finanziario virtuale
di un affittacamere italiano.

Il tuo compito è aiutare i proprietari a comprendere:
- prenotazioni
- camere
- incassi
- spese
- fatture
- documenti
- dati economici

REGOLE IMPORTANTI:

1. Rispondi in italiano.

2. Puoi utilizzare esclusivamente le informazioni presenti
   nel contesto che ti viene fornito.

3. NON inventare numeri, fatture, date, clienti, prezzi
   o informazioni che non sono presenti nei dati.

4. Se una risposta non può essere determinata dai dati
   disponibili, dillo chiaramente.

5. Quando fai calcoli, mostra il ragionamento in modo semplice
   e indica i valori utilizzati.

6. Se l'utente chiede un totale, cerca di fornire un totale
   preciso basandoti sui dati disponibili.

7. Se l'utente chiede un confronto, confronta realmente
   i dati disponibili.

8. Se analizzi una fattura, indica quando possibile:
   - fornitore
   - numero fattura
   - data
   - imponibile
   - IVA
   - totale
   - eventuali informazioni importanti

9. Non dare consulenza fiscale o legale definitiva.
   Se la domanda riguarda obblighi fiscali complessi,
   suggerisci di verificare con il commercialista.

10. Rispondi in modo chiaro e comprensibile.
"""

    prompt_completo = f"""
{istruzioni}

========================
CONTESTO DELL'AFFITTACAMERE
========================

{contesto}

========================
DOMANDA DELL'UTENTE
========================

{domanda}
"""

    try:

        risposta = client.models.generate_content(
            model=MODELLO,
            contents=prompt_completo
        )

        return risposta.text

    except Exception as e:

        return (
            "❌ Si è verificato un errore durante la comunicazione "
            f"con Gemini.\n\nDettaglio: {str(e)}"
        )


# ============================================================
# TITOLO
# ============================================================

st.title("🛏️ Affittacamere Gestore IA")

st.write(
    "Gestionale temporaneo con prenotazioni, documenti "
    "e assistente IA."
)


# ============================================================
# MENU
# ============================================================

menu = st.sidebar.selectbox(
    "Menu di Navigazione",
    [
        "🏠 Panoramica Camere",
        "➕ Nuova Prenotazione",
        "📋 Elenco Prenotazioni",
        "📂 Documenti",
        "💬 Chat IA"
    ]
)


# ============================================================
# PANORAMICA CAMERE
# ============================================================

if menu == "🏠 Panoramica Camere":

    st.header("🏠 Stato Attuale delle Camere")

    dati_tabella = []

    for camera, info in st.session_state.camere_stato.items():

        dati_tabella.append({
            "Camera": camera,
            "Stato": info["stato"],
            "Ospite Attuale": info["ospite"]
        })

    df_camere = pd.DataFrame(dati_tabella)

    st.dataframe(
        df_camere,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# NUOVA PRENOTAZIONE
# ============================================================

elif menu == "➕ Nuova Prenotazione":

    st.header("➕ Registra una Nuova Prenotazione")

    with st.form("form_prenotazione"):

        nome_ospite = st.text_input(
            "Nome e Cognome Ospite"
        )

        camera_scelta = st.selectbox(
            "Seleziona Camera",
            list(st.session_state.camere_stato.keys())
        )

        data_arrivo = st.date_input(
            "Data di Arrivo"
        )

        data_partenza = st.date_input(
            "Data di Partenza"
        )

        prezzo_totale = st.number_input(
            "Prezzo Totale (€)",
            min_value=0.0,
            format="%.2f"
        )

        pulsante_salva = st.form_submit_button(
            "💾 Salva Prenotazione"
        )

        if pulsante_salva:

            if not nome_ospite.strip():

                st.error(
                    "Inserisci il nome dell'ospite."
                )

            elif data_partenza <= data_arrivo:

                st.error(
                    "La data di partenza deve essere "
                    "successiva alla data di arrivo."
                )

            else:

                nuova_prenotazione = {
                    "Ospite": nome_ospite,
                    "Camera": camera_scelta,
                    "Arrivo": str(data_arrivo),
                    "Partenza": str(data_partenza),
                    "Prezzo (€)": prezzo_totale
                }

                st.session_state.prenotazioni.append(
                    nuova_prenotazione
                )

                st.session_state.camere_stato[
                    camera_scelta
                ]["stato"] = "Occupata"

                st.session_state.camere_stato[
                    camera_scelta
                ]["ospite"] = nome_ospite

                st.success(
                    f"Prenotazione registrata per {nome_ospite}!"
                )


# ============================================================
# ELENCO PRENOTAZIONI
# ============================================================

elif menu == "📋 Elenco Prenotazioni":

    st.header("📋 Prenotazioni")

    if st.session_state.prenotazioni:

        df_prenotazioni = pd.DataFrame(
            st.session_state.prenotazioni
        )

        st.dataframe(
            df_prenotazioni,
            use_container_width=True,
            hide_index=True
        )

        totale = sum(
            p["Prezzo (€)"]
            for p in st.session_state.prenotazioni
        )

        st.metric(
            "Totale prenotazioni",
            f"€ {totale:,.2f}"
        )

    else:

        st.info(
            "Non ci sono ancora prenotazioni."
        )


# ============================================================
# DOCUMENTI
# ============================================================

elif menu == "📂 Documenti":

    st.header("📂 Archivio Temporaneo Documenti")

    st.info(
        "In questa versione i documenti vengono conservati "
        "solo nella memoria temporanea della sessione."
    )

    file_caricato = st.file_uploader(
        "Carica PDF, Excel o CSV",
        type=["pdf", "csv", "xlsx", "xls"]
    )

    if file_caricato is not None:

        nome_file = file_caricato.name

        # Evitiamo di caricare lo stesso file continuamente
        nomi_esistenti = [
            d["nome"]
            for d in st.session_state.documenti_caricati
        ]

        if nome_file not in nomi_esistenti:

            try:

                # ----------------------------
                # CSV
                # ----------------------------

                if nome_file.lower().endswith(".csv"):

                    df = pd.read_csv(file_caricato)

                    contenuto = df.to_string(
                        index=False
                    )

                    st.session_state.documenti_caricati.append({
                        "nome": nome_file,
                        "tipo": "CSV",
                        "contenuto": contenuto
                    })

                    st.success(
                        f"CSV '{nome_file}' caricato."
                    )

                    st.dataframe(
                        df,
                        use_container_width=True
                    )

                # ----------------------------
                # EXCEL
                # ----------------------------

                elif nome_file.lower().endswith(
                    (".xlsx", ".xls")
                ):

                    df = pd.read_excel(
                        file_caricato
                    )

                    contenuto = df.to_string(
                        index=False
                    )

                    st.session_state.documenti_caricati.append({
                        "nome": nome_file,
                        "tipo": "Excel",
                        "contenuto": contenuto
                    })

                    st.success(
                        f"Excel '{nome_file}' caricato."
                    )

                    st.dataframe(
                        df,
                        use_container_width=True
                    )

                # ----------------------------
                # PDF
                # ----------------------------

                elif nome_file.lower().endswith(".pdf"):

                    pdf_bytes = file_caricato.getvalue()

                    st.session_state.documenti_caricati.append({
                        "nome": nome_file,
                        "tipo": "PDF",
                        "contenuto": (
                            "PDF caricato. "
                            "Il documento può essere analizzato "
                            "dall'AI nella chat."
                        ),
                        "bytes": pdf_bytes
                    })

                    st.success(
                        f"PDF '{nome_file}' caricato."
                    )

            except Exception as e:

                st.error(
                    f"Errore durante il caricamento: {e}"
                )

        else:

            st.info(
                "Questo documento è già presente "
                "nella memoria temporanea."
            )

    st.divider()

    st.subheader("📄 Documenti presenti")

    if st.session_state.documenti_caricati:

        for doc in st.session_state.documenti_caricati:

            st.write(
                f"📄 **{doc['nome']}** — {doc['tipo']}"
            )

    else:

        st.info(
            "Nessun documento caricato."
        )


# ============================================================
# CHAT IA
# ============================================================

elif menu == "💬 Chat IA":

    st.header("💬 Assistente IA dell'Affittacamere")

    st.write(
        "Scrivi normalmente la tua domanda. "
        "Non devi usare parole chiave particolari."
    )

    if client is None:

        st.warning(
            "⚠️ Gemini non è ancora configurato. "
            "Aggiungi GEMINI_API_KEY nei Secrets di Streamlit."
        )

    # -----------------------------------------
    # STORICO CHAT
    # -----------------------------------------

    for messaggio in st.session_state.messaggi_chat:

        with st.chat_message(
            messaggio["ruolo"]
        ):

            st.markdown(
                messaggio["contenuto"]
            )

    # -----------------------------------------
    # INPUT UTENTE
    # -----------------------------------------

    domanda = st.chat_input(
        "Scrivi qui la tua domanda..."
    )

    if domanda:

        st.session_state.messaggi_chat.append({
            "ruolo": "user",
            "contenuto": domanda
        })

        with st.chat_message("user"):

            st.markdown(domanda)

        with st.chat_message("assistant"):

            with st.spinner(
                "Sto analizzando i dati..."
            ):

                risposta = invia_a_gemini(
                    domanda
                )

            st.markdown(risposta)

        st.session_state.messaggi_chat.append({
            "ruolo": "assistant",
            "contenuto": risposta
        })


# ============================================================
# SIDEBAR - INFORMAZIONI
# ============================================================

st.sidebar.divider()

st.sidebar.caption(
    "🧪 Versione di prova\n"
    "I dati sono temporanei e verranno persi "
    "quando la sessione viene riavviata."
)
