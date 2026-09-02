import hashlib
import io
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

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
MAX_MESSAGGI_STORICO_IA = 10
VERSIONE_APP = "2.5 - Analisi parallela documenti"
MAX_ANALISI_PARALLELE = 4

CAMERE_DEFAULT = [
    "Baia di Budoni",
    "La Cinta",
    "Cala Brandinchi",
    "Capo Comino",
]

CATEGORIE_CONTABILI = [
    "Arredi",
    "Elettrodomestici",
    "Biancheria",
    "Pulizie",
    "Manutenzione",
    "Utenze",
    "Telefonia/Internet",
    "Software/Servizi online",
    "Commissioni portali",
    "Trasporti/Spedizioni",
    "Materiale di consumo",
    "Marketing/Pubblicità",
    "Professionisti",
    "Imposte/Tasse",
    "Altro",
]

# =========================================================
# MEMORIA TEMPORANEA DELLA SESSIONE
# =========================================================
def inizializza_sessione():
    if "prenotazioni" not in st.session_state:
        st.session_state.prenotazioni = []

    if "camere_stato" not in st.session_state:
        st.session_state.camere_stato = {
            camera: {"stato": "Disponibile", "ospite": "-"}
            for camera in CAMERE_DEFAULT
        }

    if "documenti_caricati" not in st.session_state:
        st.session_state.documenti_caricati = []

    if "contabilita" not in st.session_state:
        st.session_state.contabilita = []

    if "messaggi_chat" not in st.session_state:
        st.session_state.messaggi_chat = [
            {
                "ruolo": "assistant",
                "contenuto": (
                    "Ciao! 👋 Sono l'assistente IA dell'affittacamere. "
                    "Puoi farmi domande su camere, prenotazioni, documenti e contabilità. "
                    "I calcoli contabili principali vengono preparati da Python, mentre Gemini "
                    "si occupa di leggere e interpretare i documenti."
                ),
            }
        ]

    if "prenotazione_da_modificare" not in st.session_state:
        st.session_state.prenotazione_da_modificare = None

    if "riga_contabile_da_modificare" not in st.session_state:
        st.session_state.riga_contabile_da_modificare = None


inizializza_sessione()

# =========================================================
# FUNZIONI GENERALI
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


def hash_bytes(dati):
    # Import locale intenzionale: evita errori NameError anche in caso di rerun/cache anomali.
    import hashlib as _hashlib
    return _hashlib.sha256(dati).hexdigest()


def euro(valore):
    try:
        numero = float(valore)
    except Exception:
        numero = 0.0
    return f"€ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_numero(valore):
    """Converte importi italiani/internazionali in float in modo prudente."""
    if valore is None or valore == "":
        return 0.0
    if isinstance(valore, (int, float)):
        return float(valore)

    s = str(valore).strip().replace("€", "").replace(" ", "")
    if not s:
        return 0.0

    # 1.234,56 -> 1234.56 | 1234,56 -> 1234.56 | 1,234.56 -> 1234.56
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")

    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return 0.0


def aggiorna_stato_camere():
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

# =========================================================
# DOCUMENTI
# =========================================================
def documento_gia_presente(nome, dimensione, hash_file=None):
    for doc in st.session_state.documenti_caricati:
        if hash_file and doc.get("hash") == hash_file:
            return True
        if doc["nome"] == nome and doc.get("dimensione", 0) == dimensione:
            return True
    return False


def leggi_file_caricato(file_caricato):
    nome = file_caricato.name
    estensione = nome.lower().split(".")[-1]
    bytes_file = file_caricato.getvalue()

    if estensione == "csv":
        df = pd.read_csv(io.BytesIO(bytes_file))
        return "CSV", df.to_string(index=False), bytes_file

    if estensione in ("xls", "xlsx"):
        df = pd.read_excel(io.BytesIO(bytes_file))
        return "Excel", df.to_string(index=False), bytes_file

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

        return "PDF", testo_completo.strip(), bytes_file

    raise ValueError("Formato file non supportato.")


def elimina_documento(indice):
    doc = st.session_state.documenti_caricati[indice]
    doc_hash = doc.get("hash")
    nome = doc.get("nome")

    st.session_state.contabilita = [
        r
        for r in st.session_state.contabilita
        if not (
            (doc_hash and r.get("hash_documento") == doc_hash)
            or (not doc_hash and r.get("documento") == nome)
        )
    ]
    st.session_state.documenti_caricati.pop(indice)

# =========================================================
# CONTABILITÀ AUTOMATICA
# =========================================================
def trova_riga_contabile_per_doc(doc):
    doc_hash = doc.get("hash")
    for i, riga in enumerate(st.session_state.contabilita):
        if doc_hash and riga.get("hash_documento") == doc_hash:
            return i
    return None


def documento_da_analizzare(doc):
    """True solo se il documento non è mai stato analizzato con successo."""
    if trova_riga_contabile_per_doc(doc) is not None:
        return False
    return not bool(doc.get("analizzato_contabilita", False))


def estrai_json_da_testo(testo):
    testo = str(testo or "").strip()
    testo = re.sub(r"^```(?:json)?\s*", "", testo, flags=re.I)
    testo = re.sub(r"\s*```$", "", testo)

    match = re.search(r"\{.*\}", testo, flags=re.S)
    if not match:
        raise ValueError("Gemini non ha restituito un oggetto JSON leggibile.")
    return json.loads(match.group(0))


def prepara_file_gemini(client, doc):
    if not doc.get("bytes"):
        return None, None

    estensione = ".pdf" if doc.get("tipo") == "PDF" else ".bin"
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=estensione)
    temp.write(doc["bytes"])
    temp.close()
    uploaded = client.files.upload(file=temp.name)
    return uploaded, temp.name


def analizza_documento_contabile(doc, api_key=None):
    """Usa Gemini per trasformare un documento in dati contabili strutturati.

    api_key può essere passata dal thread principale: in questo modo le analisi
    parallele non devono leggere st.secrets dai thread secondari.
    """
    client = genai.Client(api_key=api_key) if api_key else get_gemini_client()

    prompt = f"""
Sei un estrattore di dati contabili per un affittacamere italiano.
Analizza UN SOLO documento e restituisci ESCLUSIVAMENTE un oggetto JSON valido, senza markdown e senza commenti.

Campi richiesti:
{{
  "e_documento_contabile": true/false,
  "tipo_documento": "Fattura|Ricevuta|Nota di credito|Altro",
  "fornitore": "stringa",
  "numero_documento": "stringa",
  "data_documento": "YYYY-MM-DD oppure stringa vuota",
  "categoria": "una categoria dell'elenco",
  "imponibile": 0.00,
  "iva": 0.00,
  "totale": 0.00,
  "aliquota_iva": "es. 22% oppure Mista oppure stringa vuota",
  "valuta": "EUR",
  "note": "breve nota utile"
}}

Categorie ammesse:
{', '.join(CATEGORIE_CONTABILI)}

Regole:
- Non inventare dati mancanti.
- Gli importi devono essere numeri JSON, senza simbolo euro.
- Se il documento contiene più aliquote IVA, usa aliquota_iva = "Mista".
- Se imponibile e IVA sono presenti, verifica che imponibile + IVA sia coerente con il totale.
- Se il documento non è una fattura/ricevuta/nota di credito o altro documento economico utile, imposta e_documento_contabile=false.
- In caso di nota di credito, usa importi negativi se il documento rappresenta uno storno.

Nome file: {doc.get('nome', '')}
Tipo file: {doc.get('tipo', '')}
"""

    testo = doc.get("contenuto", "").strip()
    if testo:
        prompt += f"\nTESTO ESTRATTO DAL DOCUMENTO:\n{testo[:30000]}"

    errori = []
    for modello in MODELLI_GEMINI:
        temp_path = None
        try:
            contents = [prompt]
            # Per PDF scansionati o con testo scarso, inviamo anche il file originale.
            if doc.get("tipo") == "PDF" and len(testo) < 150:
                uploaded, temp_path = prepara_file_gemini(client, doc)
                if uploaded is not None:
                    contents.append(uploaded)

            response = client.models.generate_content(model=modello, contents=contents)
            dati = estrai_json_da_testo(getattr(response, "text", ""))

            if not dati.get("e_documento_contabile", True):
                return None, modello

            categoria = str(dati.get("categoria", "Altro") or "Altro")
            if categoria not in CATEGORIE_CONTABILI:
                categoria = "Altro"

            imponibile = round(parse_numero(dati.get("imponibile")), 2)
            iva = round(parse_numero(dati.get("iva")), 2)
            totale = round(parse_numero(dati.get("totale")), 2)

            # Se uno dei tre campi manca ma gli altri due sono coerenti, ricaviamolo matematicamente.
            if totale == 0 and (imponibile != 0 or iva != 0):
                totale = round(imponibile + iva, 2)
            elif imponibile == 0 and totale != 0 and iva != 0:
                imponibile = round(totale - iva, 2)
            elif iva == 0 and totale != 0 and imponibile != 0:
                iva = round(totale - imponibile, 2)

            riga = {
                "documento": doc.get("nome", ""),
                "hash_documento": doc.get("hash", ""),
                "tipo_documento": str(dati.get("tipo_documento", "Altro") or "Altro"),
                "fornitore": str(dati.get("fornitore", "") or ""),
                "numero_documento": str(dati.get("numero_documento", "") or ""),
                "data_documento": str(dati.get("data_documento", "") or ""),
                "categoria": categoria,
                "imponibile": imponibile,
                "iva": iva,
                "totale": totale,
                "aliquota_iva": str(dati.get("aliquota_iva", "") or ""),
                "valuta": str(dati.get("valuta", "EUR") or "EUR"),
                "note": str(dati.get("note", "") or ""),
                "estratto_con": modello,
                "verificato": False,
                "estratto_il": datetime.now().strftime("%d/%m/%Y %H:%M"),
            }
            return riga, modello
        except Exception as e:
            errori.append(f"{modello}: {str(e)[:180]}")
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    raise RuntimeError(" | ".join(errori))


def analizza_documenti_non_elaborati(documenti=None, barra=None, stato_testo=None):
    """Analizza i documenti in parallelo, a piccoli gruppi, senza scrivere
    nello st.session_state dai thread secondari.

    Questo evita che un singolo PDF lento blocchi tutti gli altri e rende molto
    più rapido il caricamento di decine di fatture. Il limite di concorrenza
    resta volutamente prudente per non saturare le quote API di Gemini.
    """
    docs = documenti if documenti is not None else st.session_state.documenti_caricati
    da_analizzare = [d for d in docs if documento_da_analizzare(d)]

    risultati = {"aggiunti": 0, "non_contabili": 0, "errori": []}
    totale = len(da_analizzare)
    if totale == 0:
        return risultati

    api_key = st.secrets.get("GEMINI_API_KEY", "")
    if not api_key:
        risultati["errori"].append("La chiave GEMINI_API_KEY non è presente nei Secrets di Streamlit.")
        return risultati

    lavoratori = min(MAX_ANALISI_PARALLELE, totale)
    completati = 0

    if stato_testo is not None:
        stato_testo.write(
            f"🚀 Analisi parallela avviata: **{totale} documenti**, fino a **{lavoratori} contemporaneamente**."
        )

    def lavoro(doc):
        return doc, analizza_documento_contabile(doc, api_key=api_key)

    with ThreadPoolExecutor(max_workers=lavoratori, thread_name_prefix="fatture") as executor:
        future_map = {executor.submit(lavoro, doc): doc for doc in da_analizzare}

        for future in as_completed(future_map):
            doc = future_map[future]
            completati += 1
            try:
                _, (riga, _) = future.result()
                if riga is None:
                    doc["analizzato_contabilita"] = True
                    doc["esito_analisi"] = "Non contabile"
                    risultati["non_contabili"] += 1
                else:
                    # La modifica dello stato Streamlit avviene solo nel thread principale.
                    st.session_state.contabilita.append(riga)
                    doc["analizzato_contabilita"] = True
                    doc["esito_analisi"] = "Inserito in Contabilità"
                    risultati["aggiunti"] += 1
            except Exception as e:
                risultati["errori"].append(f"{doc['nome']}: {e}")

            if barra is not None:
                barra.progress(completati / totale)
            if stato_testo is not None:
                ok = risultati["aggiunti"] + risultati["non_contabili"]
                err = len(risultati["errori"])
                stato_testo.write(
                    f"⚡ Completati **{completati}/{totale}** • riusciti **{ok}** • errori **{err}** • "
                    f"ultimo: **{doc['nome']}**"
                )

    return risultati


def dataframe_contabilita():
    colonne = [
        "Data",
        "Fornitore",
        "Numero documento",
        "Categoria",
        "Imponibile (€)",
        "IVA (€)",
        "Totale (€)",
        "Aliquota IVA",
        "Documento",
        "Verificato",
    ]
    if not st.session_state.contabilita:
        return pd.DataFrame(columns=colonne)

    righe = []
    for r in st.session_state.contabilita:
        righe.append(
            {
                "Data": r.get("data_documento", ""),
                "Fornitore": r.get("fornitore", ""),
                "Numero documento": r.get("numero_documento", ""),
                "Categoria": r.get("categoria", "Altro"),
                "Imponibile (€)": round(parse_numero(r.get("imponibile")), 2),
                "IVA (€)": round(parse_numero(r.get("iva")), 2),
                "Totale (€)": round(parse_numero(r.get("totale")), 2),
                "Aliquota IVA": r.get("aliquota_iva", ""),
                "Documento": r.get("documento", ""),
                "Verificato": "Sì" if r.get("verificato") else "No",
            }
        )
    return pd.DataFrame(righe, columns=colonne)


def riepilogo_contabile_python(righe=None):
    righe = st.session_state.contabilita if righe is None else righe
    imponibile = round(sum(parse_numero(r.get("imponibile")) for r in righe), 2)
    iva = round(sum(parse_numero(r.get("iva")) for r in righe), 2)
    totale = round(sum(parse_numero(r.get("totale")) for r in righe), 2)
    return {
        "numero_documenti": len(righe),
        "imponibile": imponibile,
        "iva": iva,
        "totale": totale,
    }


def crea_testo_contabilita_per_ia():
    if not st.session_state.contabilita:
        return "Nessun dato contabile strutturato disponibile."

    riepilogo = riepilogo_contabile_python()
    righe = [
        "=== CONTABILITÀ STRUTTURATA (CALCOLI ESEGUITI DA PYTHON) ===",
        f"Numero documenti contabili: {riepilogo['numero_documenti']}",
        f"Imponibile totale: {riepilogo['imponibile']:.2f} EUR",
        f"IVA totale: {riepilogo['iva']:.2f} EUR",
        f"Totale complessivo: {riepilogo['totale']:.2f} EUR",
    ]

    df = dataframe_contabilita()
    if not df.empty:
        per_fornitore = (
            df.groupby("Fornitore", dropna=False)["Totale (€)"]
            .sum()
            .sort_values(ascending=False)
        )
        righe.append("\nTotali per fornitore:")
        for nome, valore in per_fornitore.head(25).items():
            righe.append(f"- {nome or 'Non specificato'}: {float(valore):.2f} EUR")

        per_categoria = (
            df.groupby("Categoria", dropna=False)["Totale (€)"]
            .sum()
            .sort_values(ascending=False)
        )
        righe.append("\nTotali per categoria:")
        for nome, valore in per_categoria.items():
            righe.append(f"- {nome}: {float(valore):.2f} EUR")

        righe.append("\nDettaglio documenti:")
        for _, r in df.tail(120).iterrows():
            righe.append(
                f"- {r['Data']} | {r['Fornitore']} | {r['Numero documento']} | "
                f"{r['Categoria']} | Imponibile {r['Imponibile (€)']:.2f} | "
                f"IVA {r['IVA (€)']:.2f} | Totale {r['Totale (€)']:.2f} EUR | "
                f"File: {r['Documento']}"
            )

    return "\n".join(righe)

# =========================================================
# CONTESTO E CHAT IA
# =========================================================
def seleziona_documenti_rilevanti(domanda):
    docs = st.session_state.documenti_caricati
    if not docs:
        return []

    domanda_norm = normalizza_testo(domanda)

    # Riferimenti temporali semplici: "ultima fattura", "ultimo documento".
    if any(frase in domanda_norm for frase in ["ultima fattura", "ultimo documento", "ultimo file", "appena caricato"]):
        return docs[-1:]

    parole = {
        p
        for p in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", domanda_norm)
        if len(p) >= 3
    }

    punteggi = []
    for indice, doc in enumerate(docs):
        testo_ricerca = normalizza_testo(
            f"{doc.get('nome', '')} {doc.get('tipo', '')} {doc.get('contenuto', '')[:8000]}"
        )
        punteggio = sum(1 for parola in parole if parola in testo_ricerca)
        recenza = indice / max(len(docs), 1)
        punteggi.append((punteggio + recenza * 0.05, indice, doc))

    punteggi.sort(key=lambda x: x[0], reverse=True)
    selezionati = [x[2] for x in punteggi[:MAX_DOCUMENTI_IA] if x[0] > 0]

    if not selezionati and any(
        parola in domanda_norm
        for parola in ["document", "fattur", "file", "pdf", "excel", "spes", "incass"]
    ):
        selezionati = docs[-MAX_DOCUMENTI_IA:]

    return selezionati


def prepara_pdf_per_gemini(client, documenti):
    file_gemini = []
    temp_paths = []

    for doc in documenti:
        if doc.get("tipo") == "PDF" and not doc.get("contenuto", "").strip():
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                    temp_pdf.write(doc.get("bytes", b""))
                    temp_path = temp_pdf.name
                    temp_paths.append(temp_path)
                file_gemini.append(client.files.upload(file=temp_path))
            except Exception:
                pass

    return file_gemini, temp_paths


def crea_contesto_gestionale(documenti_selezionati=None):
    aggiorna_stato_camere()
    righe = []

    righe.append("=== STATO CAMERE ===")
    for camera, info in st.session_state.camere_stato.items():
        righe.append(f"- {camera}: {info['stato']} | Ospite attuale: {info['ospite']}")

    righe.append("\n=== PRENOTAZIONI ===")
    if st.session_state.prenotazioni:
        for i, p in enumerate(st.session_state.prenotazioni, start=1):
            righe.append(
                f"{i}. {p['Ospite']} | Camera: {p['Camera']} | Arrivo: {p['Arrivo']} | "
                f"Partenza: {p['Partenza']} | Prezzo: {float(p['Prezzo (€)']):.2f} EUR | "
                f"Stato: {stato_prenotazione(p)}"
            )
    else:
        righe.append("Nessuna prenotazione registrata.")

    righe.append("\n" + crea_testo_contabilita_per_ia())

    docs = documenti_selezionati or []
    righe.append("\n=== DOCUMENTI RILEVANTI PER LA DOMANDA ===")
    if docs:
        for doc in docs:
            testo = doc.get("contenuto", "")
            if testo:
                righe.append(
                    f"\n--- Documento: {doc['nome']} ({doc['tipo']}) ---\n"
                    f"{testo[:MAX_CARATTERI_PER_DOC]}"
                )
            else:
                righe.append(
                    f"\n--- Documento: {doc['nome']} ({doc['tipo']}) ---\n"
                    "PDF senza testo estraibile: può essere allegato direttamente a Gemini."
                )
    else:
        righe.append("Nessun documento specifico selezionato per questa domanda.")

    return "\n".join(righe)


def storico_chat_per_ia():
    messaggi = st.session_state.messaggi_chat[-MAX_MESSAGGI_STORICO_IA:]
    righe = []
    for m in messaggi:
        ruolo = "UTENTE" if m.get("ruolo") == "user" else "ASSISTENTE"
        righe.append(f"{ruolo}: {str(m.get('contenuto', ''))[:2500]}")
    return "\n".join(righe)


def formatta_euro(valore):
    testo = f"{float(valore):,.2f}"
    testo = testo.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {testo}"


def risposta_contabile_python(domanda):
    """Risponde senza Gemini a molte domande numeriche/contabili sui dati strutturati.

    Gemini resta necessario per interpretazioni, consigli, cause, previsioni e analisi qualitative.
    """
    if not st.session_state.contabilita:
        return None

    q = normalizza_testo(domanda)
    segnali_contabili = [
        "spes", "totale", "iva", "imponibile", "fattur", "document",
        "fornitor", "categoria", "pagato", "pagata", "quanto", "quante", "quanti",
        "media", "percent", "piu cost", "meno cost", "superior", "inferior", "confront",
        "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"
    ]
    if not any(x in q for x in segnali_contabili):
        return None

    # Se la richiesta chiede giudizi o spiegazioni, lasciamo il lavoro a Gemini.
    segnali_interpretativi = [
        "analizza", "spiega", "consigli", "consiglio", "risparm", "anomali",
        "perche", "perché", "conviene", "preved", "strateg", "valuta",
        "cosa ne pensi", "come mai", "motivo", "sugger", "ottimizz"
    ]
    if any(x in q for x in segnali_interpretativi):
        return None

    df = dataframe_contabilita().copy()
    if df.empty:
        return None

    # Colonne numeriche sempre coerenti.
    for col in ["Imponibile (€)", "IVA (€)", "Totale (€)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    riepilogo = riepilogo_contabile_python()

    def risposta_python(titolo, righe_testo):
        if isinstance(righe_testo, str):
            righe_testo = [righe_testo]
        return (
            f"📊 **{titolo}**\n\n" + "\n".join(righe_testo) +
            "\n\n⚡ Risposta calcolata direttamente da Python sui dati della pagina Contabilità, senza chiamare Gemini."
        )

    def filtro_righe_per_nome(colonna, valori):
        trovati = []
        for valore in sorted(valori, key=len, reverse=True):
            if normalizza_testo(valore) in q:
                trovati.append(valore)
        # Evita duplicati mantenendo l'ordine.
        return list(dict.fromkeys(trovati))

    fornitori = [str(x) for x in df["Fornitore"].dropna().unique() if str(x).strip()]
    categorie = [str(x) for x in df["Categoria"].dropna().unique() if str(x).strip()]
    fornitori_trovati = filtro_righe_per_nome("Fornitore", fornitori)
    categorie_trovate = filtro_righe_per_nome("Categoria", categorie)

    # Soglia monetaria, es. "fatture superiori a 200 euro".
    soglia_match = re.search(r"(?:superior[ei]?|sopra|oltre|maggior[ei]? di|piu di|più di)\s*(?:a|di)?\s*€?\s*(\d+(?:[\.,]\d+)?)", q)
    if soglia_match and any(x in q for x in ["fattur", "document", "spes"]):
        soglia = float(soglia_match.group(1).replace(".", "").replace(",", "."))
        righe = df[df["Totale (€)"] > soglia].sort_values("Totale (€)", ascending=False)
        if righe.empty:
            return risposta_python("Filtro contabile (Python)", f"Non risultano documenti con totale superiore a **{formatta_euro(soglia)}**.")
        elenco = [f"Documenti con totale superiore a **{formatta_euro(soglia)}**: **{len(righe)}**"]
        for _, r in righe.head(20).iterrows():
            elenco.append(f"- **{r['Fornitore']}** — {r['Numero documento']} — **{formatta_euro(r['Totale (€)'])}**")
        if len(righe) > 20:
            elenco.append(f"- … e altri {len(righe)-20} documenti")
        return risposta_python("Filtro contabile (Python)", elenco)

    soglia_bassa_match = re.search(r"(?:inferior[ei]?|sotto|meno di)\s*(?:a|di)?\s*€?\s*(\d+(?:[\.,]\d+)?)", q)
    if soglia_bassa_match and any(x in q for x in ["fattur", "document", "spes"]):
        soglia = float(soglia_bassa_match.group(1).replace(".", "").replace(",", "."))
        righe = df[df["Totale (€)"] < soglia].sort_values("Totale (€)")
        if righe.empty:
            return risposta_python("Filtro contabile (Python)", f"Non risultano documenti con totale inferiore a **{formatta_euro(soglia)}**.")
        elenco = [f"Documenti con totale inferiore a **{formatta_euro(soglia)}**: **{len(righe)}**"]
        for _, r in righe.head(20).iterrows():
            elenco.append(f"- **{r['Fornitore']}** — {r['Numero documento']} — **{formatta_euro(r['Totale (€)'])}**")
        return risposta_python("Filtro contabile (Python)", elenco)

    # Top N fatture/documenti più costosi.
    # Gestisce anche domande composte, ad esempio:
    # "Quali sono le 2 fatture più costose e che percentuale rappresentano sul totale?"
    top_match = re.search(r"(?:le|i)?\s*(\d+)\s+(?:fattur\w*|document\w*)\s+(?:piu|più)\s+cost", q)
    if top_match or (any(x in q for x in ["fattura piu costosa", "fattura più costosa", "documento piu costoso", "documento più costoso"])):
        n = int(top_match.group(1)) if top_match else 1
        n = max(1, min(n, 20))
        righe = df.sort_values("Totale (€)", ascending=False).head(n)
        elenco = [f"Le **{len(righe)}** fatture/documenti più costosi sono:"]
        for pos, (_, r) in enumerate(righe.iterrows(), start=1):
            elenco.append(f"{pos}. **{r['Fornitore']}** — {r['Numero documento']} — **{formatta_euro(r['Totale (€)'])}**")

        # Se la stessa domanda chiede anche altri calcoli, li aggiungiamo nella stessa risposta.
        somma_top = float(righe["Totale (€)"].sum())
        iva_top = float(righe["IVA (€)"].sum())
        imponibile_top = float(righe["Imponibile (€)"].sum())
        percentuale_top = (somma_top / riepilogo["totale"] * 100) if riepilogo["totale"] else 0

        if any(x in q for x in ["insieme", "complessiv", "somma", "quanto valgono", "totale delle", "totale di queste"]):
            elenco.append(f"- Totale delle {len(righe)} fatture: **{formatta_euro(somma_top)}**")

        if "percent" in q or "incidenza" in q or "rappresentano sul totale" in q or "del totale" in q:
            elenco.append(
                f"- Incidenza sul totale complessivo: **{percentuale_top:.2f}%** "
                f"({formatta_euro(somma_top)} su {formatta_euro(riepilogo['totale'])})"
            )

        if "iva" in q:
            elenco.append(f"- IVA complessiva delle {len(righe)} fatture: **{formatta_euro(iva_top)}**")

        if "imponibile" in q:
            elenco.append(f"- Imponibile complessivo delle {len(righe)} fatture: **{formatta_euro(imponibile_top)}**")

        return risposta_python("Classifica fatture (Python)", elenco)

    if any(x in q for x in ["fattura meno costosa", "fattura piu economica", "fattura più economica", "documento meno costoso"]):
        r = df.sort_values("Totale (€)").iloc[0]
        return risposta_python(
            "Fattura meno costosa (Python)",
            f"La fattura/documento meno costoso è **{r['Fornitore']} — {r['Numero documento']}**, per **{formatta_euro(r['Totale (€)'])}**."
        )

    # Media fatture.
    if "media" in q and any(x in q for x in ["fattur", "document", "spes", "totale"]):
        media = float(df["Totale (€)"].mean())
        return risposta_python(
            "Media contabile (Python)",
            [f"- Numero documenti: **{len(df)}**", f"- Valore medio per documento: **{formatta_euro(media)}**"]
        )

    # Dati filtrati per fornitore o categoria, inclusa IVA/imponibile.
    if len(fornitori_trovati) == 1:
        fornitore = fornitori_trovati[0]
        righe = df[df["Fornitore"] == fornitore]
        tot = float(righe["Totale (€)"].sum())
        iva = float(righe["IVA (€)"].sum())
        imp = float(righe["Imponibile (€)"].sum())
        percentuale = (tot / riepilogo["totale"] * 100) if riepilogo["totale"] else 0
        return risposta_python(
            f"Dati fornitore: {fornitore} (Python)",
            [
                f"- Documenti: **{len(righe)}**",
                f"- Totale: **{formatta_euro(tot)}**",
                f"- Imponibile: **{formatta_euro(imp)}**",
                f"- IVA: **{formatta_euro(iva)}**",
                f"- Incidenza sulla spesa complessiva: **{percentuale:.1f}%**",
            ]
        )

    if len(categorie_trovate) == 1:
        categoria = categorie_trovate[0]
        righe = df[df["Categoria"] == categoria]
        tot = float(righe["Totale (€)"].sum())
        iva = float(righe["IVA (€)"].sum())
        imp = float(righe["Imponibile (€)"].sum())
        percentuale = (tot / riepilogo["totale"] * 100) if riepilogo["totale"] else 0
        return risposta_python(
            f"Categoria: {categoria} (Python)",
            [
                f"- Documenti: **{len(righe)}**",
                f"- Totale: **{formatta_euro(tot)}**",
                f"- Imponibile: **{formatta_euro(imp)}**",
                f"- IVA: **{formatta_euro(iva)}**",
                f"- Incidenza sulla spesa complessiva: **{percentuale:.1f}%**",
            ]
        )

    # Confronto numerico fra due fornitori o due categorie.
    if "confront" in q and len(fornitori_trovati) >= 2:
        a, b = fornitori_trovati[:2]
        ta = float(df[df["Fornitore"] == a]["Totale (€)"].sum())
        tb = float(df[df["Fornitore"] == b]["Totale (€)"].sum())
        diff = abs(ta - tb)
        maggiore = a if ta >= tb else b
        return risposta_python(
            "Confronto fornitori (Python)",
            [f"- **{a}**: {formatta_euro(ta)}", f"- **{b}**: {formatta_euro(tb)}", f"- Differenza: **{formatta_euro(diff)}**", f"- Spesa maggiore: **{maggiore}**"]
        )

    if "confront" in q and len(categorie_trovate) >= 2:
        a, b = categorie_trovate[:2]
        ta = float(df[df["Categoria"] == a]["Totale (€)"].sum())
        tb = float(df[df["Categoria"] == b]["Totale (€)"].sum())
        diff = abs(ta - tb)
        maggiore = a if ta >= tb else b
        return risposta_python(
            "Confronto categorie (Python)",
            [f"- **{a}**: {formatta_euro(ta)}", f"- **{b}**: {formatta_euro(tb)}", f"- Differenza: **{formatta_euro(diff)}**", f"- Categoria con spesa maggiore: **{maggiore}**"]
        )

    # Analisi per mese sulla colonna Data. Supporta date YYYY-MM-DD e formati leggibili da pandas.
    mesi = {
        "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
        "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    }
    mesi_trovati = [(nome, num) for nome, num in mesi.items() if nome in q]
    if mesi_trovati:
        date_parsed = pd.to_datetime(df["Data"], errors="coerce", dayfirst=True)
        anno_match = re.search(r"\b(20\d{2})\b", q)
        anno = int(anno_match.group(1)) if anno_match else None
        risultati_mesi = []
        for nome, num in mesi_trovati[:2]:
            mask = date_parsed.dt.month.eq(num)
            if anno:
                mask &= date_parsed.dt.year.eq(anno)
            tot = float(df.loc[mask, "Totale (€)"].sum())
            iva = float(df.loc[mask, "IVA (€)"].sum())
            count = int(mask.sum())
            risultati_mesi.append((nome, tot, iva, count))

        if len(risultati_mesi) == 2 and any(x in q for x in ["confront", "rispetto", "differenza", "aumento", "diminuzione"]):
            a, b = risultati_mesi
            diff = b[1] - a[1]
            perc = (diff / a[1] * 100) if a[1] else None
            righe_out = [
                f"- **{a[0].capitalize()}**: {formatta_euro(a[1])} ({a[3]} documenti)",
                f"- **{b[0].capitalize()}**: {formatta_euro(b[1])} ({b[3]} documenti)",
                f"- Differenza {b[0]} - {a[0]}: **{formatta_euro(diff)}**",
            ]
            if perc is not None:
                righe_out.append(f"- Variazione percentuale: **{perc:+.1f}%**")
            return risposta_python("Confronto mensile (Python)", righe_out)

        nome, tot, iva, count = risultati_mesi[0]
        return risposta_python(
            f"Riepilogo di {nome} (Python)",
            [f"- Documenti: **{count}**", f"- Totale spese: **{formatta_euro(tot)}**", f"- IVA: **{formatta_euro(iva)}**"]
        )

    chiede_categoria_top = (
        "categoria" in q and any(x in q for x in ["maggiore", "maggior", "piu", "più", "inciso", "alta", "costosa"])
    )
    chiede_fornitore_top = (
        "fornitor" in q and any(x in q for x in ["maggiore", "maggior", "piu", "più", "cost", "speso"])
    )
    chiede_totale = any(x in q for x in ["totale", "quanto abbiamo speso", "quanto ho speso", "spesa complessiva", "spese complessive"])
    chiede_iva = "iva" in q
    chiede_imponibile = "imponibile" in q
    chiede_numero = any(x in q for x in ["quante fatture", "quanti documenti", "numero fatture", "numero documenti"])

    parti = []
    if chiede_totale:
        parti.append(f"- Totale spese: **{formatta_euro(riepilogo['totale'])}**")
    if chiede_imponibile:
        parti.append(f"- Imponibile totale: **{formatta_euro(riepilogo['imponibile'])}**")
    if chiede_iva:
        parti.append(f"- IVA totale: **{formatta_euro(riepilogo['iva'])}**")
    if chiede_numero:
        parti.append(f"- Documenti contabili: **{riepilogo['numero_documenti']}**")
    if chiede_categoria_top:
        per_categoria = df.groupby("Categoria")["Totale (€)"].sum().sort_values(ascending=False)
        if not per_categoria.empty:
            nome = str(per_categoria.index[0])
            valore = float(per_categoria.iloc[0])
            percentuale = (valore / riepilogo["totale"] * 100) if riepilogo["totale"] else 0
            parti.append(f"- Categoria con maggiore spesa: **{nome}** ({formatta_euro(valore)}, **{percentuale:.1f}%** del totale)")
    if chiede_fornitore_top:
        per_fornitore = df.groupby("Fornitore")["Totale (€)"].sum().sort_values(ascending=False)
        if not per_fornitore.empty:
            nome = str(per_fornitore.index[0])
            valore = float(per_fornitore.iloc[0])
            parti.append(f"- Fornitore con maggiore spesa: **{nome}** ({formatta_euro(valore)})")

    if not parti:
        return None

    return risposta_python("Risposta dalla Contabilità (Python)", parti)


def invia_a_gemini(domanda):
    documenti = seleziona_documenti_rilevanti(domanda)
    contesto = crea_contesto_gestionale(documenti)
    storico = storico_chat_per_ia()

    istruzioni = f"""
Sei l'assistente amministrativo, gestionale e finanziario di un affittacamere italiano.
Rispondi sempre in italiano, in modo chiaro, pratico e professionale.

REGOLE IMPORTANTI:
- Usa i dati del contesto e gli eventuali PDF allegati.
- Non inventare nomi, importi, prenotazioni, fatture o dati mancanti.
- Se un dato non è disponibile, dillo chiaramente.
- I numeri presenti nella sezione CONTABILITÀ STRUTTURATA sono già stati calcolati da Python: per totali, IVA, imponibile, fornitori e categorie preferisci SEMPRE quei valori ai calcoli fatti mentalmente.
- Se l'utente chiede il dettaglio di una singola fattura, puoi usare il documento originale selezionato.
- Se trovi una possibile incoerenza tra documento originale e tabella contabile, segnalala invece di nasconderla.
- Puoi dare suggerimenti gestionali, ma non presentare consulenza fiscale o legale come definitiva.
- Mantieni il filo della conversazione usando lo storico recente quando è utile.

STORICO RECENTE DELLA CHAT:
{storico}

CONTESTO GESTIONALE:
{contesto}

DOMANDA DELL'UTENTE:
{domanda}
"""

    client = get_gemini_client()
    errori = []
    attese_retry = [0, 2, 5, 10]

    for tentativo, attesa in enumerate(attese_retry, start=1):
        if attesa:
            time.sleep(attesa)

        for modello in MODELLI_GEMINI:
            temp_paths = []
            try:
                file_gemini, temp_paths = prepara_pdf_per_gemini(client, documenti)
                contents = [istruzioni] + file_gemini
                response = client.models.generate_content(model=modello, contents=contents)
                testo = getattr(response, "text", None)
                if testo:
                    return testo, modello
                errori.append(f"tentativo {tentativo} - {modello}: risposta vuota")
            except Exception as e:
                msg = str(e)
                errori.append(f"tentativo {tentativo} - {modello}: {msg[:180]}")
                # 503/429 sono tipicamente temporanei: il ciclo farà retry/backoff.
            finally:
                for path in temp_paths:
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    raise RuntimeError(
        "Gemini è temporaneamente indisponibile anche dopo i tentativi automatici. "
        "I dati della Contabilità restano comunque disponibili. "
        "Ultimi errori: " + " | ".join(errori[-3:])
    )

# =========================================================
# BACKUP EXCEL
# =========================================================
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
                "Elaborato contabilità": "Sì" if trova_riga_contabile_per_doc(doc) is not None else "No",
            }
            for doc in st.session_state.documenti_caricati
        ]
    )

    df_contabilita = dataframe_contabilita()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if df_prenotazioni.empty:
            pd.DataFrame(
                columns=["Ospite", "Camera", "Arrivo", "Partenza", "Prezzo (€)", "Stato"]
            ).to_excel(writer, sheet_name="Prenotazioni", index=False)
        else:
            df_prenotazioni.to_excel(writer, sheet_name="Prenotazioni", index=False)

        df_camere.to_excel(writer, sheet_name="Stato camere", index=False)

        if df_documenti.empty:
            pd.DataFrame(
                columns=["Nome file", "Tipo", "Dimensione (KB)", "Caricato il", "Elaborato contabilità"]
            ).to_excel(writer, sheet_name="Archivio documenti", index=False)
        else:
            df_documenti.to_excel(writer, sheet_name="Archivio documenti", index=False)

        df_contabilita.to_excel(writer, sheet_name="Contabilita", index=False)

        riepilogo = riepilogo_contabile_python()
        pd.DataFrame(
            [
                {"Voce": "Numero documenti", "Valore": riepilogo["numero_documenti"]},
                {"Voce": "Imponibile totale", "Valore": riepilogo["imponibile"]},
                {"Voce": "IVA totale", "Valore": riepilogo["iva"]},
                {"Voce": "Totale complessivo", "Valore": riepilogo["totale"]},
            ]
        ).to_excel(writer, sheet_name="Riepilogo contabile", index=False)

    output.seek(0)
    return output.getvalue()

# =========================================================
# TESTATA E MENU
# =========================================================
aggiorna_stato_camere()

st.title("🛏️ Sistema Gestione Affittacamere con IA")
st.write("Gestionale con archivio documenti, contabilità automatica, prenotazioni e assistente IA.")

st.sidebar.caption(f"Versione: {VERSIONE_APP}")

menu = st.sidebar.selectbox(
    "Menu di Navigazione",
    [
        "🏠 Panoramica Camere",
        "➕ Nuova Prenotazione",
        "📋 Elenco Prenotazioni",
        "📂 Archivio e Analisi File",
        "📊 Contabilità",
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
        {"Camera": camera, "Stato": info["stato"], "Ospite Attuale": info["ospite"]}
        for camera, info in st.session_state.camere_stato.items()
    ]
    st.dataframe(pd.DataFrame(dati_tabella), use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    disponibili = sum(1 for x in st.session_state.camere_stato.values() if x["stato"] == "Disponibile")
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
        camera_scelta = st.selectbox("Seleziona Camera", list(st.session_state.camere_stato.keys()))
        data_arrivo = st.date_input("Data di Arrivo")
        data_partenza = st.date_input("Data di Partenza")
        prezzo_totale = st.number_input("Prezzo Totale (€)", min_value=0.0, format="%.2f")
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
        filtro_stato = col2.selectbox("Stato", ["Tutte", "Attiva", "Futura", "Terminata"])
        filtro_camera = col3.selectbox("Camera", ["Tutte"] + list(st.session_state.camere_stato.keys()))

        righe = []
        for p in st.session_state.prenotazioni:
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
                    nuova_camera = st.selectbox("Camera", camere, index=camere.index(corrente["Camera"]))
                    nuovo_arrivo = st.date_input("Arrivo", value=pd.to_datetime(corrente["Arrivo"]).date())
                    nuova_partenza = st.date_input("Partenza", value=pd.to_datetime(corrente["Partenza"]).date())
                    nuovo_prezzo = st.number_input(
                        "Prezzo Totale (€)", min_value=0.0,
                        value=float(corrente["Prezzo (€)"]), format="%.2f"
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
        "Puoi selezionare molti file insieme. Dopo il caricamento puoi farli elaborare dalla "
        "contabilità automatica: Gemini li legge una volta e Python userà poi i valori estratti per i calcoli."
    )

    files_caricati = st.file_uploader(
        "Carica documenti",
        type=["csv", "xls", "xlsx", "pdf"],
        accept_multiple_files=True,
    )

    nuovi_doc = []
    if files_caricati:
        aggiunti = 0
        duplicati = 0
        errori = []
        barra = st.progress(0)

        for numero, file_caricato in enumerate(files_caricati, start=1):
            try:
                bytes_originali = file_caricato.getvalue()
                dimensione = len(bytes_originali)
                impronta = hash_bytes(bytes_originali)
                if documento_gia_presente(file_caricato.name, dimensione, impronta):
                    duplicati += 1
                else:
                    tipo, contenuto, bytes_file = leggi_file_caricato(file_caricato)
                    doc = {
                        "nome": file_caricato.name,
                        "tipo": tipo,
                        "contenuto": contenuto,
                        "bytes": bytes_file,
                        "dimensione": dimensione,
                        "hash": impronta,
                        "caricato_il": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "analizzato_contabilita": False,
                        "esito_analisi": "Da analizzare",
                    }
                    st.session_state.documenti_caricati.append(doc)
                    nuovi_doc.append(doc)
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

    # CASSELLA BEN VISIBILE PER L'ANALISI CONTABILE
    da_elaborare = [d for d in st.session_state.documenti_caricati if documento_da_analizzare(d)]
    gia_elaborati = len(st.session_state.documenti_caricati) - len(da_elaborare)

    with st.container(border=True):
        st.subheader("🤖 Analisi contabile automatica")
        st.write(
            "Dopo aver caricato le fatture, premi il pulsante qui sotto. "
            "Gemini leggerà **solo i documenti nuovi**. La versione 2.5 li analizza **in parallelo a piccoli gruppi**, "
            "estrae i dati contabili e li passa alla pagina **📊 Contabilità**."
        )
        c_a, c_b, c_c = st.columns(3)
        c_a.metric("Da analizzare", len(da_elaborare))
        c_b.metric("Già analizzati", gia_elaborati)
        c_c.metric("Righe in Contabilità", len(st.session_state.contabilita))

        avvia_analisi = st.button(
            "🤖 ANALIZZA I NUOVI FILE",
            type="primary",
            use_container_width=True,
            disabled=len(da_elaborare) == 0,
        )

        if len(da_elaborare) == 0 and st.session_state.documenti_caricati:
            st.success("✅ Tutti i documenti presenti sono già stati analizzati.")
        elif len(da_elaborare) > 0:
            st.caption(
                f"Verranno analizzati {len(da_elaborare)} file, fino a {min(MAX_ANALISI_PARALLELE, len(da_elaborare))} contemporaneamente. "
                "Vedrai l'avanzamento man mano che ciascun documento termina."
            )

    if avvia_analisi:
        st.info("Analisi parallela in corso: non chiudere questa pagina fino al completamento. Puoi usare altre schede del browser.")
        barra_ia = st.progress(0)
        stato_ia = st.empty()
        risultati = analizza_documenti_non_elaborati(barra=barra_ia, stato_testo=stato_ia)
        stato_ia.empty()
        barra_ia.empty()
        if risultati["aggiunti"]:
            st.success(f"✅ {risultati['aggiunti']} fatture/documenti aggiunti alla pagina Contabilità.")
        if risultati["non_contabili"]:
            st.info(f"ℹ️ {risultati['non_contabili']} file sono stati letti ma non riconosciuti come documenti contabili.")
        if risultati["errori"]:
            st.warning(f"⚠️ {len(risultati['errori'])} documenti non sono stati elaborati e potranno essere riprovati.")
            with st.expander("Mostra errori di analisi"):
                for errore in risultati["errori"]:
                    st.write(f"- {errore}")
        st.rerun()

    col1, col2, col3 = st.columns([1, 1, 2])
    col1.metric("Documenti", len(st.session_state.documenti_caricati))
    spazio_mb = sum(doc.get("dimensione", 0) for doc in st.session_state.documenti_caricati) / (1024 * 1024)
    col2.metric("Dimensione sessione", f"{spazio_mb:.1f} MB")
    ricerca_doc = col3.text_input("🔎 Cerca nell'archivio", placeholder="Nome file, fattura, fornitore...")

    if st.session_state.documenti_caricati:
        if st.button("🧹 Svuota tutto l'archivio", type="secondary"):
            st.session_state.documenti_caricati = []
            st.session_state.contabilita = []
            st.success("Archivio e relativa contabilità svuotati.")
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
                c1, c2, c3 = st.columns([5, 1, 1])
                elaborato = trova_riga_contabile_per_doc(doc) is not None
                if elaborato:
                    badge = " • ✅ Contabilità"
                elif doc.get("analizzato_contabilita", False):
                    badge = " • ℹ️ Analizzato (non contabile)"
                else:
                    badge = " • ⏳ Da analizzare"
                c1.markdown(f"**📄 {doc['nome']}**")
                c1.caption(
                    f"{doc['tipo']} • {doc.get('dimensione', 0) / 1024:.1f} KB • "
                    f"{doc.get('caricato_il', '')}{badge}"
                )

                if c2.button("👁️ Anteprima", key=f"preview_{indice}"):
                    st.session_state[f"mostra_doc_{indice}"] = not st.session_state.get(f"mostra_doc_{indice}", False)

                if c3.button("🗑️ Elimina", key=f"delete_{indice}"):
                    elimina_documento(indice)
                    st.success(f"Eliminato: {doc['nome']}")
                    st.rerun()

                if st.session_state.get(f"mostra_doc_{indice}", False):
                    if doc.get("contenuto"):
                        st.text_area(
                            "Contenuto estratto", doc["contenuto"][:12000],
                            height=220, key=f"testo_{indice}"
                        )
                    else:
                        st.info(
                            "Questo PDF non contiene testo estraibile. Gemini può comunque leggerlo "
                            "direttamente durante l'analisi contabile o quando è rilevante in chat."
                        )
    else:
        st.info("L'archivio è vuoto.")

    st.caption(
        "Per molti documenti è meglio caricarli a gruppi e poi premere 'Analizza nuovi file'. "
        "L'analisi usa una chiamata IA per documento non ancora elaborato; in seguito i totali vengono calcolati da Python."
    )

# =========================================================
# CONTABILITÀ
# =========================================================
elif menu == "📊 Contabilità":
    st.header("📊 Contabilità automatica")
    st.write(
        "Qui trovi i dati estratti dalle fatture. Gemini interpreta ogni documento una volta; "
        "imponibile, IVA e totale vengono poi sommati matematicamente da Python."
    )

    if not st.session_state.contabilita:
        st.info(
            "Non ci sono ancora dati contabili. Vai in **📂 Archivio e Analisi File**, carica le fatture "
            "e premi **🤖 Analizza nuovi file**."
        )
    else:
        df = dataframe_contabilita()

        c1, c2, c3, c4 = st.columns(4)
        riepilogo = riepilogo_contabile_python()
        c1.metric("Documenti contabili", riepilogo["numero_documenti"])
        c2.metric("Imponibile totale", euro(riepilogo["imponibile"]))
        c3.metric("IVA totale", euro(riepilogo["iva"]))
        c4.metric("Totale spese", euro(riepilogo["totale"]))

        st.caption(
            "🧮 I quattro valori qui sopra sono calcolati da Python sui dati estratti, non generati liberamente dall'IA."
        )

        st.divider()
        f1, f2, f3 = st.columns([2, 1, 1])
        cerca_fornitore = f1.text_input("🔎 Cerca fornitore/documento", placeholder="Es. IKEA")
        categorie_presenti = sorted({r.get("categoria", "Altro") for r in st.session_state.contabilita})
        filtro_categoria = f2.selectbox("Categoria", ["Tutte"] + categorie_presenti)
        filtro_verifica = f3.selectbox("Verifica", ["Tutti", "Verificati", "Da verificare"])

        indici_filtrati = []
        righe_filtrate = []
        ricerca_norm = normalizza_testo(cerca_fornitore)
        for i, r in enumerate(st.session_state.contabilita):
            testo = normalizza_testo(
                f"{r.get('fornitore','')} {r.get('documento','')} {r.get('numero_documento','')}"
            )
            if ricerca_norm and ricerca_norm not in testo:
                continue
            if filtro_categoria != "Tutte" and r.get("categoria") != filtro_categoria:
                continue
            if filtro_verifica == "Verificati" and not r.get("verificato"):
                continue
            if filtro_verifica == "Da verificare" and r.get("verificato"):
                continue
            indici_filtrati.append(i)
            righe_filtrate.append(r)

        if righe_filtrate:
            df_filtro = dataframe_contabilita().iloc[indici_filtrati].reset_index(drop=True)
            st.dataframe(
                df_filtro,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Imponibile (€)": st.column_config.NumberColumn(format="€ %.2f"),
                    "IVA (€)": st.column_config.NumberColumn(format="€ %.2f"),
                    "Totale (€)": st.column_config.NumberColumn(format="€ %.2f"),
                },
            )

            r_filtro = riepilogo_contabile_python(righe_filtrate)
            st.caption(
                f"Totale filtrato: **{euro(r_filtro['totale'])}** • IVA: **{euro(r_filtro['iva'])}** • "
                f"Imponibile: **{euro(r_filtro['imponibile'])}**"
            )
        else:
            st.info("Nessuna riga corrisponde ai filtri selezionati.")

        st.divider()
        col_sx, col_dx = st.columns(2)

        with col_sx:
            st.subheader("Spesa per fornitore")
            per_fornitore = (
                df.groupby("Fornitore", dropna=False)["Totale (€)"]
                .sum()
                .sort_values(ascending=False)
                .reset_index()
            )
            st.dataframe(per_fornitore, use_container_width=True, hide_index=True)

        with col_dx:
            st.subheader("Spesa per categoria")
            per_categoria = (
                df.groupby("Categoria", dropna=False)["Totale (€)"]
                .sum()
                .sort_values(ascending=False)
                .reset_index()
            )
            st.dataframe(per_categoria, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("✏️ Controlla, correggi o elimina una riga")

        opzioni = {
            f"{i+1}. {r.get('fornitore') or 'Fornitore non indicato'} — {r.get('documento')} — {euro(r.get('totale'))}": i
            for i, r in enumerate(st.session_state.contabilita)
        }
        selezione = st.selectbox("Seleziona documento contabile", list(opzioni.keys()))
        idx = opzioni[selezione]
        corrente = st.session_state.contabilita[idx]

        with st.form("form_modifica_contabilita"):
            m1, m2 = st.columns(2)
            fornitore = m1.text_input("Fornitore", value=corrente.get("fornitore", ""))
            numero_doc = m2.text_input("Numero documento", value=corrente.get("numero_documento", ""))
            data_doc = m1.text_input("Data documento (YYYY-MM-DD)", value=corrente.get("data_documento", ""))
            cat_attuale = corrente.get("categoria", "Altro")
            categoria = m2.selectbox(
                "Categoria", CATEGORIE_CONTABILI,
                index=CATEGORIE_CONTABILI.index(cat_attuale) if cat_attuale in CATEGORIE_CONTABILI else CATEGORIE_CONTABILI.index("Altro")
            )
            imponibile = m1.number_input("Imponibile (€)", value=float(corrente.get("imponibile", 0.0)), format="%.2f")
            iva = m2.number_input("IVA (€)", value=float(corrente.get("iva", 0.0)), format="%.2f")
            totale = m1.number_input("Totale (€)", value=float(corrente.get("totale", 0.0)), format="%.2f")
            aliquota = m2.text_input("Aliquota IVA", value=corrente.get("aliquota_iva", ""))
            note = st.text_area("Note", value=corrente.get("note", ""))
            verificato = st.checkbox(
                "✅ Ho verificato i valori confrontandoli con il documento originale",
                value=bool(corrente.get("verificato", False)),
            )
            salva = st.form_submit_button("💾 Salva correzioni", use_container_width=True)

        if salva:
            corrente.update(
                {
                    "fornitore": fornitore.strip(),
                    "numero_documento": numero_doc.strip(),
                    "data_documento": data_doc.strip(),
                    "categoria": categoria,
                    "imponibile": round(float(imponibile), 2),
                    "iva": round(float(iva), 2),
                    "totale": round(float(totale), 2),
                    "aliquota_iva": aliquota.strip(),
                    "note": note.strip(),
                    "verificato": verificato,
                }
            )
            st.session_state.contabilita[idx] = corrente
            st.success("Dati contabili aggiornati.")
            st.rerun()

        if st.button("🗑️ Elimina solo questa riga contabile", type="secondary"):
            st.session_state.contabilita.pop(idx)
            st.success("Riga contabile eliminata. Il file originale resta nell'Archivio e potrà essere rianalizzato.")
            st.rerun()

# =========================================================
# BACKUP
# =========================================================
elif menu == "💾 Backup Dati":
    st.header("Backup ed esportazione")
    st.write(
        "Scarica un Excel con prenotazioni, stato camere, archivio documenti, contabilità e riepilogo contabile."
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
        "Il backup contiene i dati estratti e l'elenco dei documenti, ma non incorpora i PDF/Excel originali. "
        "I file originali restano nella memoria temporanea della sessione."
    )

# =========================================================
# CHAT IA
# =========================================================
elif menu == "💬 Chat IA Assistente":
    st.header("Assistente Virtuale IA dell'Affittacamere")
    st.write(
        "Chiedi qualsiasi cosa su camere, prenotazioni, incassi, spese, Excel, fatture e contabilità. "
        "Totali, filtri, classifiche e molti confronti contabili vengono risposti direttamente da Python; Gemini interviene quando serve interpretazione, spiegazione o consulenza."
    )

    for messaggio in st.session_state.messaggi_chat:
        with st.chat_message(messaggio["ruolo"]):
            st.markdown(messaggio["contenuto"])

    input_utente = st.chat_input("Scrivi qui la tua domanda...")

    if input_utente:
        st.session_state.messaggi_chat.append({"ruolo": "user", "contenuto": input_utente})
        with st.chat_message("user"):
            st.markdown(input_utente)

        with st.chat_message("assistant"):
            with st.spinner("Sto analizzando i dati e preparando la risposta..."):
                try:
                    risposta_python = risposta_contabile_python(input_utente)
                    if risposta_python:
                        risposta = risposta_python
                        st.markdown(risposta)
                        st.caption("🧮 Motore: Python • Gemini non utilizzato")
                    else:
                        risposta, modello_usato = invia_a_gemini(input_utente)
                        st.markdown(risposta)
                        st.caption(f"🤖 IA: {modello_usato} • Retry automatico attivo")
                except Exception as e:
                    risposta = (
                        "⚠️ Gemini è momentaneamente indisponibile anche dopo i tentativi automatici. "
                        "La pagina 📊 Contabilità e i calcoli Python continuano a funzionare normalmente. "
                        "Riprova tra poco per le richieste che richiedono l'IA."
                    )
                    st.error(risposta)

        st.session_state.messaggi_chat.append({"ruolo": "assistant", "contenuto": risposta})
