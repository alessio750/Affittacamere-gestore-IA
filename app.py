import streamlit as st
import pandas as pd
import pdfplumber
from datetime import datetime

# Configurazione della pagina
st.set_page_config(page_title="Gestione Affittacamere IA", page_icon="🛏️", layout="wide")

# Inizializziamo la memoria delle prenotazioni e delle camere se non esistono già
if "prenotazioni" not in st.session_state:
    st.session_state.prenotazioni = []

if "camere_stato" not in st.session_state:
    st.session_state.camere_stato = {
        "Camera 1 (Matrimoniale)": {"stato": "Disponibile", "ospite": "-"},
        "Camera 2 (Doppia)": {"stato": "Occupata", "ospite": "Mario Rossi"},
        "Camera 3 (Singola)": {"stato": "Disponibile", "ospite": "-"}
    }

st.title("🛏️ Sistema Gestione Affittacamere con IA")
st.write("Il cuore del gestionale: monitoraggio camere, prenotazioni attive e analisi documenti.")

# Menu laterale per navigare tra le sezioni
menu = st.sidebar.selectbox("Menu di Navigazione", [
    "🏠 Panoramica Camere", 
    "➕ Nuova Prenotazione", 
    "📋 Elenco Prenotazioni", 
    "📂 Archivio e Analisi File (Fase 5)"
])

if menu == "🏠 Panoramica Camere":
    st.header("Stato Attuale delle Camere")
    
    # Creiamo la tabella prendendo i dati aggiornati dalla memoria
    dati_tabella = []
    for camera, info in st.session_state.camere_stato.items():
        dati_tabella.append({
            "Camera": camera,
            "Stato": info["stato"],
            "Ospite Attuale": info["ospite"]
        })
    
    df_camere = pd.DataFrame(dati_tabella)
    st.dataframe(df_camere, use_container_width=True)

elif menu == "➕ Nuova Prenotazione":
    st.header("Registra una Nuova Prenotazione")
    
    with st.form("form_prenotazione"):
        nome_ospite = st.text_input("Nome e Cognome Ospite")
        camera_scelta = st.selectbox("Seleziona Camera", list(st.session_state.camere_stato.keys()))
        data_arrivo = st.date_input("Data di Arrivo")
        data_partenza = st.date_input("Data di Partenza")
        prezzo_totale = st.number_input("Prezzo Totale (€)", min_value=0.0, format="%.2f")
        
        pulsante_salva = st.form_submit_button("Salva e Registra Prenotazione")
        
        if pulsante_salva:
            if nome_ospite:
                # Salviamo la prenotazione nella lista
                nuova_prenotazione = {
                    "Ospite": nome_ospite,
                    "Camera": camera_scelta,
                    "Arrivo": str(data_arrivo),
                    "Partenza": str(data_partenza),
                    "Prezzo (€)": prezzo_totale
                }
                st.session_state.prenotazioni.append(nuova_prenotazione)
                
                # Aggiorniamo lo stato della camera automaticamente
                st.session_state.camere_stato[camera_scelta]["stato"] = "Occupata"
                st.session_state.camere_stato[camera_scelta]["ospite"] = nome_ospite
                
                st.success(f"Prenotazione registrata con successo per {nome_ospite}!")
            else:
                st.error("Per favore, inserisci il nome dell'ospite.")

elif menu == "📋 Elenco Prenotazioni":
    st.header("Storico Prenotazioni Attive")
    if len(st.session_state.prenotazioni) > 0:
        df_prenotazioni = pd.DataFrame(st.session_state.prenotazioni)
        st.dataframe(df_prenotazioni, use_container_width=True)
    else:
        st.info("Nessuna prenotazione registrata in questa sessione. Usaci la sezione 'Nuova Prenotazione' per aggiungerne una!")

elif menu == "📂 Archivio e Analisi File (Fase 5)":
    st.header("Analisi Automatica Fogli di Calcolo e Fatture PDF")
    st.info("Carica un file Excel/CSV oppure una fattura in PDF: l'app analizzerà il contenuto.")
    
    file_caricato = st.file_uploader("Carica un documento (Excel, CSV o PDF)", type=["csv", "xlsx", "pdf"])
    
    if file_caricato is not None:
        try:
            if file_caricato.name.endswith('.csv'):
                df_caricato = pd.read_csv(file_caricato)
                st.success(f"File CSV '{file_caricato.name}' letto con successo!")
                st.dataframe(df_caricato, use_container_width=True)
            elif file_caricato.name.endswith(('.xls', '.xlsx')):
                df_caricato = pd.read_excel(file_caricato)
                st.success(f"File Excel '{file_caricato.name}' letto con successo!")
                st.dataframe(df_caricato, use_container_width=True)
            elif file_caricato.name.endswith('.pdf'):
                st.success(f"File PDF '{file_caricato.name}' caricato con successo!")
                with pdfplumber.open(file_caricato) as pdf:
                    testo_completo = ""
                    for pagina in pdf.pages:
                        testo_estr = pagina.extract_text()
                        if testo_estr:
                            testo_completo += testo_estr + "\n"
                st.subheader("📄 Testo estratto dal PDF:")
                if testo_completo.strip():
                    st.text_area("Contenuto del documento:", testo_completo, height=250)
                else:
                    st.warning("Il PDF sembra un'immagine scansionata.")
        except Exception as e:
            st.error(f"Errore nella lettura del file: {e}")
