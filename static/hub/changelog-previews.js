(function () {
    'use strict';

    function refreshCurrentPage(event) {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        var destination = new URL(event.currentTarget.href);
        if (destination.origin !== window.location.origin || destination.pathname !== window.location.pathname ||
            destination.search !== window.location.search) return;
        // A fragment-only navigation would leave the old comparison URLs in place.
        event.preventDefault();
        window.history.replaceState(null, '', destination.href);
        window.location.reload();
    }

    document.querySelectorAll('[data-changelog-preview]').forEach(function (container) {
        var disclosure = container.closest('details');
        var link = container.querySelector('[data-changelog-preview-link]');
        var status = container.querySelector('[data-changelog-preview-status]');
        if (!disclosure || !link || !status) return;
        var loading = false;
        var loaded = false;

        async function loadPreview() {
            if (!disclosure.open || loading || loaded) return;
            loading = true;
            status.hidden = false;
            status.textContent = 'Loading comparison…';
            try {
                var response = await fetch(link.href, {
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                if (response.redirected || (!response.ok && response.status !== 409)) throw new Error('Preview unavailable');
                var html = new DOMParser().parseFromString(await response.text(), 'text/html');
                if (response.status === 409) {
                    var refresh = html.querySelector('a[data-changelog-preview-refresh]');
                    if (!refresh) throw new Error('Refresh link missing');
                    refresh.addEventListener('click', refreshCurrentPage);
                    link.replaceWith(refresh);
                    status.textContent = "This release's comparisons have changed. Refresh and reopen the comparison.";
                    loaded = true;
                    return;
                }
                var preview = html.querySelector('[data-changelog-preview-content]');
                if (!preview) throw new Error('Preview missing');
                container.appendChild(preview);
                loaded = true;
                status.textContent = 'Comparison loaded.';
            } catch (_) {
                status.textContent = 'Preview unavailable here. Use the link to open it directly.';
            } finally {
                loading = false;
            }
        }

        disclosure.addEventListener('toggle', loadPreview);
        loadPreview();
    });
}());
