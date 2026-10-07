function aggiornaSelezioneOrdini() {

    const checkbox = document.querySelectorAll(
        'input[name="ordini_selezionati"]'
    )

    const selezionati = document.querySelectorAll(
        'input[name="ordini_selezionati"]:checked'
    )

    // Nessun ordine selezionato:
    // riabilita tutto
    if (selezionati.length === 0) {
        checkbox.forEach(function (elemento) {
            elemento.disabled = false
        })

        return
    }

    // Il primo selezionato diventa il riferimento
    const riferimento = selezionati[0]

    const codArtRiferimento = riferimento.dataset.codArt
    const prezzaturaRiferimento = riferimento.dataset.prezzatura

    checkbox.forEach(function (elemento) {

        const stessoCodArt =
            elemento.dataset.codArt === codArtRiferimento

        const stessaPrezzatura =
            elemento.dataset.prezzatura === prezzaturaRiferimento

        const compatibile =
            stessoCodArt && stessaPrezzatura

        elemento.disabled = !compatibile
    })
}


document.body.addEventListener("change", function (event) {

    if (!event.target.matches(
        'input[name="ordini_selezionati"]'
    )) {
        return
    }

    aggiornaSelezioneOrdini()
})