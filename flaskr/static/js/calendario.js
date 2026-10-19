// FullCalendar 7, caricato tramite il bundle globale della base.j2.
// Le URL provengono dal template Jinja, non vengono costruite a mano.
const calendarEl = document.getElementById("calendar");

if (calendarEl) {
    const scadenzeUrl = calendarEl.dataset.eventsScadenzeUrl;
    const pianificazioneUrl = calendarEl.dataset.eventsPianificazioneUrl;

    if (!scadenzeUrl || !pianificazioneUrl) {
        console.error("Calendario: mancano gli URL data-events-* nel template calendario.j2");
    } else {
        const calendario = new FullCalendar.Calendar(calendarEl, {
            // Una colonna per giorno, schede impilate anche con lo stesso orario.
            initialView: "dayGridWeek",
            locale: "it",
            firstDay: 1,
            timeZone: "local",
            nowIndicator: true,
            height: "auto",
            expandRows: true,
            eventDisplay: "block",
            eventOrder: "start,title",

            // Non collassare eventi dietro al link "+ altri".
            dayMaxEvents: false,
            dayMaxEventRows: false,

            // Nella vista oraria gli eventi simultanei sono affiancati,
            // mai sovrapposti fra loro.
            slotEventOverlap: false,
            slotMinTime: "06:00:00",
            slotMaxTime: "17:30:00",
            slotDuration: "00:30:00",
            slotLabelInterval: "01:00:00",
            allDaySlot: true,

            displayEventTime: true,
            displayEventEnd: false,
            eventTimeFormat: {
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
            },

            headerToolbar: {
                start: "prev,next today",
                center: "title",
                end: "dayGridWeek,timeGridWeek,dayGridMonth,timeGridDay",
            },
            buttonText: {
                today: "Oggi",
                dayGridWeek: "Settimana",
                timeGridWeek: "Ore",
                dayGridMonth: "Mese",
                timeGridDay: "Giorno",
            },
            views: {
                dayGridWeek: {
                    dayHeaderFormat: { weekday: "long", day: "numeric", month: "short" },
                },
                timeGridWeek: {
                    dayHeaderFormat: { weekday: "long", day: "numeric", month: "short" },
                },
                dayGridMonth: {
                    dayHeaderFormat: { weekday: "short" },
                },
            },

            eventSources: [
                { id: "scadenze", url: scadenzeUrl },
                { id: "pianificazione", url: pianificazioneUrl },
            ],

            // Distinzione visuale dei due tipi di evento.
            eventClass(info) {
                return info.event.source && info.event.source.id === "scadenze"
                    ? "evento-scadenza"
                    : "evento-produzione";
            },

            // Testo su più righe: evita che i titoli lunghi siano illeggibili.
            // Utilizziamo textContent per non interpretare HTML proveniente dal DB.
            eventContent(info) {
                const contenuto = document.createElement("div");
                contenuto.className = "calendario-evento-contenuto";

                if (info.timeText) {
                    const ora = document.createElement("span");
                    ora.className = "calendario-evento-ora";
                    ora.textContent = info.timeText;
                    contenuto.appendChild(ora);
                }

                const titolo = document.createElement("span");
                titolo.className = "calendario-evento-titolo";
                titolo.textContent = info.event.title || "Evento senza titolo";
                contenuto.appendChild(titolo);

                return { domNodes: [contenuto] };
            },

            // Tooltip del browser con il titolo completo, utile anche in vista ore.
            eventDidMount(info) {
                const evento = info.event;
                const inizio = evento.start ? evento.start.toLocaleString("it-IT") : "";
                const fine = evento.end ? evento.end.toLocaleString("it-IT") : "";
                info.el.title = [evento.title, inizio, fine ? `Fine: ${fine}` : ""]
                    .filter(Boolean)
                    .join("\n");
            },

            eventClick(info) {
                const detailUrl = info.event.extendedProps.detailUrl;
                if (detailUrl && window.htmx) {
                    htmx.ajax("GET", detailUrl, "#htmx-global");
                }
            },

            eventSourceFailure(error) {
                console.error("Errore nel recupero degli eventi calendario:", error);
            },
        });

        calendario.render();
    }
}
