// Toasts: fade out on their own. An Undo toast shows how long is left.
document.querySelectorAll('[data-toast]').forEach((toast) => {
    const seconds = Number(toast.dataset.toastSeconds || 4);
    const bar = toast.querySelector('[data-toast-bar]');
    if (bar) {
        bar.style.transition = `transform ${seconds}s linear`;
        requestAnimationFrame(() => requestAnimationFrame(() => { bar.style.transform = 'scaleX(0)'; }));
    }
    setTimeout(() => toast.remove(), seconds * 1000);
});

// Results review: count ticked listings and enable "Track selected".
const review = document.getElementById('review-form');
if (review) {
    const boxes = [...document.querySelectorAll('[data-track]')];
    const count = document.getElementById('track-count');
    const submit = document.getElementById('track-submit');
    const all = document.getElementById('track-all');
    const sync = () => {
        const ticked = boxes.filter((box) => box.checked).length;
        count.textContent = ticked;
        submit.disabled = ticked === 0;
        all.textContent = ticked === boxes.length ? 'Untick all' : 'Tick all';
    };
    boxes.forEach((box) => box.addEventListener('change', sync));
    all.addEventListener('click', () => {
        const tick = boxes.some((box) => !box.checked);
        boxes.forEach((box) => { box.checked = tick; });
        sync();
    });
    sync();
}

// Hunt filter form: cascading dropdowns and the live "active filters" bar.
const form = document.getElementById('hunt-form');
if (form) {
    const catalog = JSON.parse(document.getElementById('catalog-data').textContent);
    const field = (name) => form.elements[name];

    // Look a brand up regardless of how it was typed.
    const lookup = (map, brand) => {
        const wanted = brand.trim().toLowerCase();
        const key = Object.keys(map).find((k) => k.toLowerCase() === wanted);
        return key ? map[key] : null;
    };
    const everything = (map) => Object.values(map).flat();
    const fill = (listId, values) => {
        document.getElementById(listId).replaceChildren(...values.map((v) => new Option('', v)));
    };

    // Laptops, tower PCs or both. One of the two always stays ticked.
    const wants = (kind) => form.querySelector(`input[name="kinds"][value="${kind}"]`).checked;

    // Brand -> series suggestions (all ticked brands, or every brand when none is ticked).
    const syncSeries = () => {
        const ticked = [...form.querySelectorAll('input[name="brands"]:checked')].map((box) => box.value);
        const maps = [wants('laptop') && catalog.series, wants('tower') && catalog.tower_series].filter(Boolean);
        const series = ticked.flatMap((brand) => maps.flatMap((names) => lookup(names, brand) || []));
        fill('dl-series', ticked.length ? series : maps.flatMap(everything));
    };

    // A tower has no screen or carrying weight to filter on, and fewer stores sell one.
    const syncKind = (changed) => {
        if (!wants('laptop') && !wants('tower')) (changed || form.querySelector('input[name="kinds"]')).checked = true;
        const laptop = wants('laptop');
        const tower = wants('tower');
        form.querySelectorAll('[data-laptop-only]').forEach((part) => {
            part.classList.toggle('hidden', !laptop);
            part.querySelectorAll('input, select').forEach((input) => { input.disabled = !laptop; });
        });
        form.querySelectorAll('[data-tower-only]').forEach((part) => part.classList.toggle('hidden', !tower));
        form.querySelectorAll('input[name="retailers"]').forEach((box) => {
            box.disabled = !laptop && !catalog.tower_retailers.includes(box.value);
            box.closest('label').classList.toggle('opacity-50', box.disabled);
        });
        const word = laptop && tower ? 'laptop or tower PC' : tower ? 'tower PC' : 'laptop';
        form.querySelectorAll('[data-kind-word]').forEach((spot) => { spot.textContent = word; });
        field('series').placeholder = laptop ? 'Any, e.g. Legion Pro 7' : 'Any, e.g. Legion Tower 5';
        syncSeries();
    };

    // Ticked brands -> the rows of choices shown for them (every row when no brand is ticked).
    // Choices of a brand that is not ticked are hidden and unticked.
    const syncGroups = (brandField, attribute, changed) => {
        const ticked = [...form.querySelectorAll(`input[name="${brandField}"]:checked`)].map((box) => box.value);
        form.querySelectorAll(`[${attribute}]`).forEach((group) => {
            const show = !ticked.length || ticked.includes(group.getAttribute(attribute));
            group.classList.toggle('hidden', !show);
            if (changed && !show) group.querySelectorAll('input').forEach((box) => { box.checked = false; });
        });
    };
    const syncCpu = (changed) => syncGroups('cpu_brand', 'data-cpu-group', changed);
    const syncGpu = (changed) => syncGroups('gpu_brand', 'data-gpu-group', changed);

    // One removable chip per filter that is set.
    const bar = document.getElementById('active-filters');
    const empty = document.getElementById('no-filters');
    const chipText = (input) => {
        const { chipPrefix = '', chipSuffix = '' } = input.dataset;
        if (input.type === 'checkbox') return input.checked ? input.value + chipSuffix : '';
        if (input.tagName === 'SELECT') return input.value ? chipPrefix + input.selectedOptions[0].dataset.chipText : '';
        return input.value.trim() ? chipPrefix + input.value.trim() + chipSuffix : '';
    };
    const syncChips = () => {
        const chips = [];
        form.querySelectorAll('[data-chip]').forEach((input) => {
            const text = input.disabled ? '' : chipText(input);
            if (!text) return;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'chip hover:bg-slate-200 dark:hover:bg-slate-700';
            chip.title = 'Remove this filter';
            chip.append(text, ' ×');
            chip.addEventListener('click', () => {
                if (input.type === 'checkbox') input.checked = false; else input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true }));
            });
            chips.push(chip);
        });
        bar.replaceChildren(...chips);
        empty.classList.toggle('hidden', chips.length > 0);
    };

    form.addEventListener('input', (event) => {
        const name = event.target.name;
        if (name === 'brands') syncSeries();
        if (name === 'kinds') syncKind(event.target);
        if (name === 'cpu_brand') syncCpu(true);
        if (name === 'gpu_brand') syncGpu(true);
        syncChips();
    });

    syncKind();
    syncCpu(false);
    syncGpu(false);
    syncChips();
}

// Tracked page: a listing's small chart opens its full price history. The tracker's own checks and the
// earlier prices imported from BuyHatke are two separate lines; clicking one in the legend hides it.
const historyDialog = document.getElementById('history-dialog');
if (historyDialog) {
    const part = (name) => historyDialog.querySelector(`[data-history-${name}]`);
    const rupees = (amount) => '₹' + Number(amount).toLocaleString('en-IN');
    const day = (ms, withYear = true) => new Date(ms).toLocaleDateString('en-IN',
        { day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) });
    const SERIES = [
        { key: 'own', label: 'This tracker', light: '#059669', dark: '#199e70', dash: [] },
        { key: 'imported', label: 'BuyHatke', light: '#2a78d6', dark: '#3987e5', dash: [6, 4] },
    ];
    let chart = null;
    let loaded = null;
    let days = 30;  // opens on the last month, where the tracker's own checks are

    // A vertical line under the pointer, so the date being read is unmistakable.
    const crosshair = {
        id: 'crosshair',
        afterDatasetsDraw(c) {
            const active = c.tooltip && c.tooltip.getActiveElements();
            if (!active || !active.length) return;
            const { ctx, chartArea } = c;
            ctx.save();
            ctx.strokeStyle = c.options.scales.x.grid.color;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(active[0].element.x, chartArea.top);
            ctx.lineTo(active[0].element.x, chartArea.bottom);
            ctx.stroke();
            ctx.restore();
        },
    };

    function draw() {
        const dark = matchMedia('(prefers-color-scheme: dark)').matches;
        const ink = dark ? '#94a3b8' : '#64748b';
        const grid = dark ? '#1e293b' : '#e2e8f0';
        const from = days ? Date.now() - days * 86400000 : 0;
        const shown = SERIES.map((series) => ({ ...series, points: loaded[series.key].filter((p) => p.x >= from) }))
            .filter((series) => series.points.length);
        const all = shown.flatMap((series) => series.points.map((p) => ({ ...p, by: series.label })))
            .sort((a, b) => a.x - b.x);  // the chart's date range reads the first and last

        if (all.length) {
            const low = all.reduce((a, b) => (b.y < a.y ? b : a));
            const high = all.reduce((a, b) => (b.y > a.y ? b : a));
            part('stats').textContent = `Lowest ${rupees(low.y)} on ${day(low.x)} · highest ${rupees(high.y)} on ${day(high.x)}`;
        } else {
            part('stats').textContent = 'No prices in this period.';
        }

        // The table lists changes only: a row each time a source's price differs from its own previous one.
        // The tracker checks several times a day, so its rows carry the time too.
        const time = (ms) => new Date(ms).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
        const rows = shown.flatMap((series) => series.points.filter((p, i, list) => i === 0 || list[i - 1].y !== p.y)
            .map((p) => ({ ...p, by: series.label, own: series.key === 'own' })));
        rows.sort((a, b) => a.x - b.x);
        part('table').replaceChildren(...rows.reverse().map((p) => {
            const row = document.createElement('tr');
            [p.own ? `${day(p.x)}, ${time(p.x)}` : day(p.x), rupees(p.y), p.by].forEach((text) => {
                const cell = document.createElement('td');
                cell.className = 'py-0.5 pr-3';
                cell.textContent = text;
                row.append(cell);
            });
            return row;
        }));

        if (chart) chart.destroy();
        const span = all.length ? all[all.length - 1].x - all[0].x : 0;
        chart = new Chart(part('chart'), {
            type: 'line',
            data: {
                datasets: shown.map((series) => ({
                    label: series.label,
                    data: series.points,
                    borderColor: dark ? series.dark : series.light,
                    backgroundColor: dark ? series.dark : series.light,
                    borderWidth: 2,
                    borderDash: series.dash,
                    stepped: true,  // a price holds until the next time it was seen to change
                    pointRadius: series.points.length <= 40 ? 3 : 0,
                    pointHoverRadius: 5,
                    pointHitRadius: 12,
                })),
            },
            plugins: [crosshair],
            options: {
                maintainAspectRatio: false,
                animation: false,
                parsing: false,
                interaction: { mode: 'nearest', axis: 'x', intersect: false },
                scales: {
                    x: {
                        type: 'linear',
                        // Keep to the dates there are prices for, with a little air at each end.
                        min: all.length ? all[0].x - span * 0.02 : undefined,
                        max: all.length ? all[all.length - 1].x + span * 0.02 : undefined,
                        grid: { color: grid, drawTicks: false },
                        border: { display: false },
                        ticks: { color: ink, maxTicksLimit: 7, maxRotation: 0, padding: 8,
                                 includeBounds: false,
                                 callback: (value) => day(value, span > 200 * 86400000) },
                    },
                    y: {
                        grid: { color: grid, drawTicks: false },
                        border: { display: false },
                        ticks: { color: ink, padding: 8, maxTicksLimit: 6, callback: (value) => rupees(value) },
                    },
                },
                plugins: {
                    legend: { position: 'top', align: 'start',
                              labels: { color: ink, usePointStyle: true, pointStyle: 'line', boxWidth: 28, padding: 16 } },
                    tooltip: {
                        displayColors: true,
                        callbacks: {
                            title: (items) => `${day(items[0].parsed.x)}, ${new Date(items[0].parsed.x).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}`,
                            label: (item) => ` ${rupees(item.parsed.y)} · ${item.dataset.label}`,
                        },
                    },
                },
            },
        });
    }

    document.querySelectorAll('[data-history]').forEach((button) => {
        button.addEventListener('click', async () => {
            part('title').textContent = 'Loading…';
            historyDialog.showModal();
            try {
                const response = await fetch(button.dataset.history);
                if (!response.ok) throw new Error(response.status);
                loaded = await response.json();
            } catch (error) {
                part('title').textContent = 'The price history could not be loaded.';
                return;
            }
            part('title').textContent = loaded.title;
            part('store').textContent = loaded.store;
            part('note').textContent = loaded.note || '';
            part('link').hidden = !loaded.link;
            if (loaded.link) part('link').href = loaded.link;
            draw();
        });
    });
    historyDialog.querySelectorAll('[data-history-range]').forEach((button) => {
        button.addEventListener('click', () => {
            days = Number(button.dataset.historyRange);
            historyDialog.querySelectorAll('[data-history-range]').forEach((other) =>
                other.setAttribute('aria-pressed', String(other === button)));
            if (loaded) draw();
        });
    });
    part('close').addEventListener('click', () => historyDialog.close());
    // A click on the dimmed area around the dialog closes it too.
    historyDialog.addEventListener('click', (event) => { if (event.target === historyDialog) historyDialog.close(); });
}
