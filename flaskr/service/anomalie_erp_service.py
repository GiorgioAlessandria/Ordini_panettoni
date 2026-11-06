"""Ordini ERP scomparsi durante la produzione: scelta esplicita, audit e ricalcolo A.

Funzioni con Session esplicita per il sync, e funzioni web con la Session Flask.
Ogni modifica applicativa avviene in una singola transazione BEGIN IMMEDIATE.
Non riutilizza lotti, non ricrea piani, non scrive negli ordini ERP.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from json import dumps
from math import ceil
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from flaskr.extensions import db
from flaskr.models.model_anomalie_erp import AnomaliaOrdineERP
from flaskr.models.model_spedizioni_snapshot import OrdineProntoSpedizione
from flaskr.models.model_ordini import (
    AnagraficaArticoli, AnagraficaClienti, BozzaPianificazioneMista,
    BozzaPianificazioneMistaCotta, BozzaPianificazioneMistaForno,
    BozzaPianificazioneMistaOrdine, CapacitaForno, Coefficienti,
    LogOperazioni, OrdiniCliente, PianificazioneOperazioni, PianificazioneFasi,
    PianificazioneImpastatriceFase, PianificazioneMistaAttiva,
    PianificazioneOrdini, PianificazioneProduzione,
    PrenotazioneMistaForno, PrenotazioneMistaImpastatrice, Stock,
)
from flaskr.models.model_consuntivi import ConsuntivoProduzioneOrdine
from flaskr.service.spedizioni_snapshot_service import _quantita_e_lotti


class ErroreAnomalia(ValueError):
    pass


def _ora():
    return datetime.now(ZoneInfo('Europe/Rome')).replace(tzinfo=None)


def _chiave(r):
    return r.IdDocumento, r.IdRigaDoc


def registra_anomalie_sync(sess: Session, chiavi_erp: set[tuple[int, int]], sync_time: datetime) -> dict:
    """Cattura prima di DELETE ERP. Non fa commit, non tocca lo stato del piano.

    L'assenza dall'ERP NON e' automaticamente un annullamento produttivo.
    Una riga Nuovo senza pianificazione non genera una decisione.
    """
    chiavi_locali = set(sess.execute(select(OrdiniCliente.IdDocumento, OrdiniCliente.IdRigaDoc)).all())
    assenti = sorted(chiavi_locali - chiavi_erp)
    nuove, attive = 0, set()
    for offset in range(0, len(assenti), 200):
        blocco = assenti[offset:offset+200]
        if not blocco:
            continue
        filtro_trad = tuple_(PianificazioneOrdini.IdDocumento, PianificazioneOrdini.IdRiga).in_(blocco)
        for r, piano in sess.execute(
            select(PianificazioneOrdini, PianificazioneProduzione)
            .join(PianificazioneProduzione, PianificazioneProduzione.IdPianificazione == PianificazioneOrdini.IdPianificazione)
            .where(filtro_trad, PianificazioneOrdini.Stato == 'In produzione',
                   PianificazioneProduzione.StatoRigaOrdine == 'In produzione')
        ):
            attive.add(('TRAD',piano.IdPianificazione,r.IdDocumento,r.IdRiga,int(r.QtaPianificata),int(r.QtaDaStock or 0),str(piano.CodArt)))
        filtro_misto = tuple_(BozzaPianificazioneMistaOrdine.IdDocumento, BozzaPianificazioneMistaOrdine.IdRigaDoc).in_(blocco)
        for r, p in sess.execute(
            select(BozzaPianificazioneMistaOrdine,PianificazioneMistaAttiva)
            .join(PianificazioneMistaAttiva,PianificazioneMistaAttiva.IdBozza == BozzaPianificazioneMistaOrdine.IdBozza)
            .where(filtro_misto,PianificazioneMistaAttiva.Stato == 'In produzione')
        ):
            attive.add(('MISTA',p.IdBozza,r.IdDocumento,r.IdRigaDoc,int(r.QtaPianificata),0,str(r.CodArt)))
    if not attive:
        return {'segnalate':0,'nuove':0}
    keys={(x[2],x[3]) for x in attive}
    ordini={}
    for i in range(0,len(keys),200):
        sub=list(sorted(keys))[i:i+200]
        for order, cliente in sess.execute(
            select(OrdiniCliente,AnagraficaClienti.RagioneSociale)
            .outerjoin(AnagraficaClienti,AnagraficaClienti.CodCliFor == OrdiniCliente.CodCliFor)
            .where(tuple_(OrdiniCliente.IdDocumento,OrdiniCliente.IdRigaDoc).in_(sub))
        ):
            ordini[_chiave(order)] = (order,cliente)
    for tipo,id_piano,doc,riga,pianificata,stock,codart_piano in sorted(attive):
        if sess.get(AnomaliaOrdineERP,(tipo,id_piano,doc,riga)) is not None:
            continue  # Non sovrascrivere mai la decisione dell'operatore.
        if (doc,riga) not in ordini:
            raise ErroreAnomalia(f'Non posso copiare ordine {doc}-{riga} prima della cancellazione')
        ordine,cliente = ordini[(doc,riga)]
        if str(ordine.CodArt) != codart_piano:
            raise ErroreAnomalia(
                f'Ordine {doc}-{riga}: CodArt ERP {ordine.CodArt!r} diverso da '
                f'CodArt pianificato {codart_piano!r}. Sync fermato per controllo.')
        sess.add(AnomaliaOrdineERP(
            TipoPiano=tipo,IdPiano=id_piano,IdDocumento=doc,IdRigaDoc=riga,
            Decisione='DaDecidere',CodArt=ordine.CodArt,
            CodCliFor=ordine.CodCliFor,ClienteSnapshot=(cliente or str(ordine.CodCliFor))[:250],
            DesArtSnapshot=(ordine.DesArt or ordine.CodArt)[:500],
            DataConsegnaSnapshot=ordine.DataConsegna,
            SaldoERPSnapshot=max(0,int(ordine.QTA_SALDO_DOC or 0)),
            QtaPianificata=pianificata,QtaDaStock=stock,RilevataIl=sync_time,
        ))
        nuove += 1
    sess.flush()
    return {'segnalate':len(attive),'nuove':nuove}


def elenco_anomalie(*, tipo=None, id_piano=None, pendenti_soltanto=False):
    stmt=db.select(AnomaliaOrdineERP)
    if tipo is not None: stmt=stmt.where(AnomaliaOrdineERP.TipoPiano==tipo)
    if id_piano is not None: stmt=stmt.where(AnomaliaOrdineERP.IdPiano==id_piano)
    if pendenti_soltanto: stmt=stmt.where(AnomaliaOrdineERP.Decisione=='DaDecidere')
    return db.session.scalars(stmt.order_by(AnomaliaOrdineERP.RilevataIl.desc())).all()


def verifica_scelte_piano(tipo,id_piano):
    """Blocca la chiusura di tutti i tipi di piano in presenza di anomalie aperte."""
    if elenco_anomalie(tipo=tipo,id_piano=id_piano,pendenti_soltanto=True):
        raise ErroreAnomalia('Il piano contiene ordini rimossi dall’ERP: scegli Continua o Ricalcola per ogni riga prima di chiudere.')


def ordine_proxy_mancante(tipo,id_piano,doc,riga):
    a=db.session.get(AnomaliaOrdineERP,(tipo,id_piano,doc,riga))
    if a is None:
        snap=db.session.get(OrdineProntoSpedizione,(doc,riga))
        if snap is None:
            return SimpleNamespace(IdDocumento=doc,IdRigaDoc=riga,CodArt='—',
                DesArt='Dati ERP storici non disponibili',DataConsegna=None,QTA_SALDO_DOC=0,
                CodCliFor=None,Cliente=SimpleNamespace(RagioneSociale='Cliente ERP non disponibile'))
        return SimpleNamespace(IdDocumento=doc,IdRigaDoc=riga,CodArt=snap.CodArt,
            DesArt=snap.DesArt,DataConsegna=snap.DataConsegna,
            QTA_SALDO_DOC=snap.QtaSaldoERPSnapshot,CodCliFor=snap.CodCliFor,
            Cliente=SimpleNamespace(RagioneSociale=snap.ClienteSnapshot))
    return SimpleNamespace(
        IdDocumento=doc,IdRigaDoc=riga,CodArt=a.CodArt,DesArt=a.DesArtSnapshot,
        DataConsegna=a.DataConsegnaSnapshot,QTA_SALDO_DOC=a.SaldoERPSnapshot,
        CodCliFor=a.CodCliFor,Cliente=SimpleNamespace(RagioneSociale=a.ClienteSnapshot),
    )


def _record_assente_riconosciuto(doc,riga):
    """Usato solo nei servizi di ricalcolo stato quando manca il record ERP.

    La chiusura impone in separata sede la risoluzione di tutte le anomalie.
    """
    return db.session.scalar(
        db.select(AnomaliaOrdineERP.TipoPiano).where(
            AnomaliaOrdineERP.IdDocumento==doc,AnomaliaOrdineERP.IdRigaDoc==riga
        ).limit(1)
    ) is not None


def _snapshot_mancanti_chiusi(sess,chiavi):
    """Accredita al registro logistico gli ordini assenti ERP completati dopo la decisione Continua.

    Se lo stesso ordine era in più piani, non lo dichiarare completo finché
    tutte le prenotazioni sono chiuse e i pezzi pianificati coprono il saldo.
    """
    if not chiavi: return
    qta,lotti=_quantita_e_lotti(sess,sorted(chiavi))
    adesso=_ora()
    for doc,riga in sorted(chiavi):
        anomalie=sess.scalars(select(AnomaliaOrdineERP).where(
            AnomaliaOrdineERP.IdDocumento==doc,
            AnomaliaOrdineERP.IdRigaDoc==riga,
            AnomaliaOrdineERP.Decisione=='Continua',
        )).all()
        if not anomalie: continue
        incompleti=sess.scalar(select(PianificazioneOrdini.IdPianificazione).where(
            PianificazioneOrdini.IdDocumento==doc,PianificazioneOrdini.IdRiga==riga,
            PianificazioneOrdini.Stato=='In produzione').limit(1))
        ancora_misto=sess.scalar(select(BozzaPianificazioneMistaOrdine.IdBozza)
            .join(PianificazioneMistaAttiva,PianificazioneMistaAttiva.IdBozza==BozzaPianificazioneMistaOrdine.IdBozza)
            .where(BozzaPianificazioneMistaOrdine.IdDocumento==doc,
                   BozzaPianificazioneMistaOrdine.IdRigaDoc==riga,
                   PianificazioneMistaAttiva.Stato=='In produzione').limit(1))
        previsti=sess.scalar(select(db.func.coalesce(db.func.sum(PianificazioneOrdini.QtaPianificata),0)).where(
            PianificazioneOrdini.IdDocumento==doc,PianificazioneOrdini.IdRiga==riga,
            PianificazioneOrdini.Stato=='Chiuso')) or 0
        previsti+=sess.scalar(select(db.func.coalesce(db.func.sum(BozzaPianificazioneMistaOrdine.QtaPianificata),0))
            .join(PianificazioneMistaAttiva,PianificazioneMistaAttiva.IdBozza==BozzaPianificazioneMistaOrdine.IdBozza)
            .where(BozzaPianificazioneMistaOrdine.IdDocumento==doc,
                   BozzaPianificazioneMistaOrdine.IdRigaDoc==riga,
                   PianificazioneMistaAttiva.Stato=='Chiuso')) or 0
        riferimento=anomalie[0]
        confermato=not incompleti and not ancora_misto and int(previsti)>=int(riferimento.SaldoERPSnapshot)
        snap=sess.get(OrdineProntoSpedizione,(doc,riga))
        if snap is None:
            snap=OrdineProntoSpedizione(IdDocumento=doc,IdRigaDoc=riga)
            sess.add(snap)
        snap.CodCliFor=riferimento.CodCliFor
        snap.ClienteSnapshot=riferimento.ClienteSnapshot
        snap.CodArt=riferimento.CodArt
        snap.DesArt=riferimento.DesArtSnapshot
        snap.DataConsegna=riferimento.DataConsegnaSnapshot
        snap.QtaSaldoERPSnapshot=riferimento.SaldoERPSnapshot
        snap.QtaAssegnataSpedizione=int(qta.get((doc,riga),0))
        snap.LottiSnapshot=', '.join(lotti.get((doc,riga),[])) or None
        snap.PresenteERP=False
        snap.ConfermatoChiuso=bool(confermato)
        snap.AggiornatoIl=adesso
    sess.flush()


def aggiorna_spedizioni_a_chiusura(tipo,id_piano):
    righe=db.session.scalars(db.select(AnomaliaOrdineERP).where(
        AnomaliaOrdineERP.TipoPiano==tipo,AnomaliaOrdineERP.IdPiano==id_piano,
        AnomaliaOrdineERP.Decisione=='Continua',
    )).all()
    _snapshot_mancanti_chiusi(db.session,{(r.IdDocumento,r.IdRigaDoc) for r in righe})


def _to_json(data):
    return dumps(data,ensure_ascii=False,default=str,sort_keys=True)


def _stock_restituzione(cod,qta):
    if qta<1: return
    stock=db.session.get(Stock,cod)
    if stock is None:
        stock=Stock(CodArt=cod,Quantita=qta)
        db.session.add(stock)
    else:
        stock.Quantita=int(stock.Quantita)+qta


def _recalc_trad(a,operatore):
    piano=db.session.get(PianificazioneProduzione,a.IdPiano)
    if not piano or piano.StatoRigaOrdine!='In produzione':
        raise ErroreAnomalia('La pianificazione tradizionale non è più in produzione.')
    if piano.QtaStockGenerata is None or int(piano.QtaStockGenerata)!=0:
        raise ErroreAnomalia('Piano con surplus anticipato non riconciliato: verificare lo stock prima del ricalcolo.')
    righe=db.session.scalars(db.select(PianificazioneOrdini).where(
        PianificazioneOrdini.IdPianificazione==a.IdPiano,
        PianificazioneOrdini.Stato=='In produzione',
    )).all()
    target=next((r for r in righe if (r.IdDocumento,r.IdRiga)==(a.IdDocumento,a.IdRigaDoc)),None)
    if target is None: raise ErroreAnomalia('Riga non più attiva nel piano.')
    rimanenti=[r for r in righe if r is not target]
    if not rimanenti:
        if piano.QtaStockGenerata is None or int(piano.QtaStockGenerata)!=0:
            raise ErroreAnomalia('Piano senza altri ordini con surplus storico non riconciliato: verifica il magazzino prima di annullarlo.')
        qta_stornata=int(target.QtaDaStock or 0)
        _stock_restituzione(piano.CodArt,qta_stornata)
        target.Stato='Annullato'
        target.DataChiusura=None
        ora=_ora()
        piano.StatoRigaOrdine='Annullato'
        piano.DataAnnullamento=ora
        piano.OperatoreAnnullamento=operatore
        piano.MotivoAnnullamento='Ultima riga ERP esclusa: '+a.IdDocumento.__str__()+'-'+str(a.IdRigaDoc)
        id_fasi=db.session.scalars(db.select(PianificazioneFasi.IdPianificazioneFase).where(
            PianificazioneFasi.IdPianificazione==a.IdPiano)).all()
        if id_fasi:
            db.session.execute(delete(PianificazioneImpastatriceFase).where(
                PianificazioneImpastatriceFase.IdPianificazioneFase.in_(id_fasi)))
        db.session.add(PianificazioneOperazioni(IdPianificazione=a.IdPiano,Azione='Annullato',
            Operatore=operatore,DataOperazione=ora,
            Motivo='Esclusione ultima riga per assenza ERP',StockRestituito=qta_stornata,StockStornato=0))
        return {'riga_esclusa':f'{a.IdDocumento}-{a.IdRigaDoc}',
                'pezzi':int(target.QtaPianificata)}, {
            'piano':'Annullato','id_invariato':True,'lotto_invariato':True,
            'stock_restituito':qta_stornata,'prenotazioni_rilasciate':True,
        }
    vecchia_qta=int(piano.PanettoniPianificati or 0)
    vecchio_peso=Decimal(str(piano.PesoTotale or 0))
    nuova_qta=sum(int(r.QtaPianificata)-int(r.QtaDaStock or 0) for r in rimanenti)
    if nuova_qta<=0 or vecchia_qta<=0 or vecchio_peso<=0:
        raise ErroreAnomalia('I pezzi da produrre sono zero o i dati storici non consentono il ricalcolo: richiede intervento manuale.')
    art=db.session.get(AnagraficaArticoli,piano.CodArt)
    if art is None: raise ErroreAnomalia('Articolo assente: impossibile calcolare la capacità forno.')
    forno=db.session.scalar(db.select(CapacitaForno).where(
        CapacitaForno.IdForno==piano.IdForno,
        CapacitaForno.DimensionePanettone==art.Prezzatura,
        CapacitaForno.TipologiaPanettone==piano.TipologiaPanettone,
    ))
    if forno is None or int(forno.Capienza or 0)<=0:
        raise ErroreAnomalia('Capacità forno non disponibile. Nessun dato modificato.')
    nuove_cotte=ceil(nuova_qta / int(forno.Capienza))
    if nuove_cotte>int(piano.NumeroCotte or 0):
        raise ErroreAnomalia('Nuovo numero cotte maggiore della prenotazione attuale: necessaria ripianificazione.')
    nuovo_peso=(vecchio_peso*Decimal(nuova_qta)/Decimal(vecchia_qta)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    vecchio={'pezzi':vecchia_qta,'cotte':piano.NumeroCotte,'peso_grammi':str(piano.PesoTotale),
             'riga_esclusa':{'doc':a.IdDocumento,'riga':a.IdRigaDoc,'qta':target.QtaPianificata,'stock':target.QtaDaStock}}
    # Le fasi e gli slot delle macchine restano IDENTICI; il kg prenotato si riduce.
    id_fasi=db.session.scalars(db.select(PianificazioneFasi.IdPianificazioneFase).where(
        PianificazioneFasi.IdPianificazione==a.IdPiano)).all()
    for pren in db.session.scalars(db.select(PianificazioneImpastatriceFase).where(
        PianificazioneImpastatriceFase.IdPianificazioneFase.in_(id_fasi))).all() if id_fasi else []:
        pren.KgAssegnati=(Decimal(str(pren.KgAssegnati))*nuovo_peso/vecchio_peso).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
    _stock_restituzione(piano.CodArt,int(target.QtaDaStock or 0))
    target.Stato='Annullato'  # audit: riga originale e quantità sono conservate.
    target.DataChiusura=None
    piano.PanettoniPianificati=nuova_qta
    piano.NumeroCotte=nuove_cotte
    piano.PesoTotale=nuovo_peso
    db.session.add(LogOperazioni(IdDocumento=a.IdDocumento,IdRigaDoc=a.IdRigaDoc,
        Stato='Escluso da piano '+str(a.IdPiano)+' per assenza ERP',Operatore=operatore,synced_at=_ora()))
    from flaskr.service.ciclo_vita_pianificazione_service import _rileggi_stato_ordine
    for r in rimanenti:
        if db.session.get(OrdiniCliente,(r.IdDocumento,r.IdRiga)) is not None:
            _rileggi_stato_ordine(r.IdDocumento,r.IdRiga,_ora(),operatore)
    return vecchio, {'pezzi':nuova_qta,'cotte':nuove_cotte,'peso_grammi':str(nuovo_peso),
                     'rimanenti':len(rimanenti),'stock_restituito':int(target.QtaDaStock or 0),
                     'lotto_invariato':True,'slot_macchine_invariati':True}


def _peso_misto_corrente(bozza):
    codici={r.CodArt for r in bozza.ordini}
    articoli={a.CodArt:a for a in db.session.scalars(db.select(AnagraficaArticoli).where(
        AnagraficaArticoli.CodArt.in_(codici))).all()}
    if set(articoli)!=codici: raise ErroreAnomalia('Anagrafica articolo mancante per peso misto')
    coeff={c.Coefficiente:Decimal(str(c.Valore)) for c in db.session.scalars(db.select(Coefficienti).where(
        Coefficienti.Coefficiente.in_(['PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA','PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO']))).all()}
    if len(coeff)!=2: raise ErroreAnomalia('Coefficienti impasto mancanti')
    from flaskr.service.anteprima_impasto_misto_service import _prezzo_peso_unitario
    return sum((_prezzo_peso_unitario(articoli[r.CodArt],
                  coeff['PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA'],
                  coeff['PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO'])*int(r.QtaPianificata)
                for r in bozza.ordini),Decimal(0))


def _recalc_misto(a,operatore,cotte_per_forno):
    piano=db.session.get(PianificazioneMistaAttiva,a.IdPiano)
    if not piano or piano.Stato!='In produzione':
        raise ErroreAnomalia('Piano misto non più attivo')
    b=piano.bozza
    if cotte_per_forno is None or set(cotte_per_forno)!={int(f.IdForno) for f in b.forni}:
        raise ErroreAnomalia('Indica esplicitamente le cotte per TUTTI i forni della pianificazione.')
    if any(type(v) is not int or v<0 for v in cotte_per_forno.values()):
        raise ErroreAnomalia('Numero di cotte non valido.')
    ordine=next((r for r in b.ordini if _chiave(r)==(a.IdDocumento,a.IdRigaDoc)),None)
    if ordine is None: raise ErroreAnomalia('Riga mista già rimossa dalla pianificazione')
    if len(b.ordini)<2:
        if any(v!=0 for v in cotte_per_forno.values()):
            raise ErroreAnomalia('Ultimo ordine: imposta 0 cotte per ogni forno per annullare il piano.')
        ora=_ora()
        piano.Stato='Annullato'
        piano.AnnullataIl=ora
        piano.OperatoreAnnullamento=operatore
        piano.MotivoAnnullamento='Esclusione ultima riga ERP durante la produzione'
        db.session.execute(delete(PrenotazioneMistaForno).where(
            PrenotazioneMistaForno.IdBozza==a.IdPiano))
        db.session.execute(delete(PrenotazioneMistaImpastatrice).where(
            PrenotazioneMistaImpastatrice.IdBozza==a.IdPiano))
        return {'riga_esclusa':f'{a.IdDocumento}-{a.IdRigaDoc}',
                'pezzi':int(ordine.QtaPianificata)}, {
            'piano':'Annullato','id_invariato':True,'lotto_invariato':True,
            'prenotazioni_rilasciate':True,
        }
    old_peso=Decimal(str(b.PesoTotaleGrammi))
    if old_peso<=0: raise ErroreAnomalia('Peso misto originario non valido')
    prima={'qta':{f'{r.IdDocumento}-{r.IdRigaDoc}':int(r.QtaPianificata) for r in b.ordini},
           'cotte':{str(f.IdForno):[(c.NumeroCotta,c.CodArt,int(c.QtaPezzi)) for c in f.cotte] for f in b.forni},
           'peso_grammi':str(old_peso)}
    codice=ordine.CodArt
    forno_target=ordine.IdForno
    qta_da_togliere=int(ordine.QtaPianificata)
    forno=next((f for f in b.forni if f.IdForno==forno_target),None)
    if forno is None: raise ErroreAnomalia('Forno non trovato nello storico del piano misto')
    cotte=[(int(c.NumeroCotta),str(c.CodArt),int(c.QtaPezzi)) for c in forno.cotte]
    if sum(q for _,c,q in cotte if c==codice)<qta_da_togliere:
        raise ErroreAnomalia('Distribuzione cotte incoerente con la quantità da escludere. Ricalcolo bloccato.')
    # Rimuove i pezzi dell'ordine dalle ULTIME cotte del suo CodArt.
    riduzione=qta_da_togliere
    nuovo=[]
    for n,c,q in sorted(cotte,key=lambda x:(x[0],x[1]),reverse=True):
        if c==codice and riduzione:
            delta=min(riduzione,q);q-=delta;riduzione-=delta
        if q>0: nuovo.append((n,c,q))
    if riduzione: raise ErroreAnomalia('Riduzione delle cotte non completata')
    # Convalida il numero di cotte che l'operatore ha dichiarato.
    # Sugli altri forni i numeri restano immutati; su quello interessato
    # il nuovo valore non può superare il precedente né essere inferiore
    # alle cotte con pezzi rimasti dopo il ricalcolo.
    for f in b.forni:
        proposte=sorted({n for n,c,q in nuovo}) if f.IdForno==forno_target else sorted({c.NumeroCotta for c in f.cotte})
        richieste=cotte_per_forno[f.IdForno]
        minimo=len(proposte)
        if richieste<minimo or richieste>int(f.NumeroCotte):
            raise ErroreAnomalia(f'Forno {f.IdForno}: cotte consentite da {minimo} a {f.NumeroCotte}; richieste {richieste}.')
        if f.IdForno!=forno_target and richieste!=int(f.NumeroCotte):
            raise ErroreAnomalia(f'Forno {f.IdForno} non coinvolto dalla riga eliminata: le cotte devono rimanere invariate.')
        if f.IdForno==forno_target and bool(proposte)!=bool(richieste):
            raise ErroreAnomalia(f'Forno {f.IdForno}: non è possibile prenotare cotte senza pezzi.')
    # Non toccare le quantità degli altri CodArt o i rispettivi forni.
    b.ordini.remove(ordine)  # delete-orphan: audit della riga nella tabella anomalie
    db.session.flush()
    rimanenti=list(b.ordini)
    if not rimanenti: raise ErroreAnomalia('Nessun ordine residuo')
    nuovo_peso=_peso_misto_corrente(b)
    if nuovo_peso<=0 or nuovo_peso>old_peso:
        raise ErroreAnomalia('Il nuovo peso impasto non è valido')
    # Ricostruisce solo il forno coinvolto, senza cambiare i blocchi o le macchine.
    db.session.execute(delete(BozzaPianificazioneMistaCotta).where(
        BozzaPianificazioneMistaCotta.IdBozza==a.IdPiano,
        BozzaPianificazioneMistaCotta.IdForno==forno_target))
    db.session.flush()
    occupate=sorted({n for n,_,_ in nuovo})
    # Se il forno rimane usato, la numerazione diventa continua da 1.
    if nuovo:
        rinumera={n:i+1 for i,n in enumerate(occupate)}
        for n,c,q in nuovo:
            db.session.add(BozzaPianificazioneMistaCotta(
                IdBozza=a.IdPiano,IdForno=forno_target,NumeroCotta=rinumera[n],CodArt=c,QtaPezzi=q))
        forno.NumeroCotte=cotte_per_forno[forno_target]
        db.session.expire(forno, ["cotte"])
        for pren in db.session.scalars(db.select(PrenotazioneMistaForno).where(
            PrenotazioneMistaForno.IdBozza==a.IdPiano,
            PrenotazioneMistaForno.IdForno==forno_target)).all():
            pren.NumeroCotte=cotte_per_forno[forno_target]
    else:
        db.session.execute(delete(PrenotazioneMistaForno).where(
            PrenotazioneMistaForno.IdBozza==a.IdPiano,
            PrenotazioneMistaForno.IdForno==forno_target))
        b.forni.remove(forno)  # delete-orphan preserva la bozza ricalcolata
        db.session.flush()
    b.NumeroCotte=sum(int(f.NumeroCotte) for f in b.forni)
    b.PesoTotaleGrammi=nuovo_peso
    fattore=nuovo_peso/old_peso
    for m in b.impastatrici:
        m.KgAssegnati=(Decimal(str(m.KgAssegnati))*fattore).quantize(Decimal('0.000001'),rounding=ROUND_HALF_UP)
    for m in db.session.scalars(db.select(PrenotazioneMistaImpastatrice).where(
        PrenotazioneMistaImpastatrice.IdBozza==a.IdPiano)).all():
        m.KgAssegnati=(Decimal(str(m.KgAssegnati))*fattore).quantize(Decimal('0.000001'),rounding=ROUND_HALF_UP)
    db.session.add(LogOperazioni(IdDocumento=a.IdDocumento,IdRigaDoc=a.IdRigaDoc,
        Stato='Escluso da piano misto '+str(a.IdPiano)+' per assenza ERP',
        Operatore=operatore,synced_at=_ora()))
    from flaskr.service.attivazione_impasto_misto_service import _rileggi_stato
    for r in rimanenti:
        if db.session.get(OrdiniCliente,(r.IdDocumento,r.IdRigaDoc)) is not None:
            _rileggi_stato(r.IdDocumento,r.IdRigaDoc,_ora(),operatore)
    db.session.flush()
    dopo={'qta':{f'{r.IdDocumento}-{r.IdRigaDoc}':int(r.QtaPianificata) for r in rimanenti},
          'cotte':{str(f.IdForno):[(c.NumeroCotta,c.CodArt,int(c.QtaPezzi)) for c in f.cotte] for f in b.forni},
          'peso_grammi':str(nuovo_peso),'lotto_invariato':True,'slot_macchine_invariati':True}
    return prima,dopo


def decidi_anomalia(*,tipo,id_piano,doc,riga,decisione,motivazione,operatore,conferma=False,cotte_per_forno=None):
    """Conferma operatoria atomica. Selezione A conserva ID e lotto del piano."""
    if tipo not in ('TRAD','MISTA') or decisione not in ('continua','escludi'):
        raise ErroreAnomalia('Tipo piano o decisione non valida')
    motivazione=(motivazione or '').strip()
    if len(motivazione)<10 or len(motivazione)>500:
        raise ErroreAnomalia('Motivazione obbligatoria da 10 a 500 caratteri')
    if decisione=='escludi' and not conferma:
        raise ErroreAnomalia('Confermare il ricalcolo dei pezzi, delle cotte e delle prenotazioni')
    if db.engine.dialect.name != 'sqlite':
        raise ErroreAnomalia('Gestione anomalie disponibile solo con SQLite')
    db.session.rollback()
    try:
        db.session.execute(text('BEGIN IMMEDIATE'))
        a=db.session.get(AnomaliaOrdineERP,(tipo,id_piano,doc,riga))
        if a is None or a.Decisione!='DaDecidere':
            raise ErroreAnomalia('Anomalia non trovata o già risolta. Ricarica la pagina.')
        if db.session.get(OrdiniCliente,(doc,riga)) is not None:
            raise ErroreAnomalia('L’ordine è tornato nel feed ERP: verifica con il gestionale prima di decidere.')
        if tipo=='TRAD':
            p=db.session.get(PianificazioneProduzione,id_piano)
            if p is None or p.StatoRigaOrdine!='In produzione': raise ErroreAnomalia('Piano non più attivo')
        else:
            p=db.session.get(PianificazioneMistaAttiva,id_piano)
            if p is None or p.Stato!='In produzione': raise ErroreAnomalia('Piano non più attivo')
        prima,dopo=({'riga':f'{doc}-{riga}','pianificati':a.QtaPianificata},
                     {'scelta':'Continua','quantita_invariate':True,'lotto_invariato':True})
        if decisione=='escludi':
            if tipo=='TRAD': prima,dopo=_recalc_trad(a,operatore)
            else: prima,dopo=_recalc_misto(a,operatore,cotte_per_forno)
        a.Decisione='Continua' if decisione=='continua' else 'Escluso'
        a.Operatore=operatore
        a.DecisaIl=_ora()
        a.Motivazione=motivazione
        a.DatiPrima=_to_json(prima)
        a.DatiDopo=_to_json(dopo)
        db.session.flush()
        db.session.commit()
        return dopo
    except (ErroreAnomalia,SQLAlchemyError,ValueError,ArithmeticError) as exc:
        db.session.rollback()
        if isinstance(exc,ErroreAnomalia): raise
        raise ErroreAnomalia('Ricalcolo non riuscito: nessuna modifica salvata. '+str(exc)) from exc
