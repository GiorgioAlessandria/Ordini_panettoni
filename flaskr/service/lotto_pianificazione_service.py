"""Assegnazione univoca di lotti DDMMYYYY invertita + progressivo 01..99.

Non effettua commit: la prenotazione del lotto deve essere nella STESSA
transazione che crea una bozza o pianificazione. Si appoggia a UNIQUE SQLite.
"""
from __future__ import annotations
import re
from datetime import date, datetime
from sqlalchemy import select
from flaskr.extensions import db
from flaskr.models.model_lotto import LottoPianificazione


class ErroreLotto(ValueError):
    pass


def prefisso_lotto(data_impasto: date) -> str:
    if not isinstance(data_impasto, date):
        raise ErroreLotto('Data della prima fase di impasto non valida.')
    return data_impasto.strftime('%d%m%Y')[::-1]


def suffisso_int(valore: str | int | None) -> int | None:
    if valore is None or valore == '':
        return None
    if isinstance(valore, bool):
        raise ErroreLotto('Il progressivo deve essere tra 01 e 99.')
    testo = str(valore)
    if not re.fullmatch(r'[0-9]{2}', testo) or not 1 <= int(testo) <= 99:
        raise ErroreLotto('Le ultime due cifre devono essere comprese tra 01 e 99.')
    return int(testo)


def prima_data_impasto_fasi(fasi) -> date:
    """Riceve SOLO fasi di impasto, come dict, tuple (fase,lavorazione) o ORM."""
    date_fasi = []
    for elemento in fasi:
        if isinstance(elemento, tuple):
            elemento = elemento[0]
        data = elemento.get('DataFase') if isinstance(elemento, dict) else getattr(elemento, 'DataFase', None)
        if isinstance(data, datetime):
            data = data.date()
        if not isinstance(data, date):
            raise ErroreLotto('Manca la data di una fase di impasto.')
        date_fasi.append(data)
    if not date_fasi:
        raise ErroreLotto('Non sono presenti fasi di impasto per il lotto.')
    return min(date_fasi)


def _occupati(data_impasto: date) -> set[int]:
    return set(int(x) for x in db.session.scalars(
        select(LottoPianificazione.Progressivo).where(
            LottoPianificazione.DataImpasto == data_impasto
        )
    ).all())


def proponi_progressivo(data_impasto: date) -> int:
    occupati = _occupati(data_impasto)
    for progressivo in range(1, 100):
        if progressivo not in occupati:
            return progressivo
    raise ErroreLotto(f'Sono stati utilizzati tutti i 99 lotti del {data_impasto:%d/%m/%Y}.')


def proponi_lotto(data_impasto: date) -> str:
    return f'{prefisso_lotto(data_impasto)}{proponi_progressivo(data_impasto):02d}'


def lotto_corrente(tipo: str, id_owner: int) -> LottoPianificazione | None:
    return db.session.scalar(select(LottoPianificazione).where(
        LottoPianificazione.TipoOwner == tipo,
        LottoPianificazione.IdOwner == int(id_owner),
        LottoPianificazione.Corrente.is_(True),
    ))


def assegna_lotto(*, tipo: str, id_owner: int, data_impasto: date,
                   progressivo: str | int | None = None,
                   operatore: str = 'sistema', motivo: str = '') -> LottoPianificazione:
    """Assegna o rigenera nella transazione aperta, senza riutilizzare i vecchi.

    Per concorrenti simultanei il DB UNIQUE garantisce la correttezza; in caso
    di collisione il chiamante fa rollback e chiede di aggiornare la proposta.
    """
    if tipo not in {'TRAD', 'MISTA'} or not isinstance(id_owner, int) or id_owner <= 0:
        raise ErroreLotto('Identificativo pianificazione per il lotto non valido.')
    if not operatore or len(operatore) > 120:
        raise ErroreLotto('Operatore lotto non valido.')
    prefisso = prefisso_lotto(data_impasto)
    richiesto = suffisso_int(progressivo)
    precedente = lotto_corrente(tipo, id_owner)
    if precedente and precedente.DataImpasto == data_impasto:
        if richiesto is None or richiesto == precedente.Progressivo:
            return precedente
        raise ErroreLotto('Il progressivo di un lotto già assegnato non si modifica senza una ripianificazione.')
    if precedente and precedente.DataImpasto != data_impasto and not motivo.strip():
        raise ErroreLotto('Per rigenerare il lotto è necessaria la motivazione della ripianificazione.')
    if richiesto is None:
        richiesto = proponi_progressivo(data_impasto)
    elif richiesto in _occupati(data_impasto):
        raise ErroreLotto(f'Il lotto {prefisso}{richiesto:02d} è già utilizzato o riservato; scegli un altro progressivo.')
    ora = datetime.now()
    if precedente:
        precedente.Corrente = False
        precedente.SostituitoIl = ora
        precedente.MotivoCambio = motivo.strip()[:500]
        db.session.flush()  # libera l'unicita' della coppia proprietario/corrente
    nuovo = LottoPianificazione(
        Lotto=f'{prefisso}{richiesto:02d}', DataImpasto=data_impasto,
        Progressivo=richiesto, TipoOwner=tipo, IdOwner=id_owner,
        Corrente=True, AssegnatoIl=ora, Operatore=operatore,
    )
    db.session.add(nuovo)
    db.session.flush()  # applica immediatamente gli UNIQUE prima del commit del piano
    return nuovo


def riserva_e_assegna_lotto_bozza(*, id_bozza: int, fasi_impasto,
                                  progressivo: str | None, operatore: str) -> str:
    record = assegna_lotto(tipo='MISTA', id_owner=id_bozza,
                           data_impasto=prima_data_impasto_fasi(fasi_impasto),
                           progressivo=progressivo, operatore=operatore)
    return record.Lotto
