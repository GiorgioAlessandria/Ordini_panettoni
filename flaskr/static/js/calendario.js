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
document.body.addEventListener("htmx:afterSwap", function (event) {

    if (event.detail.target.id !== "htmx-global") {
        return
    }

    const modalEl = document.getElementById("ordineModal")

    if (!modalEl) {
        return
    }

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl)

    modal.show()
})
calendario.render()