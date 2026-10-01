const calendarEl = document.getElementById("calendar")
const eventsUrl = calendarEl.dataset.eventsUrl
const calendario = new FullCalendar.Calendar(calendarEl, {
    initialView: 'dayGridMonth',

    locale: "it",
    firstDay: 1,

    events: eventsUrl,
    headerToolbar: {
        start: "prev,next,today",
        center: "title",
        end: ""
    },
    eventClick: function (info) {
        const detailUrl = info.event.extendedProps.detailUrl
        htmx.ajax(
            "GET",
            info.event.extendedProps.detailUrl,
            "#htmx-global"
        )
    },
});
calendario.render()