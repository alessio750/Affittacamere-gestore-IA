import streamlit as st
import pandas as pd
import pdfplumber
from datetime import datetime

# Configurazione della pagina
st.set_page_config(page_title="Gestione Affittacamere IA", page_icon="🛏️", layout="wide")

st.title("🛏️ Sistema Gestione Affittacamere con IA")
st.write("Gestione contabile, prenotazioni e analisi intelligente dei documenti.")

# Menu laterale per navigare tra le sezioni
menu = st.sidebar.selectbox("Menu di Navigazione", ["🏠 Panoramica Camere", "➕ Nuova Prenotazione", "📂 Archivio e Analisi File (Fase 5)"])

if menu == "🏠 Panoramica Camere":
    st.header("Stato Attuale delle Camere")
    dati_camere = {
        "Camera": ["Camera 1 (Matrimoniale)", "Camera 2 (Doppia)", "Camera 3 (Singola)"],
        "Stato": ["Disponibile", "Occupata", "Disponibile"],
        "Ospite Attuale": ["-", "Mario Rossi", "-"],
        "Prezzo a Notte (€)": [80, 70, 50]
    }
    df_camere = pd.DataFrame(dati_camere)
    st.dataframe(df_camere, use_container_width=True)

elif menu == "➕ Nuova Prenotazione":
    st.header("Registra una Nuova Prenotazione")
    with st.form("form_prenotazione"):
        nome_ospite = st.text_input("Nome e Cognome Ospite")
        camera_scelta = st.selectbox("Seleziona Camera", ["Camera 1", "Camera 2", "Camera 3"])
        data_arrivo = st.date_input("Data di Arrivo")
        data_partenza = st.date_input("Data di Partenza")
        prezzo_totale = st.number_input("Prezzo Totale (€)", min_value=0.0, format="%.2f")
        
        pulsante_salva = st.form_submit_button("Salva Prenotazione")
        if pulsante_salva:
            if nome_ospite:
                st.success(f"Prenotazione salvata con successo per {nome_ospite} nella {camera_scelta}!")
            else:
                st.error("Per favore, inserisci il nome dell'ospite.")

elif menu == "📂 Archivio e Analisi File (Fase 5)":
    st.header("Analisi Automatica Fogli di Calcolo e Fatture PDF")
    st.info("Carica un file Excel/CSV oppure una fattura in PDF: l'app analizzerà il contenuto.")
    
    # Caricamento file esteso anche ai PDF
    file_caricato = st.file_uploader("Carica un documento (Excel, CSV o PDF)", type=["csv", "xlsx", "pdf"])
    
    if file_caricato is not None:
        try:
            # Se è un CSV
            if file_caricato.name.endswith('.csv'):
                df_caricato = pd.read_csv(file_caricato)
                st.success(f"File CSV '{file_caricato.name}' letto con successo!")
                st.dataframe(df_caricato, use_container_width=True)
                
            # Se è un Excel
            elif file_caricato.name.endswith(('.xls', '.xlsx')):
                df_caricato = pd.read_excel(file_caricato)
                st.success(f"File Excel '{file_caricato.name}' letto con successo!")
                st.dataframe(df_caricato, use_container_width=True)
                
            # Se è un PDF (es. fattura o ricevuta)
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
                    st.warning("Il PDF sembra un'immagine scansionata o non contiene testo leggibile direttamente.")
                    
        except Exception as e:
            st.error(f"Errore nella lettura del file: {e}")
