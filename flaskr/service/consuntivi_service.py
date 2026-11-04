"""Consuntivo per ordine e per articolo; transazione gestita dal servizio di chiusura."""
from __future__ import annotations
import re
from datetime import datetime
from sqlalchemy import update
from flaskr.extensions import db
from flaskr.models.model_consuntivi import (
    ConsuntivoProduzioneArticolo, ConsuntivoProduzioneOrdine,
    GiacenzaSecondaScelta, MovimentoStockConsuntivo,
)
from flaskr.models.model_ordini import Stock
from flaskr.models.model_lotto import LottoPianificazione

MAX_QTA = (1 << 63) - 1

class ErroreConsuntivo(ValueError):
    pass

def quantita_intera(valore, campo):
    if isinstance(valore, bool) or re.fullmatch(r"[0-9]+", str(valore or "")) is None:
        raise ErroreConsuntivo(f"{campo}: inserire un intero non negativo")
    n = int(valore)
    if n > MAX_QTA:
        raise ErroreConsuntivo(f"{campo}: numero troppo grande")
    return n

def leggi_form_consuntivo(form, righe):
    """L'elenco delle righe è derivato dal DB, mai dal client."""
    if not righe:
        raise ErroreConsuntivo("Nessun ordine nel piano")
    prodotte, articoli = {}, {}
    for doc, riga, cod, _ in righe:
        k = (int(doc), int(riga))
        if k in prodotte:
            raise ErroreConsuntivo("Riga duplicata")
        nome = f"prodotta_{doc}_{riga}"
        valori = form.getlist(nome)
        if len(valori) != 1:
            raise ErroreConsuntivo(f"Campo obbligatorio: {nome}")
        prodotte[k] = quantita_intera(valori[0], nome)
        if cod not in articoli:
            valori_s = form.getlist(f"seconda_{cod}")
            valori_m = form.getlist(f"magazzino_{cod}")
            if len(valori_s) != 1 or len(valori_m) != 1:
                raise ErroreConsuntivo(f"Quantità mancanti per articolo {cod}")
            articoli[cod] = (quantita_intera(valori_s[0], f"seconda_{cod}"),
                            quantita_intera(valori_m[0], f"magazzino_{cod}"))
    return prodotte, articoli

def _aggiorna_stock_prima(cod, delta):
    if not delta:
        return
    record = db.session.get(Stock, cod)
    if record is None:
        if delta < 0:
            raise ErroreConsuntivo(f"Stock {cod} insufficiente per lo storno di surplus anticipato")
        db.session.add(Stock(CodArt=cod, Quantita=delta))
        db.session.flush()
        return
    if int(record.Quantita) + delta < 0 or int(record.Quantita) + delta > MAX_QTA:
        raise ErroreConsuntivo(f"Stock {cod}: saldo non valido per la riconciliazione")
    esito = db.session.execute(
        update(Stock).where(Stock.CodArt == cod,
            Stock.Quantita + delta >= 0, Stock.Quantita + delta <= MAX_QTA)
        .values(Quantita=Stock.Quantita + delta)
    )
    if esito.rowcount != 1:
        raise ErroreConsuntivo(f"Stock {cod} modificato: ripetere")

def _aggiorna_stock_seconda(cod, qta):
    if not qta:
        return
    record = db.session.get(GiacenzaSecondaScelta, cod)
    if record is None:
        db.session.add(GiacenzaSecondaScelta(CodArt=cod, Quantita=qta))
        db.session.flush()
        return
    if int(record.Quantita) + qta > MAX_QTA:
        raise ErroreConsuntivo(f"Stock seconda scelta {cod}: overflow")
    esito = db.session.execute(
        update(GiacenzaSecondaScelta)
        .where(GiacenzaSecondaScelta.CodArt == cod,
               GiacenzaSecondaScelta.Quantita + qta <= MAX_QTA)
        .values(Quantita=GiacenzaSecondaScelta.Quantita + qta)
    )
    if esito.rowcount != 1:
        raise ErroreConsuntivo(f"Stock seconda scelta {cod}: aggiornamento fallito")

def registra_consuntivo(tipo, id_piano, righe, prodotte, articoli, operatore, *, stock_anticipato=0):
    """Scrive consuntivo e movimenti senza commit. Nessun limite rispetto al pianificato.

    Nel TRAD legacy il surplus anticipato è sostituito dalla giacenza fisica dichiarata.
    Se l'accredito anticipato è stato già consumato e lo storno produce un
    saldo negativo, la chiusura viene bloccata per riconciliazione manuale.
    """
    if tipo not in ("TRAD", "MISTA") or not righe:
        raise ErroreConsuntivo("Piano senza righe valide")
    chiavi = {(int(d), int(r)) for d, r, _, _ in righe}
    codici = {c for _, _, c, _ in righe}
    if len(chiavi) != len(righe) or set(prodotte) != chiavi or set(articoli) != codici:
        raise ErroreConsuntivo("Dati consuntivo incompleti")
    if stock_anticipato is None:
        raise ErroreConsuntivo("Stock anticipato storico non riconciliato: verificare QtaStockGenerata")
    anticipato = quantita_intera(stock_anticipato, "Stock anticipato")
    if tipo == "MISTA" and anticipato:
        raise ErroreConsuntivo("La pianificazione mista non ha stock anticipato")
    lotto = db.session.scalar(
        db.select(LottoPianificazione.Lotto).where(
            LottoPianificazione.TipoOwner == tipo,
            LottoPianificazione.IdOwner == id_piano,
            LottoPianificazione.Corrente.is_(True),
        )
    )
    ora = datetime.now()
    for cod in sorted(codici):
        seconda = quantita_intera(articoli[cod][0], f"Seconda scelta {cod}")
        magaz = quantita_intera(articoli[cod][1], f"Magazzino {cod}")
        if db.session.get(ConsuntivoProduzioneArticolo, (tipo, id_piano, cod)) is not None:
            raise ErroreConsuntivo("Consuntivo già registrato")
        prev = anticipato if tipo == "TRAD" else 0
        delta = magaz - prev
        _aggiorna_stock_prima(cod, delta)
        _aggiorna_stock_seconda(cod, seconda)
        db.session.add(ConsuntivoProduzioneArticolo(
            TipoPiano=tipo, IdPiano=id_piano, CodArt=cod,
            QtaSecondaScelta=seconda, QtaMagazzino=magaz,
            StockAnticipato=prev, LottoSnapshot=lotto,
            Operatore=operatore, CreatoIl=ora))
        for stock, var in (("PRIMA", delta), ("SECONDA", seconda)):
            db.session.add(MovimentoStockConsuntivo(
                TipoPiano=tipo, IdPiano=id_piano, CodArt=cod, TipoStock=stock,
                Variazione=var, Operatore=operatore, RegistratoIl=ora))
    for doc, riga, cod, pianificata in righe:
        qta = quantita_intera(pianificata, "Quantità pianificata")
        if qta <= 0:
            raise ErroreConsuntivo("Storico pianificato privo di quantità")
        db.session.add(ConsuntivoProduzioneOrdine(
            TipoPiano=tipo, IdPiano=id_piano, IdDocumento=doc, IdRigaDoc=riga,
            CodArt=cod, QtaPianificataSnapshot=qta,
            QtaProdotta=quantita_intera(prodotte[(doc, riga)], "Quantità prodotta"),
            LottoSnapshot=lotto))
    db.session.flush()
