// Filtri, ordinamento e conservazione della selezione durante gli swap HTMX.
// Il server resta responsabile della validazione finale delle quantità.
(() => {
    const IDS = new Set([
        "ordini_cliente_da_produrre",
        "ordini_cliente_in_produzione",
    ]);
    const stati = new Map();
    const collator = new Intl.Collator("it", { numeric: true, sensitivity: "base" });

    function contenitoreDi(elemento) {
        const contenitore = elemento?.closest?.(
            "#ordini_cliente_da_produrre, #ordini_cliente_in_produzione"
        );
        return contenitore && IDS.has(contenitore.id) ? contenitore : null;
    }

    function statoDi(contenitore) {
        if (!stati.has(contenitore.id)) {
            stati.set(contenitore.id, {
                filtri: {},
                ordinamento: null,
                selezionati: new Set(),
                scrollX: 0,
                scrollY: 0,
                focus: null,
            });
        }
        return stati.get(contenitore.id);
    }

    function righe(contenitore) {
        return Array.from(contenitore.querySelectorAll("tbody tr[data-record]"));
    }

    function leggiValoreOrdinamento(riga, chiave) {
        const cella = riga.querySelector(`[data-col="${chiave}"]`);
        return cella ? (cella.dataset.sortValue ?? cella.textContent.trim()) : "";
    }

    function applicaOrdinamento(contenitore) {
        const stato = statoDi(contenitore);
        const intestazioni = contenitore.querySelectorAll("th[data-sort-column]");
        intestazioni.forEach((th) => {
            const pulsante = th.querySelector("[data-sort-key]");
            const attivo = stato.ordinamento?.chiave === th.dataset.sortColumn;
            const direzione = attivo ? stato.ordinamento.direzione : null;
            th.setAttribute("aria-sort", direzione === "asc" ? "ascending" :
                direzione === "desc" ? "descending" : "none");
            pulsante?.setAttribute("aria-pressed", attivo ? "true" : "false");
            const indicatore = pulsante?.querySelector(".ordini-sort-indicator");
            if (indicatore) indicatore.textContent = direzione === "asc" ? "▲" :
                direzione === "desc" ? "▼" : "↕";
        });

        if (!stato.ordinamento) return; // Ordine iniziale del backend: data consegna.
        const { chiave, direzione, tipo } = stato.ordinamento;
        const tbody = contenitore.querySelector(".ordini-table tbody");
        if (!tbody) return;
        const ordinate = righe(contenitore).sort((a, b) => {
            const aVal = leggiValoreOrdinamento(a, chiave);
            const bVal = leggiValoreOrdinamento(b, chiave);
            let confronto;
            if (tipo === "number") {
                confronto = Number(aVal) - Number(bVal);
            } else if (tipo === "date") {
                confronto = aVal.localeCompare(bVal); // YYYY-MM-DD
            } else {
                confronto = collator.compare(aVal, bVal);
            }
            if (confronto === 0) {
                confronto = Number(a.dataset.originalIndex) - Number(b.dataset.originalIndex);
            }
            return direzione === "asc" ? confronto : -confronto;
        });
        ordinate.forEach((riga) => tbody.appendChild(riga));
    }

    function applicaFiltri(contenitore) {
        const stato = statoDi(contenitore);
        const filtri = stato.filtri;
        const ricerca = (filtri.ricerca || "").trim().toLocaleLowerCase("it");
        const tutti = righe(contenitore);
        // Verifica i filtri sull'intero dataset prima di mostrare le righe.
        const corrispondenti = tutti.filter((riga) => (
            (!ricerca || (riga.dataset.filterText || "").toLocaleLowerCase("it").includes(ricerca)) &&
            (!filtri.prezzatura || riga.dataset.filterPrezzatura === filtri.prezzatura) &&
            (!filtri.glassa || riga.dataset.filterGlassa === filtri.glassa) &&
            (!filtri.pianificazione || riga.dataset.filterPianificazione === filtri.pianificazione) &&
            (!filtri.priorita || riga.dataset.filterPriorita === filtri.priorita) &&
            (!filtri.parziale || riga.dataset.filterParziale === filtri.parziale) &&
            (!filtri.dal || riga.dataset.filterConsegna >= filtri.dal) &&
            (!filtri.al || riga.dataset.filterConsegna <= filtri.al)
        ));
        // Nessuna paginazione: ogni riga che soddisfa i filtri resta nel DOM
        // ed e' raggiungibile scorrendo la tabella in verticale.
        const visibili = new Set(corrispondenti);
        for (const riga of tutti) {
            const visibile = visibili.has(riga);
            riga.hidden = !visibile;
            riga.style.display = visibile ? "" : "none";
        }
        const contatore = contenitore.querySelector("[data-contatore-righe]");
        if (contatore) {
            const scelti = contenitore.querySelectorAll('input[name="ordini_selezionati"]:checked').length;
            contatore.textContent = `${corrispondenti.length} di ${tutti.length} righe` +
                (contenitore.id === "ordini_cliente_da_produrre" ? ` · ${scelti} selezionate` : "");
        }
        const tbody = contenitore.querySelector(".ordini-table tbody");
        if (!tbody) return;
        tbody.querySelector("tr[data-filtri-vuoti]")?.remove();
        if (tutti.length > 0 && corrispondenti.length === 0) {
            const avviso = document.createElement("tr");
            avviso.dataset.filtriVuoti = "";
            const td = document.createElement("td");
            td.colSpan = contenitore.id === "ordini_cliente_da_produrre" ? 11 : 11;
            td.className = "text-center py-3";
            td.textContent = "Nessun ordine corrisponde ai filtri impostati.";
            avviso.appendChild(td);
            tbody.appendChild(avviso);
        }
    }

    function aggiornaCompatibilita(contenitore) {
        if (contenitore.id !== "ordini_cliente_da_produrre") return;
        const checkbox = Array.from(contenitore.querySelectorAll('input[name="ordini_selezionati"]'));
        const riferimento = checkbox.find((input) => input.checked);
        checkbox.forEach((input) => {
            input.disabled = Boolean(riferimento) && (
                input.dataset.codArt !== riferimento.dataset.codArt ||
                input.dataset.prezzatura !== riferimento.dataset.prezzatura
            );
        });
    }

    function acquisisciStato(contenitore) {
        const stato = statoDi(contenitore);
        contenitore.querySelectorAll("[data-filter]").forEach((input) => {
            stato.filtri[input.dataset.filter] = input.value;
        });
        if (contenitore.id === "ordini_cliente_da_produrre") {
            stato.selezionati = new Set(Array.from(
                contenitore.querySelectorAll('input[name="ordini_selezionati"]:checked'),
                (input) => input.value
            ));
        }
        const area = contenitore.querySelector(".ordini-table-scroll");
        if (area) {
            stato.scrollX = area.scrollLeft;
            stato.scrollY = area.scrollTop;
        }
        const attivo = document.activeElement;
        stato.focus = attivo && contenitore.contains(attivo) && attivo.dataset.filter ? {
            filtro: attivo.dataset.filter,
            start: (typeof attivo.selectionStart === "number" ? attivo.selectionStart : null),
        } : null;
    }

    function ripristinaStato(contenitore) {
        const stato = statoDi(contenitore);
        contenitore.querySelectorAll("[data-filter]").forEach((input) => {
            const salvato = stato.filtri[input.dataset.filter] || "";
            // Se una voce di select non esiste più, torniamo a 'Tutte'.
            input.value = salvato;
            if (input.tagName === "SELECT" && input.selectedIndex === -1) {
                input.value = "";
                stato.filtri[input.dataset.filter] = "";
            }
        });
        if (contenitore.id === "ordini_cliente_da_produrre") {
            const checkbox = Array.from(contenitore.querySelectorAll('input[name="ordini_selezionati"]'));
            const presenti = new Set(checkbox.map((input) => input.value));
            stato.selezionati = new Set([...stato.selezionati].filter((v) => presenti.has(v)));
            checkbox.forEach((input) => {
                input.checked = stato.selezionati.has(input.value);
            });
            aggiornaCompatibilita(contenitore);
        }
        applicaOrdinamento(contenitore);
        applicaFiltri(contenitore);
        const area = contenitore.querySelector(".ordini-table-scroll");
        if (area) {
            area.scrollLeft = stato.scrollX;
            area.scrollTop = stato.scrollY;
        }
        if (stato.focus) {
            const input = contenitore.querySelector(`[data-filter="${stato.focus.filtro}"]`);
            input?.focus({ preventScroll: true });
            if (input && stato.focus.start !== null && typeof input.setSelectionRange === "function") {
                input.setSelectionRange(stato.focus.start, stato.focus.start);
            }
        }
    }

    document.body.addEventListener("htmx:beforeSwap", (event) => {
        const target = event.detail.target;
        if (target && IDS.has(target.id)) acquisisciStato(target);
    });

    document.body.addEventListener("htmx:afterSwap", (event) => {
        const target = event.detail.target;
        if (target && IDS.has(target.id)) {
            ripristinaStato(target);
            // Ricalcola il riepilogo dopo l'aggiornamento dei dati, usando
            // le checkbox effettivamente presenti (non quelle già rimosse dal sync).
            if (target.id === "ordini_cliente_da_produrre") {
                const selezionato = target.querySelector('input[name="ordini_selezionati"]:checked');
                if (selezionato && window.htmx) {
                    htmx.trigger(selezionato, "change");
                } else {
                    const riepilogo = document.getElementById("riepilogo-ordini");
                    if (riepilogo) riepilogo.textContent = "Nessun ordine selezionato";
                }
            }
        }
    });

    document.body.addEventListener("input", (event) => {
        const elemento = event.target;
        if (!elemento?.matches?.("[data-filter]")) return;
        const contenitore = contenitoreDi(elemento);
        if (!contenitore) return;
        statoDi(contenitore).filtri[elemento.dataset.filter] = elemento.value;
        applicaFiltri(contenitore);
    });

    document.body.addEventListener("change", (event) => {
        const elemento = event.target;
        const contenitore = contenitoreDi(elemento);
        if (!contenitore) return;
        if (elemento.matches('[name="ordini_selezionati"]')) {
            const stato = statoDi(contenitore);
            if (elemento.checked) stato.selezionati.add(elemento.value);
            else stato.selezionati.delete(elemento.value);
            aggiornaCompatibilita(contenitore);
            applicaFiltri(contenitore);
        } else if (elemento.matches("[data-priorita-input]")) {
            // Feedback visivo locale; l'evento HX-Trigger ricarica la tabella
            // dopo che la modifica è stata effettivamente salvata sul server.
            elemento.dataset.priorityLevel = elemento.value;
        } else if (elemento.matches("[data-filter]")) {
            statoDi(contenitore).filtri[elemento.dataset.filter] = elemento.value;
            applicaFiltri(contenitore);
        }
    });

    document.body.addEventListener("click", (event) => {
        const sort = event.target.closest?.("[data-sort-key]");
        if (sort) {
            const contenitore = contenitoreDi(sort);
            if (!contenitore) return;
            const stato = statoDi(contenitore);
            const chiave = sort.dataset.sortKey;
            const precedente = stato.ordinamento;
            stato.ordinamento = {
                chiave,
                tipo: sort.dataset.sortType || "text",
                direzione: precedente?.chiave === chiave && precedente.direzione === "asc" ? "desc" : "asc",
            };
            applicaOrdinamento(contenitore);
            applicaFiltri(contenitore);
            return;
        }
        const reset = event.target.closest?.("[data-reset-filters]");
        if (reset) {
            const contenitore = contenitoreDi(reset);
            if (!contenitore) return;
            const stato = statoDi(contenitore);
            stato.filtri = {};
            contenitore.querySelectorAll("[data-filter]").forEach((input) => { input.value = ""; });
            applicaFiltri(contenitore);
        }
    });

    document.body.addEventListener("htmx:responseError", (event) => {
        const controllo = event.detail.elt;
        if (!controllo?.matches?.("[data-priorita-input]")) return;
        controllo.value = controllo.dataset.prevValue || "";
        controllo.dataset.priorityLevel = controllo.value;
        alert("Impossibile aggiornare la priorità. Verifica il server Flask.");
    });

    let resizePending = false;
    function aggiornaDimensione() {
        if (resizePending) return;
        resizePending = true;
        requestAnimationFrame(() => {
            resizePending = false;
            IDS.forEach((id) => {
                const contenitore = document.getElementById(id);
                if (contenitore?.querySelector(".ordini-form")) applicaFiltri(contenitore);
            });
        });
    }
    window.addEventListener("resize", aggiornaDimensione);
    window.addEventListener("ordini:layout-change", aggiornaDimensione);

    // Il backend invia HX-Trigger: pianificazioneSalvata solo dopo commit.
    document.body.addEventListener("pianificazioneSalvata", () => {
        const contenitore = document.getElementById("ordini_cliente_da_produrre");
        if (contenitore) {
            statoDi(contenitore).selezionati.clear();
            contenitore.querySelectorAll('input[name="ordini_selezionati"]:checked')
                .forEach((input) => { input.checked = false; });
            aggiornaCompatibilita(contenitore);
            applicaFiltri(contenitore);
        }
        const riepilogo = document.getElementById("riepilogo-ordini");
        if (riepilogo) riepilogo.textContent = "Nessun ordine selezionato";
        const modal = document.getElementById("modal-pianificazione");
        const instance = window.bootstrap?.Modal?.getInstance(modal);
        instance?.hide();
    });
})();
