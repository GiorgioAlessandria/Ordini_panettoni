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