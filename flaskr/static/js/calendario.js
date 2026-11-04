// FullCalendar 7: raggruppamento verificato delle fasi su colonne e righe per blocco.
(function () {
    'use strict';
    const calendarEl = document.getElementById('calendar');
    if (!calendarEl || calendarEl.dataset.calendarInitialized === '1') return;
    if (!window.FullCalendar?.Calendar) {
        console.error('FullCalendar non caricato: verificare fullcalendar.global.js in base.j2');
        return;
    }

    const scadenzeUrl = calendarEl.dataset.eventsScadenzeUrl;
    const pianificazioneUrl = calendarEl.dataset.eventsPianificazioneUrl;
    if (!scadenzeUrl || !pianificazioneUrl) {
        console.error('Mancano data-events-scadenze-url e/o data-events-pianificazione-url');
        return;
    }

    // Colore associato in modo stabile all'IdPianificazione.
    // L'angolo aureo evita il riutilizzo periodico della palette di 8 colori.
    // Il colore del piano identifica bordo e sfondo della scheda; quello della
    // singola lavorazione rimane distinto nell'etichetta della fase.
    function infoPiano(id) {
        const idNumerico = Number(id);
        const hue = Number.isSafeInteger(idNumerico) && idNumerico > 0
            ? (idNumerico * 137.50776405) % 360
            : 215;
        const tonalita = hue.toFixed(3);
        return [
            `hsl(${tonalita} 76% 34%)`, // bordo saturo e leggibile
            `hsl(${tonalita} 83% 77%)`, // colore del riquadro nettamente visibile
        ];
    }

    function tipoLavorazione(nome) {
        const testo = String(nome || '').toLocaleLowerCase('it');
        if (testo.includes('impast')) return 'impasto';
        if (testo.includes('cott') || testo.includes('forno')) return 'cottura';
        if (testo.includes('lievit')) return 'lievitazione';
        return 'altro';
    }

    // Filtro solamente visivo: le pianificazioni restano nel database.
    // IdLavorazione=2 e' il forno in questa applicazione; il nome gestisce
    // anche eventuali lavorazioni di cottura con un identificativo diverso.
    function faseDiCottura(evento) {
        const props = evento?.extendedProps || {};
        return Number(props.id_lavorazione) === 2 ||
            tipoLavorazione(props.lavorazione) === 'cottura';
    }

    function fasiFiltrate(eventi) {
        if (!Array.isArray(eventi)) return [];
        return nascondiCotturaCheckbox?.checked
            ? eventi.filter(evento => !faseDiCottura(evento))
            : eventi;
    }

    function addText(parent, className, value) {
        const nodo = document.createElement('div');
        nodo.className = className;
        nodo.textContent = String(value);
        parent.appendChild(nodo);
        return nodo;
    }

    // Nella vista oraria, gli eventi che occupano lo stesso identico intervallo
    // diventano un unico contenitore, visualizzato come griglia di schede.
    // L'endpoint Flask e gli eventi delle altre viste non vengono modificati.
    function raggruppaFasiPerBlocco(eventi) {
        if (!Array.isArray(eventi)) return [];
        const gruppi = new Map();
        const altri = [];
        for (const evento of eventi) {
            if (evento.allDay || typeof evento.start !== 'string' ||
                    typeof evento.end !== 'string') {
                altri.push(evento);
                continue;
            }
            const chiave = `${evento.start}|${evento.end}`;
            if (!gruppi.has(chiave)) gruppi.set(chiave, []);
            gruppi.get(chiave).push(evento);
        }
        for (const [chiave, fasi] of gruppi) {
            if (fasi.length === 1) {
                altri.push(fasi[0]);
                continue;
            }
            // Ordine deterministico: le schede restano sempre nella stessa posizione.
            fasi.sort((a, b) => {
                const aa = a.extendedProps || {};
                const bb = b.extendedProps || {};
                return Number(aa.id_pianificazione || 0) - Number(bb.id_pianificazione || 0) ||
                    Number(aa.numero_fase || 0) - Number(bb.numero_fase || 0) ||
                    String(a.id).localeCompare(String(b.id));
            });
            const prima = fasi[0];
            altri.push({
                id: `gruppo-${chiave}`,
                title: `${fasi.length} fasi pianificate`,
                start: prima.start,
                end: prima.end,
                allDay: false,
                extendedProps: { tipo_evento: 'gruppo_fasi', fasi },
            });
        }
        return altri;
    }

    function testoForniDichiarati(props) {
        const forni = props?.forni_dettaglio;
        if (!Array.isArray(forni) || !forni.length) return '';
        return forni.map(f => `${f.nome} · ${f.numero_cotte} cotte`).join(' | ');
    }

    function dettagliFase(evento) {
        const p = evento.extendedProps || {};
        const righe = [
            `Pianificazione: ${p.etichetta_piano || p.id_pianificazione || '?'}`,
            `Fase: ${p.numero_fase ?? '?'} · ${p.lavorazione || 'Lavorazione'}`,
            `Articolo: ${p.descrizione_articolo || evento.title || 'N/D'}`,
            `Ordini: ${(p.ordini || []).join(', ')}`,
            `Quantità assegnata: ${p.quantita_assegnata ?? 'N/D'}`,
        ];
        if (testoForniDichiarati(p)) righe.push(`Forni: ${testoForniDichiarati(p)}`);
        if (p.impastatrici?.length) righe.push(`Impastatrici: ${p.impastatrici.join(', ')}`);
        return righe.join('\n');
    }

    function creaSchedaFase(evento) {
        const p = evento.extendedProps || {};
        const nomeFase = p.numero_fase == null
            ? (p.lavorazione || 'Lavorazione')
            : `Fase ${p.numero_fase} · ${p.lavorazione || 'Lavorazione'}`;
        const scheda = document.createElement('button');
        scheda.type = 'button';
        scheda.className = 'cal-blocco-scheda';
        const [bordo, sfondo] = infoPiano(p.id_pianificazione);
        scheda.style.setProperty('--piano-accent', bordo);
        scheda.style.setProperty('--piano-bg', sfondo);
        // La scheda raggruppata e' un <button>, non un evento FullCalendar:
        // colora direttamente l'intera superficie come i vecchi riquadri.
        // L'inline !important evita che reset Bootstrap/temi la rendano bianca.
        scheda.style.setProperty('background', sfondo, 'important');
        scheda.style.setProperty('background-color', sfondo, 'important');
        scheda.style.setProperty('border', `2px solid ${bordo}`, 'important');
        scheda.style.setProperty('border-left', `5px solid ${bordo}`, 'important');
        scheda.title = dettagliFase(evento);
        scheda.setAttribute('aria-label', dettagliFase(evento));
        const etichetta = addText(scheda, 'cal-event-badge',
            `Piano ${p.etichetta_piano || '#' + (p.id_pianificazione ?? '?')} · ${nomeFase}`);
        etichetta.dataset.lavorazione = tipoLavorazione(p.lavorazione);
        if (typeof p.colore_fase === 'string' && /^#[0-9a-f]{6}$/i.test(p.colore_fase)) {
            etichetta.dataset.coloreFase = 'personalizzato';
            etichetta.style.setProperty('--fase-badge-bg', p.colore_fase);
            etichetta.style.setProperty('--fase-badge-fg', '#000000');
        }
        addText(scheda, 'cal-event-title', p.descrizione_articolo || evento.title || 'Articolo');
        addText(scheda, 'cal-event-summary',
            `${p.numero_ordini ?? 0} ordini · ${p.quantita_assegnata ?? 'N/D'} pz`);
        if (testoForniDichiarati(p)) addText(scheda, 'cal-event-ovens', testoForniDichiarati(p));
        if (p.dati_incompleti) addText(scheda, 'cal-event-warning', 'Quantità da verificare');
        scheda.addEventListener('click', (click) => {
            click.preventDefault();
            click.stopPropagation();
            if (p.detailUrl && window.apriRiepilogoCalendario) {
                window.apriRiepilogoCalendario(p.detailUrl);
            }
        });
        return scheda;
    }

    function propsStato(props) {
        return props.stato_giorno === 'eseguito'
            ? 'scadenza-giorno-completata'
            : 'scadenza-giorno-in-corso';
    }

    // I comandi sono nel template, fuori dal DOM ricreato da FullCalendar.
    // Aggiornare titolo e stato dei pulsanti a ogni cambio vista/periodo.
    const titoloIntestazione = document.getElementById('calendar-current-title');
    const pulsantiVista = Array.from(document.querySelectorAll('[data-calendar-view]'));

    function aggiornaIntestazione(vista) {
        if (titoloIntestazione) titoloIntestazione.textContent = vista.title || 'Calendario';
        for (const pulsante of pulsantiVista) {
            const attivo = pulsante.dataset.calendarView === vista.type;
            pulsante.classList.toggle('is-active', attivo);
            pulsante.setAttribute('aria-pressed', String(attivo));
        }
    }


    // Il weekend viene nascosto solo se non contiene fasi produttive.
    // Le scadenze cliente non obbligano a mostrare la colonna: chi consulta il
    // calendario puo' scegliere di visualizzarle tramite il comando dedicato.
    const mostraWeekendCheckbox = document.getElementById('calendar-show-weekends');
    const nascondiCotturaCheckbox = document.getElementById('calendar-hide-cottura');
    const statoWeekend = document.getElementById('calendar-weekend-status');
    const periodiAnalizzati = new Map();
    let weekendConProduzione = new Set();
    let fasiPerGiorno = new Map();
    let intervalloCorrente = null;
    let vistaCorrente = null;
    let ultimaRichiesta = 0;
    let giorniNascosti = [0, 6];

    function dataLocale(data) {
        const anno = data.getFullYear();
        const mese = String(data.getMonth() + 1).padStart(2, '0');
        const giorno = String(data.getDate()).padStart(2, '0');
        return `${anno}-${mese}-${giorno}`;
    }

    function dataEstesa(data, giorni) {
        const copia = new Date(data.getFullYear(), data.getMonth(), data.getDate(), 12);
        copia.setDate(copia.getDate() + giorni);
        return dataLocale(copia);
    }

    function weekendConEventi(eventi) {
        const giorni = new Set();
        for (const evento of eventi) {
            if (!evento || typeof evento.start !== 'string') continue;
            // Leggiamo la data locale della fase, senza conversione in UTC.
            const inizio = evento.start.slice(0, 10);
            if (!/^\d{4}-\d{2}-\d{2}$/.test(inizio)) continue;
            const giorno = new Date(`${inizio}T12:00:00`).getDay();
            if (giorno === 0 || giorno === 6) giorni.add(giorno);
        }
        return giorni;
    }

    // Distribuzione proporzionale della larghezza nella SOLA settimana oraria.
    // Il giorno senza produzione conserva spazio per intestazione e scadenze;
    // i giorni con tante fasi ricevono più spazio per le schede.
    function contaFasiPerGiorno(eventi) {
        const conteggi = new Map();
        for (const fase of eventi) {
            if (typeof fase?.start !== 'string') continue;
            const data = fase.start.slice(0, 10);
            if (!/^\d{4}-\d{2}-\d{2}$/.test(data)) continue;
            conteggi.set(data, (conteggi.get(data) || 0) + 1);
        }
        return conteggi;
    }

    // FullCalendar e' libero di usare un unico <col span="N"> per tutti i giorni.
    // In quel caso impostare width sui <th> non cambia la larghezza effettiva:
    // e' necessario espandere il COLGROUP in N colonne reali prima di pesarlo.
    function applicaPercentualiTabella(tabella, classeCella, date, percentuali) {
        if (!tabella) return false;
        const celle = Array.from(tabella.querySelectorAll(classeCella));
        if (celle.length !== date.length ||
            celle.some((cella, i) => cella.dataset.date !== date[i])) return false;

        const gruppo = Array.from(tabella.children).find(el => el.tagName === 'COLGROUP');
        if (!gruppo) return false;
        let colonne = Array.from(gruppo.children).filter(el => el.tagName === 'COL');
        // Il COLGROUP puo' avere una sola colonna con span=N; altre versioni
        // possono avere un col per giorno. Supportiamo entrambe.
        const espanse = [];
        for (const colonna of colonne) {
            const ripetizioni = Math.max(1, Number(colonna.getAttribute('span')) || 1);
            if (ripetizioni > 32) return false;
            for (let indice = 0; indice < ripetizioni; indice++) {
                const clone = colonna.cloneNode(false);
                clone.removeAttribute('span');
                espanse.push(clone);
            }
        }
        // Normalmente non esiste l'asse orario in queste tre tabelle.
        // Se e' presente, preserviamone la prima colonna e pesiamo i giorni.
        const asse = espanse.length === date.length + 1 ? 1 : 0;
        if (espanse.length - asse !== date.length) return false;
        if (colonne.length !== espanse.length || colonne.some(c => c.hasAttribute('span'))) {
            gruppo.replaceChildren(...espanse);
            colonne = espanse;
        }
        colonne.slice(asse).forEach((colonna, i) => {
            colonna.style.setProperty('width', percentuali[i], 'important');
            colonna.style.setProperty('min-width', '0', 'important');
        });
        // Proprieta' sul col hanno priorita' sul th in una tabella a layout fisso.
        tabella.style.setProperty('table-layout', 'fixed', 'important');
        celle.forEach((cella, i) => {
            cella.style.setProperty('width', percentuali[i], 'important');
            cella.style.setProperty('min-width', '0', 'important');
        });
        return true;
    }

    function applicaLarghezzeGiorni() {
        if (calendario.view?.type !== 'timeGridWeek') return;
        const vista = calendarEl.querySelector('.fc-timeGridWeek-view');
        if (!vista) return;
        const intestazioni = [...vista.querySelectorAll('.fc-col-header-cell[data-date]')];
        if (!intestazioni.length) return;
        const date = intestazioni.map(cell => cell.dataset.date);
        const totaleFasi = date.reduce((somma, data) => somma + (fasiPerGiorno.get(data) || 0), 0);
        if (!totaleFasi) return;
        if (vista.getBoundingClientRect().width < 900) return;

        // I giorni vuoti hanno una larghezza minima, i giorni affollati ricevono
        // spazio aggiuntivo senza crescere indefinitamente.
        const pesi = date.map(data => {
            const n = fasiPerGiorno.get(data) || 0;
            return n === 0 ? 0.68 : Math.min(1.25 + 0.35 * Math.sqrt(n), 3.0);
        });
        const totale = pesi.reduce((somma, peso) => somma + peso, 0);
        const percentuali = pesi.map(peso => `${(100 * peso / totale).toFixed(5)}%`);

        // FullCalendar usa 3 tabelle separate per header, all-day e ore.
        // Devono essere modificate nello stesso frame con le stesse proporzioni.
        const tabelle = [
            ...vista.querySelectorAll('table.fc-col-header'),
            ...vista.querySelectorAll('.fc-timegrid-cols > table'),
            ...vista.querySelectorAll('.fc-daygrid-body > table'),
        ];
        for (const tabella of tabelle) {
            let cella = null;
            if (tabella.matches('.fc-col-header')) cella = '.fc-col-header-cell[data-date]';
            else if (tabella.closest('.fc-timegrid-cols')) cella = '.fc-timegrid-col[data-date]';
            else if (tabella.closest('.fc-daygrid-body')) cella = '.fc-daygrid-day[data-date]';
            if (cella) applicaPercentualiTabella(tabella, cella, date, percentuali);
        }
    }

    let ricalcoloPend = false;
    function pianificaLarghezzeGiorni() {
        if (ricalcoloPend) return;
        ricalcoloPend = true;
        requestAnimationFrame(() => requestAnimationFrame(() => {
            ricalcoloPend = false;
            applicaLarghezzeGiorni();
        }));
    }

    // L'altezza delle fasi raggruppate deve restare quella assegnata da
    // FullCalendar per 07–12:30 e 13–18, non crescere con il numero di schede.
    function limitaAltezzaGruppo(elemento) {
        const harness = elemento.closest('.fc-timegrid-event-harness');
        const griglia = elemento.querySelector('.cal-blocco-griglia');
        if (!harness || !griglia) return;
        const altezza = Math.floor(harness.getBoundingClientRect().height);
        if (altezza < 25) return;
        elemento.style.setProperty('height', `${altezza}px`, 'important');
        elemento.style.setProperty('max-height', `${altezza}px`, 'important');
        for (const main of elemento.querySelectorAll('.fc-event-main, .fc-event-main-frame')) {
            main.style.setProperty('height', `${altezza}px`, 'important');
            main.style.setProperty('max-height', `${altezza}px`, 'important');
            main.style.setProperty('min-height', '0', 'important');
            main.style.setProperty('overflow', 'hidden', 'important');
        }
        griglia.style.setProperty('height', `${altezza - 2}px`, 'important');
        griglia.style.setProperty('max-height', `${altezza - 2}px`, 'important');
        griglia.style.setProperty('overflow-y', 'auto', 'important');
        griglia.style.setProperty('overflow-x', 'hidden', 'important');
    }

    function applicaVisibilitaWeekend() {
        // La vista giornaliera non ha colonne da comprimere: mantenere il giorno
        // scelto dall'utente, anche quando e' sabato o domenica.
        const mostraTutti = Boolean(mostraWeekendCheckbox?.checked);
        const nuovi = (vistaCorrente === 'dayGridDay' || mostraTutti)
            ? []
            : [0, 6].filter(giorno => !weekendConProduzione.has(giorno));
        if (nuovi.join(',') === giorniNascosti.join(',')) return;
        giorniNascosti = nuovi;
        calendario.setOption('hiddenDays', nuovi);
        pianificaLarghezzeGiorni();
    }

    function aggiornaWeekendPerVista(view) {
        vistaCorrente = view.type;
        if (vistaCorrente === 'dayGridDay') {
            applicaVisibilitaWeekend();
            return;
        }
        // currentStart/currentEnd sono stabili anche quando hiddenDays cambia.
        // Nelle viste settimanali verifichiamo SOLO i weekend della settimana
        // corrente; altrimenti un piano nel weekend successivo mostrerebbe
        // erroneamente sabato e domenica anche nella settimana vuota.
        // Solo il mese include giorni di raccordo visibili di altri mesi.
        const calendarioMensile = view.type === 'dayGridMonth';
        const inizio = calendarioMensile
            ? dataEstesa(view.currentStart, -7) : dataLocale(view.currentStart);
        const fine = calendarioMensile
            ? dataEstesa(view.currentEnd, 7) : dataLocale(view.currentEnd);
        const chiave = `${inizio}|${fine}`;
        if (chiave === intervalloCorrente) {
            applicaVisibilitaWeekend();
            pianificaLarghezzeGiorni();
            return;
        }
        intervalloCorrente = chiave;
        const richiesta = ++ultimaRichiesta;

        const memorizzati = periodiAnalizzati.get(chiave);
        if (memorizzati) {
            weekendConProduzione = memorizzati.weekend;
            fasiPerGiorno = memorizzati.conteggi;
            if (statoWeekend) statoWeekend.textContent = '';
            applicaVisibilitaWeekend();
            pianificaLarghezzeGiorni();
            return;
        }
        // Durante il caricamento non mostrare giorni che potrebbero essere vuoti.
        // Se la richiesta fallisce, invece, mostriamo entrambi i giorni per
        // evitare di occultare per errore una lavorazione pianificata.
        weekendConProduzione = new Set();
        if (statoWeekend) statoWeekend.textContent = 'Verifica weekend…';
        applicaVisibilitaWeekend();

        const url = new URL(pianificazioneUrl, window.location.href);
        url.searchParams.set('start', inizio);
        url.searchParams.set('end', fine);
        fetch(url.toString(), { credentials: 'same-origin' })
            .then(risposta => {
                if (!risposta.ok) throw new Error(`HTTP ${risposta.status}`);
                return risposta.json();
            })
            .then(eventi => {
                if (!Array.isArray(eventi)) throw new Error('Risposta calendario non valida');
                // Calcolare larghezze e weekend solo sulle fasi effettivamente visibili.
                const visibili = fasiFiltrate(eventi);
                const presenti = weekendConEventi(visibili);
                const conteggi = contaFasiPerGiorno(visibili);
                periodiAnalizzati.set(chiave, { weekend: presenti, conteggi });
                if (richiesta !== ultimaRichiesta || chiave !== intervalloCorrente) return;
                weekendConProduzione = presenti;
                fasiPerGiorno = conteggi;
                if (statoWeekend) statoWeekend.textContent = '';
                applicaVisibilitaWeekend();
                pianificaLarghezzeGiorni();
            })
            .catch(errore => {
                console.error('Impossibile verificare le lavorazioni del weekend', errore);
                if (richiesta !== ultimaRichiesta || chiave !== intervalloCorrente) return;
                weekendConProduzione = new Set([0, 6]);
                fasiPerGiorno = new Map();
                if (statoWeekend) statoWeekend.textContent = 'Weekend visibili: verifica non disponibile';
                applicaVisibilitaWeekend();
                pianificaLarghezzeGiorni();
            });
    }

    mostraWeekendCheckbox?.addEventListener('change', applicaVisibilitaWeekend);
    nascondiCotturaCheckbox?.addEventListener('change', () => {
        // La sorgente viene rifiltrata PRIMA del raggruppamento in righe.
        // Invalidare anche conteggi e weekend, che devono riflettere le sole
        // fasi visibili. Ignorare eventuali risposte asincrone precedenti.
        ++ultimaRichiesta;
        periodiAnalizzati.clear();
        intervalloCorrente = null;
        calendario.refetchEvents();
        aggiornaWeekendPerVista(calendario.view);
    });

    const calendario = new FullCalendar.Calendar(calendarEl, {
        themeSystem: 'classic',
        datesSet(info) {
            aggiornaIntestazione(info.view);
            aggiornaWeekendPerVista(info.view);
            pianificaLarghezzeGiorni();
        },
        initialView: 'timeGridWeek',
        hiddenDays: [0, 6],
        locale: 'it',
        firstDay: 1,
        timeZone: 'local',
        height: 'auto',
        expandRows: true,
        allDaySlot: true,
        lazyFetching: false, // Ricalcola raggruppamento al cambio della vista.
        eventDisplay: 'block',
        displayEventEnd: false,
        displayEventTime: false,
        dayMaxEvents: false,
        dayMaxEventRows: false,
        slotEventOverlap: false,
        slotMinTime: '07:00:00',
        slotMaxTime: '18:00:00',
        slotDuration: '00:30:00',
        slotLabelInterval: '01:00:00',
        nowIndicator: true,
        // Navigazione e scelta viste sono nella barra HTML permanente.
        headerToolbar: false,
        views: {
            timeGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
            dayGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
        },
        eventSources: [
            { id: 'scadenze', url: scadenzeUrl },
            {
                id: 'pianificazione',
                url: pianificazioneUrl,
                // FullCalendar: nella singola event source la callback è `success`,
                // NON `eventSourceSuccess` (che è un'opzione globale).
                // Questo è il punto che impediva il precedente raggruppamento.
                success(eventi) {
                    const visibili = fasiFiltrate(eventi);
                    return calendario.view?.type === 'timeGridWeek'
                        ? raggruppaFasiPerBlocco(visibili)
                        : visibili;
                },
            },
        ],
        eventClassNames(info) {
            const gruppo = info.event.extendedProps.tipo_evento === 'gruppo_fasi';
            if (gruppo) return ['evento-blocco-piani'];
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            return produzione
                ? ['evento-produzione', 'lavorazione-' + tipoLavorazione(info.event.extendedProps.lavorazione)]
                : ['evento-scadenza', propsStato(info.event.extendedProps)];
        },
        eventContent(info) {
            const props = info.event.extendedProps;
            if (props.tipo_evento === 'gruppo_fasi') {
                const griglia = document.createElement('div');
                griglia.className = 'cal-blocco-griglia';
                griglia.setAttribute('aria-label', `${props.fasi?.length ?? 0} fasi nello stesso blocco`);
                for (const fase of props.fasi || []) griglia.appendChild(creaSchedaFase(fase));
                return { domNodes: [griglia] };
            }
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            const scheda = document.createElement('div');
            scheda.className = 'cal-event-content';

            if (produzione) {
                const nomeFase = props.numero_fase == null
                    ? (props.lavorazione || 'Lavorazione')
                    : `Fase ${props.numero_fase} · ${props.lavorazione || 'Lavorazione'}`;
                const etichetta = addText(scheda, 'cal-event-badge',
                    `Piano ${props.etichetta_piano || '#' + (props.id_pianificazione ?? '?')} · ${nomeFase}`);
                etichetta.dataset.lavorazione = tipoLavorazione(props.lavorazione);
                if (typeof props.colore_fase === 'string' && /^#[0-9a-f]{6}$/i.test(props.colore_fase)) {
                    etichetta.dataset.coloreFase = 'personalizzato';
                    etichetta.style.setProperty('--fase-badge-bg', props.colore_fase);
                    etichetta.style.setProperty('--fase-badge-fg', '#000000');
                }
            } else {
                addText(scheda, 'cal-event-badge scadenza-badge', 'Scadenze del giorno');
            }
            addText(scheda, 'cal-event-title',
                produzione ? (props.descrizione_articolo || info.event.title || 'Articolo')
                           : (info.event.title || 'Scadenze cliente'));

            if (produzione) {
                addText(scheda, 'cal-event-summary',
                    `${props.numero_ordini ?? 0} ordini · ${props.quantita_assegnata ?? 'N/D'} pz assegnati`);
                if (testoForniDichiarati(props)) addText(scheda, 'cal-event-ovens', testoForniDichiarati(props));
                if (props.dati_incompleti) addText(scheda, 'cal-event-warning', 'Quantità da verificare');
            } else {
                addText(scheda, 'cal-event-summary',
                    `${props.numero_completati ?? 0} eseguiti · ${props.numero_da_eseguire ?? 0} da eseguire`);
            }
            return { domNodes: [scheda] };
        },
        eventDidMount(info) {
            const p = info.event.extendedProps;
            if (p.tipo_evento === 'gruppo_fasi') {
                // Aspettiamo che il posizionamento assoluto della libreria sia terminato.
                const aggiorna = () => limitaAltezzaGruppo(info.el);
                requestAnimationFrame(aggiorna);
                if (typeof ResizeObserver !== 'undefined') {
                    const harness = info.el.closest('.fc-timegrid-event-harness');
                    if (harness) {
                        const osservatore = new ResizeObserver(aggiorna);
                        osservatore.observe(harness);
                        info.el._bloccoResizeObserver = osservatore;
                    }
                }
                return;
            }
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            if (produzione) {
                const [bordo, sfondo] = infoPiano(p.id_pianificazione);
                // Applica il colore direttamente al riquadro FullCalendar.
                // Le proprieta' inline con !important prevalgono sui temi e
                // sulle regole che impostano background: ... !important.
                info.el.style.setProperty('--piano-accent', bordo);
                info.el.style.setProperty('--piano-bg', sfondo);
                info.el.style.setProperty('background', sfondo, 'important');
                info.el.style.setProperty('background-color', sfondo, 'important');
                info.el.style.setProperty('border', `2px solid ${bordo}`, 'important');
                info.el.style.setProperty('border-left', `6px solid ${bordo}`, 'important');
            }
            const righe = [info.event.title];
            if (produzione) {
                righe.push(
                    `Pianificazione: ${p.etichetta_piano || p.id_pianificazione}`,
                    `Lavorazione: ${p.lavorazione}`,
                    `Ordini: ${(p.ordini || []).join(', ')}`,
                    `Quantità assegnata: ${p.quantita_assegnata ?? 'N/D'}`,
                    `Produzione: ${p.panettoni_previsti ?? 'N/D'}`,
                    `Da stock: ${p.quantita_da_stock ?? 0} pz`,
                    `Cotte: ${p.numero_cotte ?? 'N/D'}`,
                    `Peso: ${p.peso_totale_kg ?? 'N/D'} kg`,
                );
                if (testoForniDichiarati(p)) righe.push(`Forni: ${testoForniDichiarati(p)}`);
                if (p.impastatrici?.length) righe.push(`Impastatrici: ${p.impastatrici.join(', ')}`);
            } else {
                righe.push(
                    `Ordini del giorno: ${p.numero_ordini ?? 0}`,
                    `Eseguiti: ${p.numero_completati ?? 0}`,
                    `Da eseguire: ${p.numero_da_eseguire ?? 0}`,
                    `Quantità ordinata: ${p.quantita_ordinata ?? 0} pz`,
                    'Clicca per vedere la tabella con i commenti'
                );
            }
            info.el.title = righe.join('\n');
        },
        eventWillUnmount(info) {
            if (info.el._bloccoResizeObserver) {
                info.el._bloccoResizeObserver.disconnect();
                delete info.el._bloccoResizeObserver;
            }
        },
        eventsSet() { pianificaLarghezzeGiorni(); },
        eventClick(info) {
            const url = info.event.extendedProps.detailUrl;
            if (url && window.apriRiepilogoCalendario) {
                info.jsEvent.preventDefault();
                window.apriRiepilogoCalendario(url);
            }
        },
        eventSourceFailure(error) { console.error('Errore caricamento eventi calendario', error); },
    });

    // Collegare i comandi una sola volta: FullCalendar non li ricrea.
    document.getElementById('calendar-prev')?.addEventListener('click', () => calendario.prev());
    document.getElementById('calendar-next')?.addEventListener('click', () => calendario.next());
    document.getElementById('calendar-today')?.addEventListener('click', () => calendario.today());

    // Il PDF usa la stessa settimana che l'utente sta visualizzando.
    // Anche dalla vista Mese/Giorno viene esportata la settimana della data attiva.
    document.getElementById('calendar-pdf-export')?.addEventListener('click', () => {
        const endpoint = calendarEl.dataset.exportPdfUrl;
        if (!endpoint) {
            console.error('URL esportazione PDF non configurato.');
            return;
        }
        const url = new URL(endpoint, window.location.href);
        url.searchParams.set('settimana', dataLocale(calendario.getDate()));
        url.searchParams.set('mostra_weekend', mostraWeekendCheckbox?.checked ? '1' : '0');
        url.searchParams.set('nascondi_cottura', nascondiCotturaCheckbox?.checked ? '1' : '0');
        window.location.assign(url.toString());
    });
    for (const pulsante of pulsantiVista) {
        pulsante.addEventListener('click', () => {
            const nomeVista = pulsante.dataset.calendarView;
            if (nomeVista && calendario.view.type !== nomeVista) calendario.changeView(nomeVista);
        });
    }

    // Ricalcola la proporzione se cambia la larghezza del contenitore.
    if (typeof ResizeObserver !== 'undefined') {
        const osservatore = new ResizeObserver(pianificaLarghezzeGiorni);
        osservatore.observe(calendarEl);
    }

    calendarEl.dataset.calendarInitialized = '1';
    calendario.render();
    document.body.addEventListener('pianificazioneSalvata', () => {
        periodiAnalizzati.clear();
        intervalloCorrente = null;
        calendario.refetchEvents();
        aggiornaWeekendPerVista(calendario.view);
    });
})();
