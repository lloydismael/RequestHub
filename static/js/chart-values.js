(function () {
    'use strict';

    const number = new Intl.NumberFormat('en-PH', { maximumSignificantDigits: 21 });
    const pesos = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP' });

    function cell(tag, text, scope) {
        const el = document.createElement(tag);
        el.textContent = text;
        if (scope) el.scope = scope;
        return el;
    }

    function render(holder, { labels, series, format = number.format, keyLabel = 'Category', title = 'Exact chart values' }) {
        if (!holder) return;
        const table = document.createElement('table');
        table.className = 'rh-chart-values-table';
        const head = document.createElement('thead');
        const headers = document.createElement('tr');
        headers.append(cell('th', keyLabel, 'col'), ...series.map(s => cell('th', s.label, 'col')));
        head.append(headers);
        const body = document.createElement('tbody');
        labels.forEach((label, i) => {
            const row = document.createElement('tr');
            row.append(cell('th', label, 'row'));
            series.forEach(s => {
                const value = s.values[i];
                row.append(cell('td', value == null ? '—' : (s.format || format)(value)));
            });
            body.append(row);
        });
        holder.setAttribute('role', 'region');
        holder.setAttribute('aria-label', title);
        holder.tabIndex = 0;
        table.append(cell('caption', title), head, body);
        holder.replaceChildren(table);
    }

    window.rhChartValues = { render, number: number.format, pesos: pesos.format };
}());
