"""Ripianifica un piano senza toccare ordini, stock o assegnazioni dei macchinari.

La simulazione non scrive. La conferma valida di nuovo sotto lock SQLite.
Non rigenera le assegnazioni: usa gli ID delle fasi e i kg già registrati.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from decimal import Decimal

from flaskr.extensions import db
from flaskr.service.lotto_pianificazione_service import assegna_lotto, prima_data_impasto_fasi
from flaskr.models.model_ordini import (
    AnagraficaImpastatrici, PianificazioneFasi, PianificazioneImpastatriceFase,
    PianificazioneOrdini, PianificazioneOrdiniImpastatrici,
    PianificazioneProduzione, StoricoRipianificazioni, TipologieLavorazione,
)
from flaskr.service.agenda_pianificazione_service import organizza_eventi_giorni_interessati
from flaskr.service.pianificazione_service import (
    ErroreCalcoloPianificazione, calcola_blocchi_fase,
)
from flaskr.service.risorse_produzione_service import (
    _blocchi_occupati, _capacita_kg, _occupazioni_impastatrici_storiche,
    disponibilita_forno, impastatrici_libere, individua_fasi_risorse,
)


def prepara_ripianificazione(id_piano: int, nuova_data: date) -> dict:
    """Controlla tutte le risorse escludendo soltanto l'occupazione del piano spostato."""
    piano = db.session.get(PianificazioneProduzione, id_piano)
    if piano is None:
        raise ErroreCalcoloPianificazione("Pianificazione non trovata")
    if piano.StatoRigaOrdine != "In produzione":
        raise ErroreCalcoloPianificazione("La pianificazione non è attiva")
    if not isinstance(nuova_data, date):
        raise ErroreCalcoloPianificazione("Inserisci una data valida")
    if piano.DataProduzione == nuova_data:
        raise ErroreCalcoloPianificazione("La nuova data coincide con quella attuale")
    if piano.BloccoIniziale not in ("M", "P"):
        raise ErroreCalcoloPianificazione("Blocco iniziale della pianificazione non valido")
    ordini = db.session.scalars(
        db.select(PianificazioneOrdini).where(PianificazioneOrdini.IdPianificazione == id_piano)
    ).all()
    if not ordini or any(o.Stato != "In produzione" for o in ordini):
        raise ErroreCalcoloPianificazione("Gli ordini del piano devono essere tutti in produzione")

    fasi_originali = db.session.scalars(
        db.select(PianificazioneFasi)
        .where(PianificazioneFasi.IdPianificazione == id_piano)
        .order_by(PianificazioneFasi.OffsetBlocchi, PianificazioneFasi.IdPianificazioneFase)
    ).all()
    if not fasi_originali:
        raise ErroreCalcoloPianificazione("La pianificazione non contiene fasi")

    offset_iniziale = 0 if piano.BloccoIniziale == "M" else 1
    fasi_nuove = {}
    for fase in fasi_originali:
        nuova_data_fase, indice = calcola_blocchi_fase(
            offset_iniziale, int(fase.OffsetBlocchi), nuova_data
        )
        fasi_nuove[fase.IdPianificazioneFase] = SimpleNamespace(
            IdPianificazioneFase=fase.IdPianificazioneFase,
            IdPianificazione=id_piano,
            IdRicettaFase=fase.IdRicettaFase,
            IdLavorazione=fase.IdLavorazione,
            OffsetBlocchi=fase.OffsetBlocchi,
            DataFase=nuova_data_fase,
            Blocco="M" if indice == 0 else "P",
        )

    nuove = list(fasi_nuove.values())
    fasi_impasto, (fase_forno, lavoro_forno, ricetta_fase_forno) = individua_fasi_risorse(nuove)
    # Gli ID fase restano invariati: le prenotazioni dettagliate li referenziano.
    prenotazioni = db.session.scalars(
        db.select(PianificazioneImpastatriceFase)
        .join(PianificazioneFasi,
              PianificazioneFasi.IdPianificazioneFase == PianificazioneImpastatriceFase.IdPianificazioneFase)
        .where(PianificazioneFasi.IdPianificazione == id_piano)
    ).all()
    legacy = db.session.scalars(
        db.select(PianificazioneOrdiniImpastatrici)
        .where(PianificazioneOrdiniImpastatrici.IdPianificazione == id_piano)
    ).all()
    ids_legacy = {r.IdImpastatrice for r in legacy}
    by_phase = defaultdict(lambda: defaultdict(list))
    for p in prenotazioni:
        by_phase[p.IdPianificazioneFase][p.IdImpastatrice].append(p)
    ids_dettaglio = {p.IdImpastatrice for p in prenotazioni}
    ids_macchine = ids_legacy | ids_dettaglio
    if not ids_macchine:
        raise ErroreCalcoloPianificazione("Nessuna impastatrice assegnata al piano")

    macchine = {
        m.IdImpastatrice: m for m in db.session.scalars(
            db.select(AnagraficaImpastatrici)
            .where(AnagraficaImpastatrici.IdImpastatrice.in_(ids_macchine))
        ).all()
    }
    if set(macchine) != ids_macchine:
        raise ErroreCalcoloPianificazione("Una delle impastatrici originali non è più disponibile nell'anagrafica")

    ids_fasi_impasto = {fase.IdPianificazioneFase for fase, _ in fasi_impasto}
    if any(p.IdPianificazioneFase not in ids_fasi_impasto for p in prenotazioni):
        raise ErroreCalcoloPianificazione(
            "Prenotazioni di impastatrici collegate a fasi non di impasto: "
            "ripianificazione bloccata per preservare le risorse originali"
        )
    if not prenotazioni:
        capacita_legacy = sum((_capacita_kg(macchine[i]) for i in ids_legacy), Decimal(0))
        if capacita_legacy < Decimal(str(piano.PesoTotale)) / Decimal(1000):
            raise ErroreCalcoloPianificazione(
                "La capacità attuale delle impastatrici storiche è inferiore al peso del piano"
            )

    # Con prenotazioni puntuali è vietato ricavare un'assegnazione diversa.
    # Per i piani storici senza righe puntuali si rispettano le associazioni legacy.
    assegnazioni = []
    for fase, lavorazione in fasi_impasto:
        fase_id = fase.IdPianificazioneFase
        macchina_fase = by_phase.get(fase_id, {})
        if prenotazioni and not macchina_fase:
            raise ErroreCalcoloPianificazione(
                "Prenotazioni incomplete: una fase di impasto è priva di impastatrici. "
                "Correggere lo storico prima di spostare il piano."
            )
        macchine_ids = set(macchina_fase) if prenotazioni else ids_legacy
        if not macchine_ids:
            raise ErroreCalcoloPianificazione("Fase di impasto senza macchine assegnate")
        for id_macchina in sorted(macchine_ids):
            disponibilita, _ = impastatrici_libere(
                [macchine[id_macchina]], [(fase, lavorazione)],
                escludi_id_pianificazione=id_piano,
            )
            if id_macchina not in disponibilita:
                raise ErroreCalcoloPianificazione(
                    f"Impastatrice {id_macchina} occupata nella nuova data "
                    f"({fase.DataFase:%d/%m/%Y}, blocco {fase.Blocco}, "
                    f"durata {int(lavorazione.DurataBlocchi)} blocchi)"
                )
            # Controllo che i kg assegnati siano ancora compatibili con la macchina.
            if macchina_fase:
                vecchie = macchina_fase[id_macchina]
                kg = {Decimal(str(p.KgAssegnati)) for p in vecchie}
                if len(kg) != 1:
                    raise ErroreCalcoloPianificazione("Prenotazioni con kg incoerenti")
                vecchi_slot = {(p.DataFase, p.Blocco) for p in vecchie}
                fase_originale = next(f for f in fasi_originali if f.IdPianificazioneFase == fase_id)
                previsti = set(_blocchi_occupati(
                    fase_originale.DataFase, fase_originale.Blocco, int(lavorazione.DurataBlocchi)
                ))
                if len(vecchie) != len(previsti) or vecchi_slot != previsti:
                    raise ErroreCalcoloPianificazione("Prenotazioni storiche incomplete o duplicate")
                quota_kg = next(iter(kg))
                if quota_kg > _capacita_kg(macchine[id_macchina]):
                    raise ErroreCalcoloPianificazione(
                        f"Impastatrice {id_macchina}: la capacità attuale è inferiore ai kg assegnati"
                    )
                assegnazioni.append((fase_id, id_macchina, quota_kg, _blocchi_occupati(
                    fase.DataFase, fase.Blocco, int(lavorazione.DurataBlocchi)
                )))

    cotte_libere, limite_forno = disponibilita_forno(
        fase_forno, lavoro_forno, ricetta_fase_forno, piano.IdForno,
        escludi_id_pianificazione=id_piano,
    )
    if int(piano.NumeroCotte) > cotte_libere:
        raise ErroreCalcoloPianificazione(
            f"Forno {piano.IdForno}: richieste {piano.NumeroCotte} cotte, "
            f"disponibili {cotte_libere} nella nuova data"
        )

    # Tutti gli slot delle fasi sono validati da individua_fasi_risorse.
    return {
        "piano": piano,
        "fasi_originali": fasi_originali,
        "fasi_nuove": fasi_nuove,
        "prenotazioni": prenotazioni,
        "assegnazioni": assegnazioni,
        "macchine": [macchine[i] for i in sorted(ids_macchine)],
        "cotte_libere": cotte_libere,
        "limite_forno": limite_forno,
        "nuova_data": nuova_data,
    }


def salva_ripianificazione(id_piano: int, nuova_data: date, operatore: str, motivo: str,
                           progressivo_lotto: str | None = None) -> None:
    """Verifica e sposta atomicamente. Non muta stock o righe ordine."""
    if not (5 <= len(motivo.strip()) <= 500):
        raise ErroreCalcoloPianificazione("La motivazione deve avere da 5 a 500 caratteri")
    # BEGIN IMMEDIATE prima della prima SELECT: blocca altri writer SQLite durante il controllo.
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    dati = prepara_ripianificazione(id_piano, nuova_data)
    piano = dati["piano"]
    precedente = piano.DataProduzione
    prenotazioni = dati["prenotazioni"]

    # Le UNIQUE su macchina/data/blocco non permettono modifiche in-place
    # quando vecchi e nuovi intervalli si sovrappongono: cancellare e ricreare
    # le occupazioni puntuali, conservando fase, macchina e kg.
    for p in prenotazioni:
        db.session.delete(p)
    db.session.flush()

    # Spostamento ordinato per evitare collisioni transitorie anche con
    # UNIQUE(IdPianificazione, DataFase, Blocco) nelle fasi del medesimo piano.
    old_step = precedente.toordinal() * 2 + (piano.BloccoIniziale == "P")
    new_step = nuova_data.toordinal() * 2 + (piano.BloccoIniziale == "P")
    fasi = sorted(
        dati["fasi_originali"],
        key=lambda f: (f.DataFase, f.Blocco),
        reverse=new_step > old_step,
    )
    for fase in fasi:
        nuova = dati["fasi_nuove"][fase.IdPianificazioneFase]
        fase.DataFase = nuova.DataFase
        fase.Blocco = nuova.Blocco
        db.session.flush()
    piano.DataProduzione = nuova_data

    for fase_id, macchina_id, kg, slots in dati["assegnazioni"]:
        for giorno, blocco in slots:
            db.session.add(PianificazioneImpastatriceFase(
                IdPianificazioneFase=fase_id,
                IdImpastatrice=macchina_id,
                DataFase=giorno,
                Blocco=blocco,
                KgAssegnati=kg,
            ))
    db.session.add(StoricoRipianificazioni(
        IdPianificazione=id_piano,
        DataPrecedente=precedente,
        DataNuova=nuova_data,
        BloccoIniziale=piano.BloccoIniziale,
        Operatore=operatore,
        DataOperazione=datetime.now(),
        Motivo=motivo.strip(),
    ))
    # Rigenera il lotto solo dopo aver calcolato le nuove fasi e prima del COMMIT.
    # Il precedente resta occupato nello storico e non viene riutilizzato.
    fasi_impasto_nuove, _ = individua_fasi_risorse(list(dati['fasi_nuove'].values()))
    assegna_lotto(
        tipo='TRAD', id_owner=id_piano,
        data_impasto=prima_data_impasto_fasi(fasi_impasto_nuove),
        progressivo=progressivo_lotto, operatore=operatore, motivo=motivo,
    )
    db.session.commit()


def anteprima_visuale_ripianificazione(id_piano: int, nuova_data: date, *, mostra_articoli_cnf: bool = True) -> dict:
    """Agenda e stato macchine in sola lettura, anche quando la data ha conflitti.

    La conferma NON si basa su questo responso: salva_ripianificazione ripete
    tutti i controlli nella transazione SQLite. Gli slot del piano stesso
    vengono esclusi dai controlli e dall'elenco degli eventi confermati.
    """
    piano = db.session.get(PianificazioneProduzione, id_piano)
    if piano is None:
        raise ErroreCalcoloPianificazione("Pianificazione non trovata")
    if piano.StatoRigaOrdine != "In produzione":
        raise ErroreCalcoloPianificazione("La pianificazione non è attiva")

    fasi_originali = db.session.scalars(
        db.select(PianificazioneFasi)
        .where(PianificazioneFasi.IdPianificazione == id_piano)
        .order_by(PianificazioneFasi.OffsetBlocchi, PianificazioneFasi.IdPianificazioneFase)
    ).all()
    if not fasi_originali or piano.BloccoIniziale not in ("M", "P"):
        raise ErroreCalcoloPianificazione("Fasi o blocco iniziale non validi")

    indice_iniziale = 0 if piano.BloccoIniziale == "M" else 1
    fasi_nuove = []
    for originale in fasi_originali:
        giorno, indice = calcola_blocchi_fase(
            indice_iniziale, int(originale.OffsetBlocchi), nuova_data,
        )
        fasi_nuove.append(SimpleNamespace(
            IdPianificazioneFase=originale.IdPianificazioneFase,
            IdPianificazione=id_piano,
            IdRicettaFase=originale.IdRicettaFase,
            IdLavorazione=originale.IdLavorazione,
            OffsetBlocchi=originale.OffsetBlocchi,
            DataFase=giorno,
            Blocco="M" if indice == 0 else "P",
        ))

    ids_lavorazioni = {fase.IdLavorazione for fase in fasi_nuove}
    lavorazioni = {
        lavorazione.IdLavorazione: lavorazione
        for lavorazione in db.session.scalars(
            db.select(TipologieLavorazione)
            .where(TipologieLavorazione.IdLavorazione.in_(ids_lavorazioni))
        ).all()
    }
    if set(lavorazioni) != ids_lavorazioni:
        raise ErroreCalcoloPianificazione("Lavorazioni della ricetta non disponibili")

    # L'agenda rimane visibile anche in presenza di conflitti: non iniziare
    # con prepara_ripianificazione, che al primo conflitto solleva un errore.
    date_interessate = {
        giorno
        for fase in fasi_nuove
        for giorno, _ in _blocchi_occupati(
            fase.DataFase, fase.Blocco, int(lavorazioni[fase.IdLavorazione].DurataBlocchi)
        )
    }
    prima, ultima = min(date_interessate), max(date_interessate)
    durata_massima = db.session.scalar(db.select(db.func.max(TipologieLavorazione.DurataBlocchi)))
    giorni_precedenti = (max(1, int(durata_massima or 1)) + 1) // 2
    righe_esistenti = db.session.execute(
        db.select(PianificazioneFasi, PianificazioneProduzione, TipologieLavorazione)
        .join(PianificazioneProduzione,
              PianificazioneProduzione.IdPianificazione == PianificazioneFasi.IdPianificazione)
        .join(TipologieLavorazione,
              TipologieLavorazione.IdLavorazione == PianificazioneFasi.IdLavorazione)
        .where(
            PianificazioneFasi.DataFase >= prima - timedelta(days=giorni_precedenti),
            PianificazioneFasi.DataFase <= ultima,
            PianificazioneProduzione.IdPianificazione != id_piano,
            PianificazioneProduzione.StatoRigaOrdine != "Annullato",
        )
        .order_by(PianificazioneFasi.DataFase, PianificazioneFasi.Blocco)
    ).all()
    giorni = organizza_eventi_giorni_interessati(
        fasi_nuove, lavorazioni, righe_esistenti, piano.CodArt,
        mostra_articoli_cnf=mostra_articoli_cnf,
    )
    for giorno in giorni:
        for eventi in giorno["slot"].values():
            for evento in eventi:
                if evento["bozza"]:
                    evento["id_pianificazione"] = id_piano
                    if evento["categoria"] == "forno":
                        evento["cotte"] = piano.NumeroCotte
                        evento["forno"] = piano.IdForno

    impastatrici = db.session.scalars(
        db.select(AnagraficaImpastatrici).order_by(AnagraficaImpastatrici.IdImpastatrice)
    ).all()
    ids_macchine = {m.IdImpastatrice for m in impastatrici}
    ids_associati = set(db.session.scalars(
        db.select(PianificazioneOrdiniImpastatrici.IdImpastatrice)
        .where(PianificazioneOrdiniImpastatrici.IdPianificazione == id_piano)
    ).all())
    ids_associati.update(db.session.scalars(
        db.select(PianificazioneImpastatriceFase.IdImpastatrice)
        .join(PianificazioneFasi,
              PianificazioneFasi.IdPianificazioneFase == PianificazioneImpastatriceFase.IdPianificazioneFase)
        .where(PianificazioneFasi.IdPianificazione == id_piano)
    ).all())

    # Stesse sorgenti di occupazione di impastatrici_libere, ma riportiamo
    # anche quali blocchi causano l'indisponibilita', non solo un booleano.
    occupate = set(db.session.execute(
        db.select(
            PianificazioneImpastatriceFase.IdImpastatrice,
            PianificazioneImpastatriceFase.DataFase,
            PianificazioneImpastatriceFase.Blocco,
        )
        .join(PianificazioneFasi,
              PianificazioneFasi.IdPianificazioneFase == PianificazioneImpastatriceFase.IdPianificazioneFase)
        .where(
            PianificazioneImpastatriceFase.IdImpastatrice.in_(ids_macchine),
            PianificazioneFasi.IdPianificazione != id_piano,
        )
    ).all())
    occupate |= _occupazioni_impastatrici_storiche(ids_macchine, id_piano)
    fasi_impasto = []
    for fase in fasi_nuove:
        lavoro = lavorazioni[fase.IdLavorazione]
        nome_lavorazione = " ".join(str(v or "") for v in (
            lavoro.TipologiaLavorazione, lavoro.CodiceLavorazione,
            lavoro.DescrizioneLavorazione,
        )).casefold()
        if "impast" in nome_lavorazione:
            fasi_impasto.append((fase, lavoro))
    slots_impasti = sorted({
        slot
        for fase, lavoro in fasi_impasto
        for slot in _blocchi_occupati(fase.DataFase, fase.Blocco, int(lavoro.DurataBlocchi))
    })
    stati_macchine = []
    for macchina in impastatrici:
        conflitti = [slot for slot in slots_impasti
                     if (macchina.IdImpastatrice, slot[0], slot[1]) in occupate]
        stati_macchine.append({
            "macchina": macchina,
            "assegnata": macchina.IdImpastatrice in ids_associati,
            "libera": not conflitti,
            "conflitti": conflitti,
        })

    # Nella lista mostra prima le macchine gia' vincolate al piano.
    stati_macchine.sort(key=lambda r: (not r["assegnata"], r["macchina"].IdImpastatrice))

    cotte_libere = None
    limite_forno = None
    errore_forno = None
    try:
        _, (fase_forno, lavoro_forno, ricetta_fase_forno) = individua_fasi_risorse(fasi_nuove)
        cotte_libere, limite_forno = disponibilita_forno(
            fase_forno, lavoro_forno, ricetta_fase_forno, piano.IdForno,
            escludi_id_pianificazione=id_piano,
        )
    except ErroreCalcoloPianificazione as exc:
        errore_forno = str(exc)

    # Questa e' la validazione autorevole, riutilizzata identica in conferma.
    errore = None
    valido = False
    try:
        prepara_ripianificazione(id_piano, nuova_data)
        valido = True
    except ErroreCalcoloPianificazione as exc:
        errore = str(exc)

    return {
        "piano": piano,
        "nuova_data": nuova_data,
        "agenda": {"selezionata": nuova_data, "giorni": giorni},
        "stati_macchine": stati_macchine,
        "ids_associati": ids_associati,
        "fasi": list(zip(fasi_originali, fasi_nuove)),
        "cotte_libere": cotte_libere,
        "limite_forno": limite_forno,
        "errore_forno": errore_forno,
        "errore": errore,
        "valido": valido,
    }
