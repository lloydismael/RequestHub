(function () {
    'use strict';
    var root = document.getElementById('sqr-classic-setting');
    var checkbox = document.getElementById('sqr-classic-visibility');
    var status = document.getElementById('sqr-classic-status');
    var badge = document.getElementById('sqr-classic-badge');
    if (!root || !checkbox || !status || !badge) return;
    var persisted = checkbox.checked;
    var pending = false;
    checkbox.addEventListener('change', async function () {
        if (pending) return;
        pending = true;
        checkbox.disabled = true;
        status.classList.remove('settings-save-error');
        status.textContent = 'Saving…';
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
            badge.textContent = persisted ? 'Visible' : 'Hidden';
            badge.dataset.visible = String(persisted);
            status.textContent = 'Saved.';
        } catch (error) {
            checkbox.checked = persisted;
            status.classList.add('settings-save-error');
            status.textContent = 'Could not save. Previous setting restored. Try again.';
        } finally {
            clearTimeout(timeout);
            pending = false;
            checkbox.disabled = false;
        }
    });
})();
