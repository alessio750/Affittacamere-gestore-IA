import streamlit as st
import pandas as pd
from datetime import datetime

# Configurazione della pagina
st.set_page_config(page_title="Gestione Affittacamere IA", page_icon="🛏️", layout="wide")

st.title("🛏️ Sistema Gestione Affittacamere con IA")
st.write("Carica i tuoi fogli di calcolo o le fatture: il sistema analizzerà i dati per te.")

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
    st.header("Analisi Automatica Fogli di Calcolo e Fatture")
    st.info("Carica un file Excel (.xlsx), CSV o una fattura: l'app leggerà i dati contenuti all'interno.")
    
    # Caricamento file multipli o singoli supportati dal motore dati
    file_caricato = st.file_uploader("Carica il tuo file di dati", type=["csv", "xlsx"])
    
    if file_caricato is not None:
        try:
            # Riconoscimento automatico del formato del file
            if file_caricato.name.endswith('.csv'):
                df_caricato = pd.read_csv(file_caricato)
            else:
                df_caricato = pd.read_excel(file_caricato)
                
            st.success(f"File '{file_caricato.name}' letto con successo!")
            st.subheader("Anteprima dei dati estratti:")
            st.dataframe(df_caricato, use_container_width=True)
            
            # Statistiche rapide di base calcolate in automatico
            st.subheader("📊 Analisi Rapida")
            st.write(f"Il documento contiene **{len(df_caricato)} righe** di dati.")
            
        except Exception as e:
            st.error(f- "Errore nella lettura del file: {e}")

