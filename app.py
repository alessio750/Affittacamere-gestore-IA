import streamlit as st
import pandas as pd
from datetime import datetime

# Configurazione della pagina
st.set_page_config(page_title="Gestione Affittacamere", page_icon="🛏️", layout="wide")

st.title("🛏️ Sistema Gestione Affittacamere")
st.write("Benvenuto nel gestionale per la tua struttura. Qui puoi monitorare le camere e le prenotazioni.")

# Menu laterale per navigare tra le sezioni
menu = st.sidebar.selectbox("Menu di Navigazione", ["🏠 Panoramica Camere", "➕ Nuova Prenotazione", "💰 Contabilità e Spese"])

if menu == "🏠 Panoramica Camere":
    st.header("Stato Attuale delle Camere")
    
    # Creiamo una tabella di esempio per le camere
    dati_camere = {
        "Camera": ["Camera 1 (Matrimoniale)", "Camera 2 (Doppia)", "Camera 3 (Singola)"],
        "Stato": ["Disponibile", "Occupata", "Disponibile"],
        "Ospite Attuale": ["-", "Mario Rossi", "-"],
        "Prezzo a Notte (€)": [80, 70, 50]
    }
    df_camere = pd.DataFrame(dati_camere)
    
    # Mostriamo la tabella in modo pulito ed elegante
    st.dataframe(df_camere, use_container_width=True)

elif menu == "➕ Nuova Prenotazione":
    st.header("Registra una Nuova Prenotazione")
    
    # Un modulo semplice per inserire i dati
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

elif menu == "💰 Contabilità e Spese":
    st.header("Gestione Finanziaria")
    st.info("In questa sezione potrai caricare le fatture e monitorare le spese mensili.")
    
    # Esempio di caricamento file (es. fattura o ricevuta)
    file_caricato = st.file_uploader("Carica una fattura o un documento (PDF/Immagine)", type=["pdf", "png", "jpg"])
    if file_caricato is not None:
        st.success("Documento caricato correttamente nel sistema!")
