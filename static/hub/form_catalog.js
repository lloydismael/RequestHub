(() => {
    'use strict';

    let serial = 0;

    function setup(catalog) {
        const search = catalog.querySelector('[data-catalog-search]');
        const label = catalog.querySelector('[data-catalog-label]');
        const status = catalog.querySelector('[data-catalog-status]');
        const empty = catalog.querySelector('[data-catalog-empty]');
        const totalText = catalog.querySelector('[data-catalog-total]');
        const filters = Array.from(catalog.querySelectorAll('[data-catalog-filter]'));
        const sections = Array.from(catalog.querySelectorAll('[data-catalog-section]'));
        const items = Array.from(catalog.querySelectorAll('[data-catalog-item]'));
        const total = items.length;
        const categoryOf = item => item.closest('[data-catalog-section]').dataset.catalogSection;
        let category = 'all';

        search.id = `catalog-search-${++serial}`;
        label.htmlFor = search.id;
        totalText.textContent = `${total} forms`;
        filters.forEach(filter => {
            const name = filter.dataset.catalogFilter;
            const count = name === 'all' ? total : items.filter(item => categoryOf(item) === name).length;
            filter.querySelector('[data-catalog-count]').textContent = count;
        });

        function render() {
            const query = search.value.trim().toLowerCase();
            let shown = 0;
            items.forEach(item => {
                const match = (category === 'all' || categoryOf(item) === category) && item.dataset.formSearch.includes(query);
                item.hidden = !match;
                if (match) shown += 1;
            });
            sections.forEach(section => {
                section.hidden = !section.querySelector('[data-catalog-item]:not([hidden])');
            });
            filters.forEach(filter => filter.setAttribute('aria-pressed', String(filter.dataset.catalogFilter === category)));
            empty.hidden = shown > 0;
            status.textContent = shown === total ? `Showing all ${total} forms.` : `Showing ${shown} of ${total} forms.`;
        }

        filters.forEach(filter => {
            filter.addEventListener('click', () => {
                category = filter.dataset.catalogFilter;
                render();
            });
        });
        search.addEventListener('input', render);
        render();
    }

    document.querySelectorAll('[data-form-catalog]').forEach(catalog => {
        if (catalog.dataset.catalogReady) return;
        catalog.dataset.catalogReady = 'true';
        setup(catalog);
    });
})();
