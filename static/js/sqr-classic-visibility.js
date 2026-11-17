(function () {
    'use strict';
    var root = document.getElementById('sqr-classic-setting');
    var checkbox = document.getElementById('sqr-classic-visibility');
    var status = document.getElementById('sqr-classic-status');
    if (!root || !checkbox || !status) return;
    var persisted = checkbox.checked;
    var pending = false;
    checkbox.addEventListener('change', async function () {
        if (pending) return;
        pending = true;
        checkbox.disabled = true;
        root.setAttribute('aria-busy', 'true');
        status.textContent = 'Saving global visibility…';
        var controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 20000);
        try {
            var csrf = root.querySelector('input[name="csrfmiddlewaretoken"]');
            var response = await fetch(root.dataset.settingsUrl, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf ? csrf.value : ''},
                body: JSON.stringify({show_classic_sqr_tracker: checkbox.checked}),
            });
            var payload = await response.json();
            if (!response.ok || !payload || payload.ok !== true || typeof payload.show_classic_sqr_tracker !== 'boolean') {
                throw new Error('Visibility was not saved');
            }
            persisted = payload.show_classic_sqr_tracker;
            checkbox.checked = persisted;
            status.textContent = 'Global visibility saved. Users will see the change after reloading SQR.';
        } catch (error) {
            checkbox.checked = persisted;
            status.textContent = 'Could not save global visibility. Previous setting restored. Please try again.';
        } finally {
            clearTimeout(timeout);
            pending = false;
            checkbox.disabled = false;
            root.removeAttribute('aria-busy');
        }
    });
})();
