from typing import (
    Any,
    Literal,
)

from sqlalchemy import and_
from sqlalchemy.orm import joinedload

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    CapacitaForno,
    Coefficienti,
    OrdiniCliente,
    OrdiniPriorita,
    PianificazioneOrdini,
    RicettaFasi,
    StatoRigaOrdine,
    TipLavorazioniAnagForni,
    TipologiaRicette,
    TipRicetteAnagArticoli,
)

date_format = "%d-%m-%Y"


class ErroreCalcoloRiepilogo(Exception):
    pass


def crea_riga_tabella(
    ordine: OrdiniCliente,
    quantita: int,
    stato: str,
    id_pianificazione: int | None = None,
    priorita: int | None = None,
) -> dict[str, Any]:

    return {
        "IdDocumento": ordine.IdDocumento,
        "IdRigaDoc": ordine.IdRigaDoc,
        "IdPianificazione": id_pianificazione,
        "Priorita": priorita,
        "DataRegistrazione": ordine.DataRegistrazione,
        "NumRegistraz": ordine.NumRegistraz,
        "Cliente": ordine.Cliente.RagioneSociale,
        "CodArt": ordine.CodArt,
        "DesArt": ordine.DesArt,
        "DataConsegna": ordine.DataConsegna.strftime(date_format),
        "QTA_ORD": ordine.QTA_ORD,
        "QTA_Saldo": quantita,
        "Prezzatura": ordine.articolo.Prezzatura,
        "Glassatura": ("Si" if ordine.articolo.Glassatura == 1 else "No"),
        "Stato": stato,
    }



def recupera_priorita_ordini() -> dict[tuple[int, int], int]:
    """Lettura delle priorità locali in una sola query."""
    righe = db.session.execute(
        db.select(
            OrdiniPriorita.IdDocumento,
            OrdiniPriorita.IdRigaDoc,
            OrdiniPriorita.Livello,
        )
    ).all()
    return {(doc, riga): livello for doc, riga, livello in righe}


def recupera_righe_in_produzione() -> list[dict[str, Any]]:
    """Una riga per pianificazione, con quantita' sommate per gli ordini attivi.

    I record PianificazioneOrdini rimangono distinti nel DB: raggruppiamo
    solamente i dati destinati alla tabella HTML.
    """
    stmt = (
        db.select(PianificazioneOrdini, OrdiniCliente)
        .join(
            OrdiniCliente,
            and_(
                PianificazioneOrdini.IdDocumento == OrdiniCliente.IdDocumento,
                PianificazioneOrdini.IdRiga == OrdiniCliente.IdRigaDoc,
            ),
        )
        .options(
            joinedload(OrdiniCliente.Cliente),
            joinedload(OrdiniCliente.articolo),
        )
        .where(PianificazioneOrdini.Stato == "In produzione")
        .order_by(
            OrdiniCliente.DataConsegna.asc(),
            PianificazioneOrdini.IdPianificazione.asc(),
        )
    )
    risultati = db.session.execute(stmt).all()

    gruppi: dict[int, list[tuple[PianificazioneOrdini, OrdiniCliente]]] = {}
    for associazione, ordine in risultati:
        if associazione.QtaPianificata is None:
            raise ErroreCalcoloRiepilogo(
                "Quantità mancante nella pianificazione "
                f"{associazione.IdPianificazione}, "
                f"ordine {ordine.IdDocumento}-{ordine.IdRigaDoc}"
            )
        gruppi.setdefault(associazione.IdPianificazione, []).append((associazione, ordine))

    righe_tabella = []
    for id_pianificazione, voci in gruppi.items():
        primo_ordine = voci[0][1]
        totale_assegnato = sum(int(assoc.QtaPianificata) for assoc, _ in voci)
        totale_ordinato = sum(int(ordine.QTA_ORD or 0) for _, ordine in voci)
        nomi_clienti = list(dict.fromkeys(ordine.Cliente.RagioneSociale for _, ordine in voci))
        descrizioni = list(dict.fromkeys(ordine.DesArt for _, ordine in voci if ordine.DesArt))
        codici = list(dict.fromkeys(ordine.CodArt for _, ordine in voci))
        consegna_minima = min(ordine.DataConsegna for _, ordine in voci)
        consegna_massima = max(ordine.DataConsegna for _, ordine in voci)

        riga = crea_riga_tabella(
            ordine=primo_ordine,
            quantita=totale_assegnato,
            stato="In produzione",
            id_pianificazione=id_pianificazione,
        )
        riga.update(
            Cliente=" · ".join(nomi_clienti),
            DesArt=" · ".join(descrizioni),
            CodArt=" · ".join(codici),
            DataConsegna=consegna_minima.strftime(date_format),
            QTA_ORD=totale_ordinato,
            NumeroOrdini=len(voci),
            ConsegnaUltima=consegna_massima.strftime(date_format),
            OrdiniDettaglio=[
                {
                    "IdDocumento": ordine.IdDocumento,
                    "IdRigaDoc": ordine.IdRigaDoc,
                    "Cliente": ordine.Cliente.RagioneSociale,
                    "DesArt": ordine.DesArt,
                    "DataConsegna": ordine.DataConsegna.strftime(date_format),
                    "QtaPianificata": int(associazione.QtaPianificata),
                }
                for associazione, ordine in voci
            ],
        )
        righe_tabella.append(riga)

    return righe_tabella


def dati_tabella_produzione(
    stato: str,
) -> list[dict[str, Any]]:

    if stato == "Nuovo":
        # Recupero tutti gli ordini ERP ordinati per consegna
        stmt = (
            db.select(OrdiniCliente)
            .options(
                joinedload(OrdiniCliente.Cliente),
                joinedload(OrdiniCliente.articolo),
            )
            .order_by(
                OrdiniCliente.DataConsegna.asc(),
                OrdiniCliente.IdDocumento.asc(),
                OrdiniCliente.IdRigaDoc.asc(),
            )
        )

        ordini = db.session.scalars(stmt).all()

        # Quantità già impegnate nelle pianificazioni
        quantita_impegnate = recupera_quantita_impegnate()
        priorita_ordini = recupera_priorita_ordini()

        righe_tabella = []

        for ordine in ordini:
            chiave = (
                ordine.IdDocumento,
                ordine.IdRigaDoc,
            )

            impegnata = quantita_impegnate.get(chiave, 0)

            if ordine.QTA_SALDO_DOC is None:
                raise ErroreCalcoloRiepilogo(f"Saldo mancante per l'ordine {chiave}")

            quantita_nuova = max(
                0,
                ordine.QTA_SALDO_DOC - impegnata,
            )

            # Un ordine completamente assegnato
            # non compare tra quelli da produrre
            if quantita_nuova <= 0:
                continue

            riga = crea_riga_tabella(
                ordine=ordine,
                quantita=quantita_nuova,
                stato="Nuovo",
                priorita=priorita_ordini.get(chiave),
            )
            riga["DataConsegnaISO"] = ordine.DataConsegna.date().isoformat()
            riga["ResiduoParziale"] = quantita_nuova < ordine.QTA_ORD
            righe_tabella.append(riga)

        righe_tabella.sort(
            key=lambda riga: (
                riga["Priorita"] if riga["Priorita"] is not None else 4,
                riga["DataConsegnaISO"],
                riga["IdDocumento"],
                riga["IdRigaDoc"],
            )
        )
        return righe_tabella

    if stato == "In produzione":
        return recupera_righe_in_produzione()

    raise ValueError(f"Stato non gestito: {stato}")


def determina_tipologia_panettone(glassatura: Any) -> Literal["TO", "MI"]:
    glassatura = int(glassatura)
    if glassatura == 1:
        return "TO"
    return "MI"


def trova_capacita_forno(
    ordine: OrdiniCliente, tipologia_panettone: Literal["TO", "MI"]
) -> tuple[CapacitaForno, TipologiaRicette]:
    # 1
    stmt_ricetta_articolo = db.select(TipRicetteAnagArticoli).where(
        TipRicetteAnagArticoli.CodArt == ordine.CodArt
    )
    ricette_articoli_selezionati: TipRicetteAnagArticoli | None = db.session.scalars(
        stmt_ricetta_articolo
    ).one_or_none()

    if ricette_articoli_selezionati is None:
        raise ErroreCalcoloRiepilogo("Nessun articolo selezionato")

    # 2
    stmt_tipologia_ricetta = db.select(TipologiaRicette).where(
        TipologiaRicette.IdRicetta == ricette_articoli_selezionati.IdRicetta
    )
    ricetta_selezionata: TipologiaRicette | None = db.session.scalars(
        stmt_tipologia_ricetta
    ).one_or_none()

    if ricetta_selezionata is None:
        raise ErroreCalcoloRiepilogo("Nessuna ricetta selezionata")

    # 3
    stmt_ricette_fasi = db.select(RicettaFasi).where(
        RicettaFasi.IdRicetta == ricetta_selezionata.IdRicetta
    )
    fasi_selezionate = db.session.scalars(stmt_ricette_fasi).all()

    # 4
    for fase in fasi_selezionate:
        lavorazione = fase.lavorazione
        if lavorazione.TipologiaLavorazione == "Forno":
            # 5
            stmt_lavorazioni_forno = db.select(TipLavorazioniAnagForni).where(
                TipLavorazioniAnagForni.IdLavorazione == lavorazione.IdLavorazione
            )
            lavorazioni_forno = db.session.scalars(stmt_lavorazioni_forno).all()
            # 6
            id_forni = [associazione.IdForno for associazione in lavorazioni_forno]
            # 7
            stmt_forno = db.select(CapacitaForno).where(
                CapacitaForno.IdForno.in_(id_forni),
                CapacitaForno.DimensionePanettone == ordine.articolo.Prezzatura,
                CapacitaForno.TipologiaPanettone == tipologia_panettone,
            )
            forno: CapacitaForno | None = db.session.scalars(stmt_forno).one_or_none()
            if forno is None:
                raise ErroreCalcoloRiepilogo("Non è stato possibile trovare la capacità del forno")

            return forno, ricetta_selezionata
        else:
            pass
    raise ErroreCalcoloRiepilogo("Capacità forno non trovata")


def calcolo_infornate(capacita_panettone: int, quantita_panettoni: int):
    if not capacita_panettone:
        raise ErroreCalcoloRiepilogo("La capacità dei panettoni non è stata inserita o è 0")
    if capacita_panettone <= 0:
        raise ErroreCalcoloRiepilogo("La capacità dei panettoni è negativa")
    numero_infornate = quantita_panettoni // capacita_panettone
    panettoni_restanti = quantita_panettoni % capacita_panettone
    if panettoni_restanti:
        numero_infornate += 1
    return numero_infornate, panettoni_restanti


def recupera_ordini_selezionati(identificativi: list[str]) -> list[OrdiniCliente]:
    if not identificativi:
        raise ErroreCalcoloRiepilogo("Nessun ordine selezionato")
    list_ordini: list[OrdiniCliente] = []
    for ordine in identificativi:
        IdDocumento, IdRiga = ordine.split("-")
        IdDocumento, IdRiga = int(IdDocumento), int(IdRiga)
        stmt_ordine_selezionato = db.select(OrdiniCliente).where(
            OrdiniCliente.IdDocumento == IdDocumento, OrdiniCliente.IdRigaDoc == IdRiga
        )
        ordine_selezionato: OrdiniCliente | None = db.session.scalars(
            stmt_ordine_selezionato
        ).one_or_none()
        if not ordine_selezionato:
            raise ErroreCalcoloRiepilogo(
                f"Non è stato possibile selezionare l'ordine corrente {IdDocumento, IdRiga}"
            )
        list_ordini.append(ordine_selezionato)
    return list_ordini


def panettoni_per_completare_cotta(quantita_panettoni: int, capacita_forno: int) -> int:
    """Quanti pezzi mancano alla prossima cotta completa (0 se gia' multiplo)."""
    if capacita_forno <= 0:
        raise ErroreCalcoloRiepilogo("La capienza del forno deve essere positiva")
    if quantita_panettoni < 0:
        raise ErroreCalcoloRiepilogo("La quantità di panettoni non può essere negativa")
    return (-quantita_panettoni) % capacita_forno


def calcolo_riepilogo_ordini(list_ordini_selezionati: list[OrdiniCliente]):
    if not list_ordini_selezionati:
        raise ErroreCalcoloRiepilogo("Nessun ordine selezionato")

    chiavi = [(ordine.IdDocumento, ordine.IdRigaDoc) for ordine in list_ordini_selezionati]
    if len(chiavi) != len(set(chiavi)):
        raise ErroreCalcoloRiepilogo("Sono presenti ordini selezionati più volte")

    codici_articolo_complessivo = {ordine.CodArt for ordine in list_ordini_selezionati}
    prezzature_complessivo = {ordine.articolo.Prezzatura for ordine in list_ordini_selezionati}
    valori_glassatura: set[int] = {ordine.articolo.Glassatura for ordine in list_ordini_selezionati}

    if len(valori_glassatura) != 1:
        raise ErroreCalcoloRiepilogo(
            "Sono stati selezionati ordini di formato eterogeneo (Torino - Milano)"
        )
    if len(codici_articolo_complessivo) != 1:
        raise ErroreCalcoloRiepilogo("Il numero di codici selezionati è eterogeneo")
    if len(prezzature_complessivo) != 1:
        raise ErroreCalcoloRiepilogo("Il numero di prezzature selezionate è eterogeneo")

    tipologia_panettone = determina_tipologia_panettone(next(iter(valori_glassatura)))
    quantita_impegnate = recupera_quantita_impegnate()
    ordini_riepilogo = []
    qta_panettoni = 0
    peso_impasto = 0

    stmt_coefficiente_glassatura = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_GLASSATURA"
    )
    coefficiente_glassatura = db.session.scalar(stmt_coefficiente_glassatura)
    if coefficiente_glassatura is None:
        raise ErroreCalcoloRiepilogo("Il coefficiente di glassatura non è stato trovato")

    stmt_coefficiente_peso_totale = db.select(Coefficienti).where(
        Coefficienti.Coefficiente == "PERCENTUALE_PESO_AGGIUNTIVO_IMPASTO"
    )
    coefficiente_peso_totale = db.session.scalar(stmt_coefficiente_peso_totale)
    if coefficiente_peso_totale is None:
        raise ErroreCalcoloRiepilogo("Il coefficiente di peso totale non è stato trovato")

    for ordine in sorted(
        list_ordini_selezionati,
        key=lambda elemento: (elemento.DataConsegna, elemento.IdDocumento, elemento.IdRigaDoc),
    ):
        chiave = (ordine.IdDocumento, ordine.IdRigaDoc)
        if ordine.QTA_SALDO_DOC is None:
            raise ErroreCalcoloRiepilogo(f"Saldo mancante per l'ordine {chiave}")
        impegnata = quantita_impegnate.get(chiave, 0)
        disponibile = max(0, int(ordine.QTA_SALDO_DOC) - int(impegnata))

        ordini_riepilogo.append({
            "IdDocumento": ordine.IdDocumento,
            "IdRigaDoc": ordine.IdRigaDoc,
            "Cliente": ordine.Cliente.RagioneSociale,
            "CodArt": ordine.CodArt,
            "DesArt": ordine.DesArt,
            "DataConsegna": ordine.DataConsegna.strftime(date_format),
            "QTA_ORD": ordine.QTA_ORD,
            "QTA_SALDO_DOC": ordine.QTA_SALDO_DOC,
            "QtaImpegnata": impegnata,
            "QtaDisponibile": disponibile,
        })

        qta_panettoni += disponibile
        # Mantiene la formula del peso gia' utilizzata dall'applicazione,
        # ma la applica alle quantita' realmente disponibili.
        peso_impasto += (
            (disponibile * ordine.articolo.Prezzatura)
            if ordine.articolo.Glassatura == 1
            else (
                disponibile
                * ordine.articolo.Prezzatura
                * coefficiente_glassatura.Valore
            )
        )

    if qta_panettoni <= 0:
        raise ErroreCalcoloRiepilogo("Gli ordini selezionati non hanno quantità residue disponibili")

    peso_totale_grammi = peso_impasto * coefficiente_peso_totale.Valore
    peso_impasto_kg = peso_totale_grammi / 1000
    forno, ricetta = trova_capacita_forno(list_ordini_selezionati[0], tipologia_panettone)
    numero_cotte, _ = calcolo_infornate(forno.Capienza, qta_panettoni)
    panettoni_mancanti = panettoni_per_completare_cotta(qta_panettoni, forno.Capienza)

    return {
        "numero_ordini": len(list_ordini_selezionati),
        "ordini_riepilogo": ordini_riepilogo,
        "qta_panettoni": qta_panettoni,
        "peso_impasto": peso_impasto_kg,
        "peso_totale_grammi": peso_totale_grammi,
        "numero_cotte": numero_cotte,
        "panettoni_mancanti_cotta": panettoni_mancanti,
        "capienza_cotta": forno.Capienza,
        "tipologia_panettone": tipologia_panettone,
        "id_forno": forno.IdForno,
        "codart_ordine": next(iter(codici_articolo_complessivo)),
        "id_ricetta": ricetta.IdRicetta,
    }


def calcola_quantita_residua_ordine(
    qta_residua: int, qta_pianificata: int, qta_saldo_documento: int
):
    quantita_ordine_residua = max(0, qta_saldo_documento - qta_pianificata)

    quantita_da_assegnare = min(
        quantita_ordine_residua,
        qta_residua,
    )
    qta_residua -= quantita_da_assegnare
    if quantita_da_assegnare <= 0:
        return 0
    return quantita_da_assegnare


def recupera_quantita_impegnate() -> dict[tuple[int, int], int]:
    """Totale delle quantità associate a pianificazioni valide per ordine."""
    stmt = (
        db.select(
            PianificazioneOrdini.IdDocumento,
            PianificazioneOrdini.IdRiga,
            db.func.coalesce(
                db.func.sum(PianificazioneOrdini.QtaPianificata), 0
            ).label("quantita_impegnata"),
        )
        .where(PianificazioneOrdini.Stato.in_(("In produzione", "Chiuso")))
        .group_by(
            PianificazioneOrdini.IdDocumento,
            PianificazioneOrdini.IdRiga,
        )
    )
    risultati = db.session.execute(stmt).all()
    return {
        (r.IdDocumento, r.IdRiga): r.quantita_impegnata
        for r in risultati
    }
