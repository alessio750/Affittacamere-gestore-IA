import io
import os
import re
import tempfile
from datetime import date, datetime

import pandas as pd
import pdfplumber
import streamlit as st
from google import genai

# =========================================================
# CONFIGURAZIONE
# =========================================================
st.set_page_config(
    page_title="Gestione Affittacamere IA",
    page_icon="🛏️",
    layout="wide",
)

MODELLI_GEMINI = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
]
MAX_DOCUMENTI_IA = 6
MAX_CARATTERI_PER_DOC = 12000

CAMERE_DEFAULT = [
    "Baia di Budoni",
    "La Cinta",
    "Cala Brandinchi",
    "Capo Comino",
]

# =========================================================
# MEMORIA TEMPORANEA DELLA SESSIONE
# =========================================================
if "prenotazioni" not in st.session_state:
    st.session_state.prenotazioni = []

if "camere_stato" not in st.session_state:
    st.session_state.camere_stato = {
        camera: {"stato": "Disponibile", "ospite": "-"}
        for camera in CAMERE_DEFAULT
    }

if "documenti_caricati" not in st.session_state:
    st.session_state.documenti_caricati = []

if "messaggi_chat" not in st.session_state:
    st.session_state.messaggi_chat = [
        {
            "ruolo": "assistant",
            "contenuto": (
                "Ciao! 👋 Sono l'assistente IA dell'affittacamere. "
                "Puoi farmi domande sulle camere, sulle prenotazioni e sui documenti caricati. "
                "Puoi scrivere normalmente, proprio come con ChatGPT o Gemini."
            ),
        }
    ]

if "prenotazione_da_modificare" not in st.session_state:
    st.session_state.prenotazione_da_modificare = None

# =========================================================
# FUNZIONI UTILI
# =========================================================
def get_gemini_client():
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "La chiave GEMINI_API_KEY non è presente nei Secrets di Streamlit."
        )
    return genai.Client(api_key=api_key)


def normalizza_testo(testo):
    return re.sub(r"\s+", " ", str(testo or "")).strip().lower()


def aggiorna_stato_camere():
    """Ricostruisce lo stato delle camere usando le prenotazioni in corso oggi."""
    oggi = date.today()
    for camera in st.session_state.camere_stato:
        st.session_state.camere_stato[camera] = {
            "stato": "Disponibile",
            "ospite": "-",
        }

    for p in st.session_state.prenotazioni:
        try:
            arrivo = pd.to_datetime(p["Arrivo"]).date()
            partenza = pd.to_datetime(p["Partenza"]).date()
            if arrivo <= oggi < partenza:
                camera = p["Camera"]
                if camera in st.session_state.camere_stato:
                    st.session_state.camere_stato[camera] = {
                        "stato": "Occupata",
                        "ospite": p["Ospite"],
                    }
        except Exception:
            pass


def stato_prenotazione(prenotazione):
    oggi = date.today()
    try:
        arrivo = pd.to_datetime(prenotazione["Arrivo"]).date()
        partenza = pd.to_datetime(prenotazione["Partenza"]).date()
        if oggi < arrivo:
            return "Futura"
        if arrivo <= oggi < partenza:
            return "Attiva"
        return "Terminata"
    except Exception:
        return "Non definita"


def documento_gia_presente(nome, dimensione):
    for doc in st.session_state.documenti_caricati:
        if doc["nome"] == nome and doc.get("dimensione", 0) == dimensione:
            return True
    return False


def leggi_file_caricato(file_caricato):
    nome = file_caricato.name
    estensione = nome.lower().split(".")[-1]
    bytes_file = file_caricato.getvalue()

    if estensione == "csv":
        df = pd.read_csv(io.BytesIO(bytes_file))
        contenuto = df.to_string(index=False)
        tipo = "CSV"
        anteprima = df.head(20)
        return tipo, contenuto, bytes_file, anteprima

    if estensione in ("xls", "xlsx"):
        df = pd.read_excel(io.BytesIO(bytes_file))
        contenuto = df.to_string(index=False)
        tipo = "Excel"
        anteprima = df.head(20)
        return tipo, contenuto, bytes_file, anteprima

    if estensione == "pdf":
        testo_completo = ""
        try:
            with pdfplumber.open(io.BytesIO(bytes_file)) as pdf:
                for pagina in pdf.pages:
                    testo = pagina.extract_text()
                    if testo:
                        testo_completo += testo + "\n"
        except Exception:
            testo_completo = ""

        tipo = "PDF"
        contenuto = testo_completo.strip()
        return tipo, contenuto, bytes_file, None

    raise ValueError("Formato file non supportato.")


def crea_contesto_gestionale(documenti_selezionati=None):
    aggiorna_stato_camere()
    righe = []

    righe.append("=== STATO CAMERE ===")
    for camera, info in st.session_state.camere_stato.items():
        righe.append(
            f"- {camera}: {info['stato']} | Ospite attuale: {info['ospite']}"
        )

    righe.append("\n=== PRENOTAZIONI ===")
    if st.session_state.prenotazioni:
        for i, p in enumerate(st.session_state.prenotazioni, start=1):
            righe.append(
                f"{i}. {p['Ospite']} | Camera: {p['Camera']} | "
                f"Arrivo: {p['Arrivo']} | Partenza: {p['Partenza']} | "
                f"Prezzo: €{float(p['Prezzo (€)']):.2f} | Stato: {stato_prenotazione(p)}"
            )
    else:
        righe.append("Nessuna prenotazione registrata.")

    docs = documenti_selezionati or []
    righe.append("\n=== DOCUMENTI RILEVANTI ===")
    if docs:
        for doc in docs:
            testo = doc.get("contenuto", "")
            if testo:
                testo = testo[:MAX_CARATTERI_PER_DOC]
                righe.append(
                    f"\n--- Documento: {doc['nome']} ({doc['tipo']}) ---\n{testo}"
                )
            else:
                righe.append(
                    f"\n--- Documento: {doc['nome']} ({doc['tipo']}) ---\n"
                    "Il PDF non contiene testo estraibile; se necessario verrà inviato direttamente a Gemini."
                )
    else:
        righe.append("Nessun documento selezionato per questa domanda.")

    return "\n".join(righe)


def seleziona_documenti_rilevanti(domanda):
    docs = st.session_state.documenti_caricati
    if not docs:
        return []

    parole = {
        p
        for p in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", normalizza_testo(domanda))
        if len(p) >= 3
    }

    punteggi = []
    for indice, doc in enumerate(docs):
        testo_ricerca = normalizza_testo(
            f"{doc.get('nome', '')} {doc.get('tipo', '')} {doc.get('contenuto', '')[:8000]}"
        )
        punteggio = sum(1 for parola in parole if parola in testo_ricerca)
        # Un piccolo vantaggio ai documenti più recenti.
        recenza = indice / max(len(docs), 1)
        punteggi.append((punteggio + recenza * 0.05, indice, doc))

    punteggi.sort(key=lambda x: x[0], reverse=True)
    selezionati = [x[2] for x in punteggi[:MAX_DOCUMENTI_IA] if x[0] > 0]

    # Se la domanda è generica sui documenti e nessuno ha matchato,
    # usa gli ultimi documenti caricati.
    if not selezionati and any(
        parola in normalizza_testo(domanda)
        for parola in ["document", "fattur", "file", "pdf", "excel", "spes", "incass"]
    ):
        selezionati = docs[-MAX_DOCUMENTI_IA:]

    return selezionati


def prepara_pdf_per_gemini(client, documenti):
    """Carica su Gemini soltanto i PDF selezionati che non hanno testo estraibile."""
    file_gemini = []
    temp_paths = []

    for doc in documenti:
        if doc.get("tipo") == "PDF" and not doc.get("contenuto", "").strip():
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".pdf"
                ) as temp_pdf:
                    temp_pdf.write(doc.get("bytes", b""))
                    temp_path = temp_pdf.name
                    temp_paths.append(temp_path)

                uploaded = client.files.upload(file=temp_path)
                file_gemini.append(uploaded)
            except Exception:
                pass

    return file_gemini, temp_paths


def invia_a_gemini(domanda):
    documenti = seleziona_documenti_rilevanti(domanda)
    contesto = crea_contesto_gestionale(documenti)

    istruzioni = f"""
Sei l'assistente amministrativo e gestionale di un affittacamere italiano.
Rispondi sempre in italiano, in modo chiaro, pratico e professionale.

REGOLE IMPORTANTI:
- Usa i dati del contesto qui sotto e gli eventuali PDF allegati.
- Non inventare nomi, importi, prenotazioni, fatture o dati mancanti.
- Se un dato non è disponibile, dillo chiaramente.
- Per somme, confronti e calcoli mostra il risultato in modo semplice.
- Se analizzi fatture o spese, evidenzia importi, scadenze, fornitori e possibili anomalie quando presenti.
- Puoi dare suggerimenti gestionali, ma non presentare consulenza fiscale o legale come definitiva.
- Se la domanda dell'utente è generale, puoi rispondere normalmente anche senza usare documenti.
- Non dire di aver letto documenti che non sono presenti nel contesto o allegati.

CONTESTO GESTIONALE:
{contesto}

DOMANDA DELL'UTENTE:
{domanda}
"""

    client = get_gemini_client()
    errori = []

    for modello in MODELLI_GEMINI:
        temp_paths = []
        try:
            file_gemini, temp_paths = prepara_pdf_per_gemini(client, documenti)
            contents = [istruzioni] + file_gemini
            response = client.models.generate_content(
                model=modello,
                contents=contents,
            )
            testo = getattr(response, "text", None)
            if testo:
                return testo, modello
            errori.append(f"{modello}: risposta vuota")
        except Exception as e:
            errori.append(f"{modello}: {str(e)[:250]}")
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except Exception:
                    pass

    raise RuntimeError(
        "In questo momento i modelli Gemini non stanno rispondendo. "
        "Riprova tra poco. Dettaglio tecnico: " + " | ".join(errori)
    )


def genera_backup_excel():
    output = io.BytesIO()
    aggiorna_stato_camere()

    df_prenotazioni = pd.DataFrame(st.session_state.prenotazioni)
    if not df_prenotazioni.empty:
        df_prenotazioni["Stato"] = [
            stato_prenotazione(p) for p in st.session_state.prenotazioni
        ]

    df_camere = pd.DataFrame(
        [
            {
                "Camera": camera,
                "Stato": info["stato"],
                "Ospite Attuale": info["ospite"],
            }
            for camera, info in st.session_state.camere_stato.items()
        ]
    )

    df_documenti = pd.DataFrame(
        [
            {
                "Nome file": doc["nome"],
                "Tipo": doc["tipo"],
                "Dimensione (KB)": round(doc.get("dimensione", 0) / 1024, 2),
                "Caricato il": doc.get("caricato_il", ""),
            }
            for doc in st.session_state.documenti_caricati
        ]
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if df_prenotazioni.empty:
            pd.DataFrame(columns=["Ospite", "Camera", "Arrivo", "Partenza", "Prezzo (€)", "Stato"]).to_excel(
                writer, sheet_name="Prenotazioni", index=False
            )
        else:
            df_prenotazioni.to_excel(writer, sheet_name="Prenotazioni", index=False)

        df_camere.to_excel(writer, sheet_name="Stato camere", index=False)

        if df_documenti.empty:
            pd.DataFrame(columns=["Nome file", "Tipo", "Dimensione (KB)", "Caricato il"]).to_excel(
                writer, sheet_name="Archivio documenti", index=False
            )
        else:
            df_documenti.to_excel(writer, sheet_name="Archivio documenti", index=False)

    output.seek(0)
    return output.getvalue()


# =========================================================
# TESTATA E MENU
# =========================================================
aggiorna_stato_camere()

st.title("🛏️ Sistema Gestione Affittacamere con IA")
st.write("Gestionale completo con archivio documenti, prenotazioni e assistente IA.")

menu = st.sidebar.selectbox(
    "Menu di Navigazione",
    [
        "🏠 Panoramica Camere",
        "➕ Nuova Prenotazione",
        "📋 Elenco Prenotazioni",
        "📂 Archivio e Analisi File",
        "💾 Backup Dati",
        "💬 Chat IA Assistente",
    ],
)

st.sidebar.caption("💡 I dati sono temporanei e restano nella sessione corrente dell'app.")

# =========================================================
# PANORAMICA CAMERE
# =========================================================
if menu == "🏠 Panoramica Camere":
    st.header("Stato Attuale delle Camere")

    dati_tabella = [
        {
            "Camera": camera,
            "Stato": info["stato"],
            "Ospite Attuale": info["ospite"],
        }
        for camera, info in st.session_state.camere_stato.items()
    ]
    st.dataframe(pd.DataFrame(dati_tabella), use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    disponibili = sum(
        1 for x in st.session_state.camere_stato.values() if x["stato"] == "Disponibile"
    )
    occupate = len(st.session_state.camere_stato) - disponibili
    col1.metric("Camere totali", len(st.session_state.camere_stato))
    col2.metric("Disponibili", disponibili)
    col3.metric("Occupate", occupate)

# =========================================================
# NUOVA PRENOTAZIONE
# =========================================================
elif menu == "➕ Nuova Prenotazione":
    st.header("Registra una Nuova Prenotazione")

    with st.form("form_prenotazione", clear_on_submit=True):
        nome_ospite = st.text_input("Nome e Cognome Ospite")
        camera_scelta = st.selectbox(
            "Seleziona Camera", list(st.session_state.camere_stato.keys())
        )
        data_arrivo = st.date_input("Data di Arrivo")
        data_partenza = st.date_input("Data di Partenza")
        prezzo_totale = st.number_input(
            "Prezzo Totale (€)", min_value=0.0, format="%.2f"
        )
        pulsante_salva = st.form_submit_button("Salva e Registra Prenotazione")

    if pulsante_salva:
        if not nome_ospite.strip():
            st.error("Inserisci il nome dell'ospite.")
        elif data_partenza <= data_arrivo:
            st.error("La data di partenza deve essere successiva alla data di arrivo.")
        else:
            st.session_state.prenotazioni.append(
                {
                    "Ospite": nome_ospite.strip(),
                    "Camera": camera_scelta,
                    "Arrivo": str(data_arrivo),
                    "Partenza": str(data_partenza),
                    "Prezzo (€)": float(prezzo_totale),
                }
            )
            aggiorna_stato_camere()
            st.success(f"Prenotazione registrata per {nome_ospite.strip()}!")

# =========================================================
# ELENCO PRENOTAZIONI
# =========================================================
elif menu == "📋 Elenco Prenotazioni":
    st.header("Storico Prenotazioni")

    if not st.session_state.prenotazioni:
        st.info("Nessuna prenotazione registrata in questa sessione.")
    else:
        col1, col2, col3 = st.columns([2, 1, 1])
        ricerca = col1.text_input("🔎 Cerca ospite", placeholder="Es. Mario Rossi")
        filtro_stato = col2.selectbox(
            "Stato", ["Tutte", "Attiva", "Futura", "Terminata"]
        )
        filtro_camera = col3.selectbox(
            "Camera", ["Tutte"] + list(st.session_state.camere_stato.keys())
        )

        righe = []
        indici_visibili = []
        for indice, p in enumerate(st.session_state.prenotazioni):
            stato = stato_prenotazione(p)
            if ricerca and ricerca.lower() not in p["Ospite"].lower():
                continue
            if filtro_stato != "Tutte" and stato != filtro_stato:
                continue
            if filtro_camera != "Tutte" and p["Camera"] != filtro_camera:
                continue

            riga = dict(p)
            riga["Stato"] = stato
            righe.append(riga)
            indici_visibili.append(indice)

        if righe:
            st.dataframe(pd.DataFrame(righe), use_container_width=True, hide_index=True)
        else:
            st.info("Nessuna prenotazione corrisponde ai filtri selezionati.")

        st.subheader("Modifica o elimina una prenotazione")
        opzioni = {
            f"{p['Ospite']} — {p['Camera']} — {p['Arrivo']} → {p['Partenza']}": i
            for i, p in enumerate(st.session_state.prenotazioni)
        }
        scelta = st.selectbox("Seleziona prenotazione", list(opzioni.keys()))
        indice_scelto = opzioni[scelta]
        p = st.session_state.prenotazioni[indice_scelto]

        col_mod, col_del = st.columns(2)
        if col_mod.button("✏️ Modifica prenotazione", use_container_width=True):
            st.session_state.prenotazione_da_modificare = indice_scelto

        if col_del.button("🗑️ Elimina prenotazione", use_container_width=True):
            st.session_state.prenotazioni.pop(indice_scelto)
            st.session_state.prenotazione_da_modificare = None
            aggiorna_stato_camere()
            st.success("Prenotazione eliminata.")
            st.rerun()

        if st.session_state.prenotazione_da_modificare is not None:
            idx = st.session_state.prenotazione_da_modificare
            if idx < len(st.session_state.prenotazioni):
                corrente = st.session_state.prenotazioni[idx]
                st.markdown("#### ✏️ Modifica dati")
                with st.form("form_modifica_prenotazione"):
                    nuovo_nome = st.text_input("Nome e Cognome", value=corrente["Ospite"])
                    camere = list(st.session_state.camere_stato.keys())
                    nuova_camera = st.selectbox(
                        "Camera",
                        camere,
                        index=camere.index(corrente["Camera"]),
                    )
                    nuovo_arrivo = st.date_input(
                        "Arrivo", value=pd.to_datetime(corrente["Arrivo"]).date()
                    )
                    nuova_partenza = st.date_input(
                        "Partenza", value=pd.to_datetime(corrente["Partenza"]).date()
                    )
                    nuovo_prezzo = st.number_input(
                        "Prezzo Totale (€)",
                        min_value=0.0,
                        value=float(corrente["Prezzo (€)"]),
                        format="%.2f",
                    )
                    salva_modifica = st.form_submit_button("💾 Salva modifiche")

                if salva_modifica:
                    if not nuovo_nome.strip():
                        st.error("Inserisci il nome dell'ospite.")
                    elif nuova_partenza <= nuovo_arrivo:
                        st.error("La partenza deve essere successiva all'arrivo.")
                    else:
                        st.session_state.prenotazioni[idx] = {
                            "Ospite": nuovo_nome.strip(),
                            "Camera": nuova_camera,
                            "Arrivo": str(nuovo_arrivo),
                            "Partenza": str(nuova_partenza),
                            "Prezzo (€)": float(nuovo_prezzo),
                        }
                        st.session_state.prenotazione_da_modificare = None
                        aggiorna_stato_camere()
                        st.success("Prenotazione aggiornata.")
                        st.rerun()

# =========================================================
# ARCHIVIO FILE
# =========================================================
elif menu == "📂 Archivio e Analisi File":
    st.header("Archivio e Analisi File")
    st.info(
        "Puoi selezionare molti file insieme. L'app accetta CSV, Excel e PDF. "
        "I documenti restano nella memoria temporanea della sessione."
    )

    files_caricati = st.file_uploader(
        "Carica documenti",
        type=["csv", "xls", "xlsx", "pdf"],
        accept_multiple_files=True,
    )

    if files_caricati:
        aggiunti = 0
        duplicati = 0
        errori = []
        barra = st.progress(0)

        for numero, file_caricato in enumerate(files_caricati, start=1):
            try:
                dimensione = len(file_caricato.getvalue())
                if documento_gia_presente(file_caricato.name, dimensione):
                    duplicati += 1
                else:
                    tipo, contenuto, bytes_file, _ = leggi_file_caricato(file_caricato)
                    st.session_state.documenti_caricati.append(
                        {
                            "nome": file_caricato.name,
                            "tipo": tipo,
                            "contenuto": contenuto,
                            "bytes": bytes_file,
                            "dimensione": dimensione,
                            "caricato_il": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        }
                    )
                    aggiunti += 1
            except Exception as e:
                errori.append(f"{file_caricato.name}: {e}")

            barra.progress(numero / len(files_caricati))

        if aggiunti:
            st.success(f"✅ Aggiunti {aggiunti} nuovi documenti all'archivio.")
        if duplicati:
            st.warning(f"↩️ Ignorati {duplicati} file già presenti.")
        if errori:
            with st.expander("Mostra errori di caricamento"):
                for errore in errori:
                    st.write(f"- {errore}")

    st.divider()

    col1, col2, col3 = st.columns([1, 1, 2])
    col1.metric("Documenti", len(st.session_state.documenti_caricati))
    spazio_mb = sum(
        doc.get("dimensione", 0) for doc in st.session_state.documenti_caricati
    ) / (1024 * 1024)
    col2.metric("Dimensione sessione", f"{spazio_mb:.1f} MB")

    ricerca_doc = col3.text_input(
        "🔎 Cerca nell'archivio", placeholder="Nome file, fattura, fornitore..."
    )

    if st.session_state.documenti_caricati:
        if st.button("🧹 Svuota tutto l'archivio", type="secondary"):
            st.session_state.documenti_caricati = []
            st.success("Archivio svuotato.")
            st.rerun()

        st.subheader("Documenti caricati")
        ricerca_norm = normalizza_testo(ricerca_doc)

        for indice, doc in list(enumerate(st.session_state.documenti_caricati)):
            testo_ricerca = normalizza_testo(
                f"{doc['nome']} {doc['tipo']} {doc.get('contenuto', '')[:4000]}"
            )
            if ricerca_norm and ricerca_norm not in testo_ricerca:
                continue

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
                c1.markdown(f"**📄 {doc['nome']}**")
                c1.caption(
                    f"{doc['tipo']} • {doc.get('dimensione', 0) / 1024:.1f} KB • "
                    f"{doc.get('caricato_il', '')}"
                )

                if c2.button("👁️ Anteprima", key=f"preview_{indice}"):
                    st.session_state[f"mostra_doc_{indice}"] = not st.session_state.get(
                        f"mostra_doc_{indice}", False
                    )

                if c3.button("🗑️ Elimina", key=f"delete_{indice}"):
                    st.session_state.documenti_caricati.pop(indice)
                    st.success(f"Eliminato: {doc['nome']}")
                    st.rerun()

                c4.write("")

                if st.session_state.get(f"mostra_doc_{indice}", False):
                    if doc.get("contenuto"):
                        st.text_area(
                            "Contenuto estratto",
                            doc["contenuto"][:12000],
                            height=220,
                            key=f"testo_{indice}",
                        )
                    else:
                        st.info(
                            "Questo PDF non contiene testo estraibile. "
                            "La Chat IA può comunque provare a leggerlo direttamente quando è rilevante."
                        )
    else:
        st.info("L'archivio è vuoto.")

    st.caption(
        "Nota: il caricamento multiplo permette di gestire molti documenti, ma Streamlit Cloud "
        "ha comunque limiti di memoria. Per un archivio realmente permanente e molto grande, "
        "in una fase successiva collegheremo un database/storage."
    )

# =========================================================
# BACKUP
# =========================================================
elif menu == "💾 Backup Dati":
    st.header("Backup ed esportazione")
    st.write(
        "Scarica un file Excel con le prenotazioni, lo stato delle camere e l'elenco dei documenti caricati."
    )

    backup = genera_backup_excel()
    nome_backup = f"backup_affittacamere_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"

    st.download_button(
        "📥 Scarica backup Excel",
        data=backup,
        file_name=nome_backup,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    st.caption(
        "Il backup contiene l'elenco dei documenti, ma non incorpora i PDF/Excel originali. "
        "I file originali restano nella sessione temporanea dell'app."
    )

# =========================================================
# CHAT IA
# =========================================================
elif menu == "💬 Chat IA Assistente":
    st.header("Assistente Virtuale IA dell'Affittacamere")
    st.write(
        "Chiedi qualsiasi cosa su camere, prenotazioni, incassi, spese, Excel e fatture caricate."
    )

    for messaggio in st.session_state.messaggi_chat:
        with st.chat_message(messaggio["ruolo"]):
            st.markdown(messaggio["contenuto"])

    input_utente = st.chat_input("Scrivi qui la tua domanda...")

    if input_utente:
        st.session_state.messaggi_chat.append(
            {"ruolo": "user", "contenuto": input_utente}
        )
        with st.chat_message("user"):
            st.markdown(input_utente)

        with st.chat_message("assistant"):
            with st.spinner("Sto analizzando i dati e preparando la risposta..."):
                try:
                    risposta, modello_usato = invia_a_gemini(input_utente)
                    st.markdown(risposta)
                    st.caption(f"IA: {modello_usato}")
                except Exception as e:
                    risposta = (
                        "❌ In questo momento Gemini non riesce a rispondere. "
                        "La tua chiave può essere corretta: potrebbe trattarsi di un sovraccarico "
                        "temporaneo o di un limite API. Riprova tra poco.\n\n"
                        f"Dettaglio: `{str(e)}`"
                    )
                    st.error(risposta)

        st.session_state.messaggi_chat.append(
            {"ruolo": "assistant", "contenuto": risposta}
        )
