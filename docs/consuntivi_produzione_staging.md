# Consuntivi di produzione: collaudo in staging

## Contratto delle quantità

- `QtaProdotta`: solo pezzi di prima scelta **effettivamente destinati a ciascuna riga ordine**.
- `QtaSecondaScelta`: pezzi aggiuntivi di seconda scelta per `CodArt`, in `giacenze_seconda_scelta`.
- `QtaMagazzino`: pezzi aggiuntivi di prima scelta per `CodArt`, in `stock`.
- Per i piani tradizionali `QtaDaStock` rappresenta i pezzi **già prelevati dallo stock** per l'ordine: il numero pronto per spedizione è `QtaProdotta + QtaDaStock`, non una nuova produzione di quei pezzi.
- Nessun vincolo per cui le quantità effettive debbano coincidere con `QtaPianificata`. Sono ammessi zero e valori superiori al pianificato, mai negativi.
- Le pianificazioni tradizionali hanno un solo articolo; nelle pianificazioni miste magazzino e seconda scelta sono distinti per ciascun `CodArt`.
- Nessun aggiornamento delle tabelle ERP `ordini_cliente_aperti`, `QTA_SALDO_DOC` o `QTA_CONS`; lo stato di chiusura resta `Chiuso`.

## Prima dell'avvio

1. Eseguire un **backup consistente a database fermo**; includere file SQLite WAL/SHM quando presenti e usare una copia staging (oppure backup API SQLite).
2. Verificare che siano applicate le migrazioni esistenti della gestione spedizioni/ruoli/lotti.
3. Applicare **una sola volta**, sulla copia di staging, `migrazioni/003_consuntivi_produzione.sql` (non modificare direttamente la produzione). La migrazione aggiunge quattro tabelle e un indice.
4. Registrare una nuova pianificazione tradizionale e assicurarsi che il surplus soltanto simulato **non** aumenti più lo stock prima della chiusura.
5. Chiudere il piano con quantità differenti dal pianificato, seconda scelta e magazzino; verificare i due saldi, i movimenti e lo stato `Chiuso`.
6. Ripetere la prova con una pianificazione mista con almeno due codici e due ordini per lo stesso codice.
7. Verificare la schermata spedizioni: quantità effettive per ordine, lotti e spedizione completa di una riga (non sono consentite spedizioni parziali). Per le pianificazioni tradizionali la quantità fisicamente pronta include `QtaDaStock`.
8. Testare due POST di chiusura identici: il secondo deve fallire senza nuovi movimenti. Testare negativi, valori mancanti, campi duplicati e overflow; fallimento deve lasciare piano `In produzione`.
9. Testare la chiusura di **piani storici**: se `QtaStockGenerata` è `NULL`, serve riconciliazione preesistente; se maggiore di 0 viene stornato l'accredito anticipato e sostituito dai pezzi fisicamente dichiarati. Se lo stock non basta, la chiusura viene bloccata, senza valori negativi.
10. Verificare i permessi `admin`/`utente_produzione` e il divieto di chiusura per `utente_vendite`/`utente_spedizioni`.

## Attenzione per installazioni con storico

- Le pianificazioni **già chiuse prima di questa modifica** non hanno il nuovo consuntivo. **Non** convertire implicitamente `QtaPianificata` in produzione fisica. La nuova disponibilità spedizioni non le considera finché non si svolge una **riconciliazione storica dedicata**; questa PR non include un backfill automatico.
- Spedizioni preparate prima della migrazione possono avere la quantità snapshot basata sul pianificato: possono richiedere annullamento della preparazione e nuova creazione dopo riconciliazione.
- I saldi stock anteriori alla modifica possono comprendere crediti surplus anticipati: riconciliare prima di assumere che rappresentino giacenza fisica.
- Il dato già attribuito a un ordine non è automaticamente trasferito al magazzino se l'ordine viene spedito con meno pezzi: gli operatori dichiarano esplicitamente la quota magazzino.
- Questi controlli sono **precondizioni di rollout**: la PR è volutamente draft finché non è stato effettuato il collaudo Flask/IIS/SQLite e la riconciliazione dei piani storici.

## Test offline

```powershell
python -m pytest -q test/test_consuntivi_schema.py
```

Il test SQL non sostituisce una prova con database reale, concorrenza e ruoli.
