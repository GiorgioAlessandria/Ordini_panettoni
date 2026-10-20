"""Catalogo ESPLICITO delle sole tabelle configurabili dall'interfaccia.

Non include ordini, clienti, articoli sincronizzati, pianificazioni e stock:
non sono impostazioni amministrative.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, func, inspect, select

from flaskr.extensions import db
from flaskr.models.model_ordini import (
    AnagraficaArticoli,
    AnagraficaForni,
    AnagraficaImpastatrici,
    CapacitaForno,
    Coefficienti,
    Costanti,
    PianificazioneFasi,
    RicettaFasi,
    TipologieLavorazione,
    TipLavorazioniAnagForni,
    TipologiaRicette,
    TipRicetteAnagArticoli,
)


class ErroreImpostazioni(ValueError):
    pass


@dataclass(frozen=True)
class Campo:
    nome: str
    etichetta: str
    tipo: str = 'text'  # text / integer / decimal / select / fk
    obbligatorio: bool = True
    minimo: int | None = None
    massimo: int | None = None
    lunghezza: int = 180
    scelte: tuple[tuple[str, str], ...] = ()
    modello_riferito: Any = None
    attributo_label: str | None = None
    attributo_sub: str | None = None
    aiuto: str = ''


@dataclass(frozen=True)
class Sezione:
    slug: str
    titolo: str
    gruppo: str
    descrizione: str
    modello: Any
    campi: tuple[Campo, ...]
    indice_label: tuple[str, ...] = ()
    consentire_eliminazione: bool = True
    tipo_operazione: str = 'standard'


def C(nome, etichetta, tipo='text', **kw):
    return Campo(nome, etichetta, tipo, **kw)


SEZIONI: dict[str, Sezione] = {
    'ricette': Sezione('ricette', 'Ricette', 'Ricette e lavorazioni',
        'Definisci una ricetta; poi collega gli articoli e aggiungi le sue fasi.',
        TipologiaRicette, (
            C('Ricetta', 'Codice / nome ricetta'),
            C('Descrizione', 'Descrizione ricetta', lunghezza=400),
            C('Standard', 'Standard', 'select', scelte=(('0', 'No'), ('1', 'Sì'))),
        ), ('Ricetta', 'Descrizione')),
    'lavorazioni': Sezione('lavorazioni', 'Lavorazioni', 'Ricette e lavorazioni',
        'Tipologie di lavorazione da utilizzare nelle fasi delle ricette.',
        TipologieLavorazione, (
            C('CodiceLavorazione', 'Codice lavorazione'),
            C('DescrizioneLavorazione', 'Descrizione', lunghezza=400),
            C('TipologiaLavorazione', 'Categoria lavorazione', aiuto='Per le lavorazioni in forno usare esattamente "Forno".'),
            C('DurataBlocchi', 'Durata in blocchi', 'integer', minimo=1),
            C('RichiedeOperatore', 'Richiede operatore', 'select', scelte=(('0', 'No'), ('1', 'Sì'))),
        ), ('CodiceLavorazione', 'DescrizioneLavorazione')),
    'fasi': Sezione('fasi', 'Fasi delle ricette', 'Ricette e lavorazioni',
        'Crea le fasi in sequenza: prima fase con offset 0; offset successivi crescenti.',
        RicettaFasi, (
            C('IdRicetta', 'Ricetta', 'fk', modello_riferito=TipologiaRicette, attributo_label='Ricetta', attributo_sub='Descrizione'),
            C('IdFase', 'Numero fase', 'integer', minimo=0),
            C('IdLavorazione', 'Lavorazione', 'fk', modello_riferito=TipologieLavorazione, attributo_label='DescrizioneLavorazione', attributo_sub='CodiceLavorazione'),
            C('OffsetBlocchi', 'Offset blocchi', 'integer', minimo=0),
            C('CapacitaMax', 'Capacità massima (configurazione fase)', 'integer', minimo=0),
        ), ('IdRicetta', 'IdFase')),
    'articoli-ricette': Sezione('articoli-ricette', 'Articoli ↔ ricette', 'Collegamenti',
        'Collega un articolo sincronizzato dall’ERP a una ricetta. Una sola ricetta per articolo, come richiesto dal calcolo produttivo.',
        TipRicetteAnagArticoli, (
            C('CodArt', 'Articolo ERP', 'fk', modello_riferito=AnagraficaArticoli, attributo_label='CodArt', attributo_sub='DesArt'),
            C('IdRicetta', 'Ricetta', 'fk', modello_riferito=TipologiaRicette, attributo_label='Ricetta', attributo_sub='Descrizione'),
        ), ('CodArt', 'IdRicetta'), tipo_operazione='associazione_articolo'),
    'forni-lavorazioni': Sezione('forni-lavorazioni', 'Forni ↔ lavorazioni', 'Collegamenti',
        'Definisci quali lavorazioni possono utilizzare ogni forno.',
        TipLavorazioniAnagForni, (
            C('IdForno', 'Forno', 'fk', modello_riferito=AnagraficaForni, attributo_label='ModelloForno', attributo_sub='DescrizioneForno'),
            C('IdLavorazione', 'Lavorazione', 'fk', modello_riferito=TipologieLavorazione, attributo_label='DescrizioneLavorazione', attributo_sub='CodiceLavorazione'),
        ), ('IdForno', 'IdLavorazione')),
    'impastatrici': Sezione('impastatrici', 'Impastatrici', 'Macchine e capacità',
        'Anagrafica e portata nominale delle impastatrici.',
        AnagraficaImpastatrici, (
            C('ModelloImpastatrice', 'Modello'),
            C('DescrizioneImpastatrice', 'Descrizione', lunghezza=400),
            C('CapienzaImpastatrice', 'Capacità nominale', 'integer', minimo=1),
            C('UdM', 'Unità di misura', 'select', scelte=(('kg','kg'), ('g','g'))),
        ), ('ModelloImpastatrice', 'CapienzaImpastatrice')),
    'forni': Sezione('forni', 'Forni', 'Macchine e capacità',
        'Crea un forno e poi configura le capacità per ogni formato e tipologia.',
        AnagraficaForni, (
            C('ModelloForno', 'Modello'),
            C('DescrizioneForno', 'Descrizione', lunghezza=400),
        ), ('ModelloForno', 'DescrizioneForno')),
    'capacita-forni': Sezione('capacita-forni', 'Capacità forni', 'Macchine e capacità',
        'Numero di panettoni per cotta in base a forno, prezzatura e formato.',
        CapacitaForno, (
            C('IdForno', 'Forno', 'fk', modello_riferito=AnagraficaForni, attributo_label='ModelloForno', attributo_sub='DescrizioneForno'),
            C('DimensionePanettone', 'Prezzatura (grammi)', 'integer', minimo=1),
            C('TipologiaPanettone', 'Tipologia', 'select', scelte=(('TO','Torino'), ('MI','Milano'))),
            C('Capienza', 'Panettoni per cotta', 'integer', minimo=1),
        ), ('IdForno', 'DimensionePanettone', 'TipologiaPanettone', 'Capienza')),
    'coefficienti': Sezione('coefficienti', 'Coefficienti', 'Parametri produttivi',
        'Parametri dei calcoli dei pesi. Valore numerico: controlla attentamente ogni variazione.',
        Coefficienti, (
            C('Coefficiente', 'Nome coefficiente'),
            C('Valore', 'Valore', 'decimal', aiuto='Usa il valore moltiplicativo previsto dalla formula (es. 1,10).'),
            C('UdM', 'Unità di misura'),
        ), ('Coefficiente', 'Valore')),
    'costanti': Sezione('costanti', 'Costanti', 'Parametri produttivi',
        'Costanti intere utilizzate dal sistema di pianificazione.',
        Costanti, (
            C('Descrizione', 'Descrizione costante'),
            C('Valore', 'Valore intero', 'integer', minimo=0),
        ), ('Descrizione', 'Valore')),
}

GRUPPI = ('Ricette e lavorazioni', 'Collegamenti', 'Macchine e capacità', 'Parametri produttivi')


def chiavi_pk(sezione: Sezione) -> tuple[str, ...]:
    return tuple(c.key for c in inspect(sezione.modello).primary_key)


def codifica_pk(sezione: Sezione, oggetto: Any) -> str:
    dati = [getattr(oggetto, nome) for nome in chiavi_pk(sezione)]
    encoded = json.dumps(dati, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(encoded).decode('ascii').rstrip('=')


def decodifica_pk(sezione: Sezione, token: str) -> Any:
    if not token or len(token) > 500:
        raise ErroreImpostazioni('Identificativo non valido')
    try:
        payload = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        values = json.loads(payload)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ErroreImpostazioni('Identificativo non valido') from exc
    if not isinstance(values, list) or len(values) != len(chiavi_pk(sezione)):
        raise ErroreImpostazioni('Identificativo non valido')
    keys = []
    for pk, value in zip(chiavi_pk(sezione), values):
        col = sezione.modello.__table__.c[pk]
        if col.type.python_type is int:
            if type(value) is not int:
                raise ErroreImpostazioni('Identificativo non valido')
        elif not isinstance(value, str) or not value or len(value) > 180:
            raise ErroreImpostazioni('Identificativo non valido')
        keys.append(value)
    return keys[0] if len(keys) == 1 else tuple(keys)


def etichetta_riga(sezione: Sezione, record: Any) -> str:
    return ' · '.join(str(getattr(record, k) or '') for k in sezione.indice_label)


def opzioni_relazione(
    campo: Campo, *, solo_articoli_senza_ricetta: bool = False,
) -> list[tuple[Any, str]]:
    """Opzioni per una FK; su richiesta esclude gli articoli ERP già collegati.

    La query filtrata serve soltanto per creare nuove associazioni. La lista
    completa rimane disponibile per mostrare le etichette dei legami esistenti.
    """
    target = campo.modello_riferito
    pk = inspect(target).primary_key[0]
    label_col = getattr(target, campo.attributo_label)
    stmt = select(target)
    if solo_articoli_senza_ricetta:
        if target is not AnagraficaArticoli:
            raise ValueError('Il filtro delle associazioni è riservato agli articoli ERP')
        articoli_associati = select(TipRicetteAnagArticoli.CodArt)
        stmt = stmt.where(~AnagraficaArticoli.CodArt.in_(articoli_associati))

    righe = db.session.scalars(stmt.order_by(label_col.asc())).all()
    opzioni = []
    for r in righe:
        label = str(getattr(r, campo.attributo_label))
        if campo.attributo_sub:
            label += ' — ' + str(getattr(r, campo.attributo_sub))
        id_ = getattr(r, pk.key)
        opzioni.append((id_, label))
    return opzioni


def converte_campo(campo: Campo, valore: str | None):
    testo = (valore or '').strip()
    if not testo:
        if campo.obbligatorio:
            raise ErroreImpostazioni(f'Il campo «{campo.etichetta}» è obbligatorio.')
        return None
    if len(testo) > campo.lunghezza and campo.tipo == 'text':
        raise ErroreImpostazioni(f'«{campo.etichetta}» supera {campo.lunghezza} caratteri.')
    if campo.tipo == 'select':
        if testo not in {v for v, _ in campo.scelte}:
            raise ErroreImpostazioni(f'Valore non valido per «{campo.etichetta}».')
        return int(testo) if testo in ('0', '1') else testo
    if campo.tipo in ('integer', 'fk'):
        if campo.tipo == 'fk' and campo.modello_riferito is AnagraficaArticoli:
            return testo
        try:
            num = int(testo)
        except ValueError as exc:
            raise ErroreImpostazioni(f'«{campo.etichetta}» deve essere un numero intero.') from exc
        if str(num) != testo and not (testo.startswith('+') and str(num) == testo[1:]):
            raise ErroreImpostazioni(f'«{campo.etichetta}» deve essere un intero.')
        if campo.minimo is not None and num < campo.minimo:
            raise ErroreImpostazioni(f'«{campo.etichetta}» deve essere almeno {campo.minimo}.')
        if campo.massimo is not None and num > campo.massimo:
            raise ErroreImpostazioni(f'«{campo.etichetta}» deve essere al massimo {campo.massimo}.')
        return num
    if campo.tipo == 'decimal':
        try:
            value = Decimal(testo.replace(',', '.'))
        except InvalidOperation as exc:
            raise ErroreImpostazioni(f'«{campo.etichetta}» deve essere numerico.') from exc
        if not value.is_finite() or value < 0 or value > Decimal('1000000'):
            raise ErroreImpostazioni(f'«{campo.etichetta}»: valore non valido.')
        # SQLite: l'attuale modello definisce Valore come INTEGER, ma l'app
        # usa coefficienti moltiplicativi. Per la precisione permanente conviene
        # migrare Valore a Numeric; questo aggiornamento conserva i decimali su SQLite.
        return float(value)
    return testo


def valida_riferimenti(sezione: Sezione, valori: dict[str, Any]):
    for campo in sezione.campi:
        if campo.tipo != 'fk':
            continue
        key = next(iter(inspect(campo.modello_riferito).primary_key)).key
        if db.session.get(campo.modello_riferito, valori[campo.nome]) is None:
            raise ErroreImpostazioni(f'Elemento non trovato per «{campo.etichetta}».')


def valida_business(sezione: Sezione, valori: dict[str, Any], record_corrente=None):
    if sezione.slug == 'articoli-ricette':
        stmt = select(TipRicetteAnagArticoli).where(
            TipRicetteAnagArticoli.CodArt == valori['CodArt']
        )
        esistenti = db.session.scalars(stmt).all()
        diversi = [r for r in esistenti if record_corrente is None or r is not record_corrente]
        if diversi:
            raise ErroreImpostazioni('Questo articolo è già collegato a una ricetta. Modifica o rimuovi il collegamento esistente.')
    if sezione.slug == 'coefficienti':
        stmt = select(Coefficienti).where(Coefficienti.Coefficiente == valori['Coefficiente'])
        righe = db.session.scalars(stmt).all()
        if any(r is not record_corrente for r in righe):
            raise ErroreImpostazioni('Nome coefficiente già presente: le formule richiedono una sola definizione.')
    if sezione.slug == 'fasi':
        stmt = select(RicettaFasi).where(RicettaFasi.IdRicetta == valori['IdRicetta'])
        fasi = db.session.scalars(stmt).all()
        lista = [(f.IdFase, f.OffsetBlocchi) for f in fasi if f is not record_corrente]
        lista.append((valori['IdFase'], valori['OffsetBlocchi']))
        lista.sort()
        if lista[0][1] != 0:
            raise ErroreImpostazioni('La prima fase della ricetta deve avere offset 0.')
        if any(lista[i][1] <= lista[i-1][1] for i in range(1, len(lista))):
            raise ErroreImpostazioni('Gli offset devono essere strettamente crescenti nell’ordine delle fasi.')
    if sezione.slug == 'lavorazioni' and valori['DurataBlocchi'] < 1:
        raise ErroreImpostazioni('La durata deve essere positiva.')


def estrai_valori(sezione: Sezione, form, record=None) -> dict[str, Any]:
    pk = set(chiavi_pk(sezione))
    values = {}
    for campo in sezione.campi:
        if record is not None and campo.nome in pk:
            values[campo.nome] = getattr(record, campo.nome)
        else:
            values[campo.nome] = converte_campo(campo, form.get(campo.nome))
    valida_riferimenti(sezione, values)
    valida_business(sezione, values, record)
    return values


def record_ha_dipendenze(sezione: Sezione, record: Any) -> bool:
    """Controllo conservativo di tutte le FK dichiarate nel modello corrente.
    Copre anche ForeignKeyConstraint con chiave composita.
    """
    tabella_padre = sezione.modello.__table__
    for figlia in db.Model.metadata.tables.values():
        if figlia is tabella_padre:
            continue
        for vincolo in figlia.foreign_key_constraints:
            if vincolo.referred_table is not tabella_padre:
                continue
            filtri = [elt.parent == getattr(record, elt.column.key) for elt in vincolo.elements]
            if db.session.execute(select(1).select_from(figlia).where(and_(*filtri)).limit(1)).first():
                return True
    return False


def conta(sezione: Sezione) -> int:
    return db.session.scalar(select(func.count()).select_from(sezione.modello)) or 0
