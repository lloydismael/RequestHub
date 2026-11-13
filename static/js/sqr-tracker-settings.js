(function () {
    'use strict';
    var control = document.getElementById('sqr-show-classic');
    var status = document.getElementById('sqr-visibility-status');
    if (!control || !status) return;
    var saved = control.checked;
    control.addEventListener('change', function () {
        var value = control.checked;
        control.disabled = true; status.textContent = 'Saving tracker visibility…';
        fetch(control.dataset.endpoint, {method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': control.dataset.csrf},
            body: JSON.stringify({show_classic_sqr_tracker: value})})
            .then(function (response) {
                if (!response.ok || response.redirected) throw new Error('Could not save tracker visibility.');
                return response.json();
            }).then(function (data) {
                if (!data.ok || typeof data.show_classic_sqr_tracker !== 'boolean') throw new Error('Could not save tracker visibility.');
                saved = data.show_classic_sqr_tracker;
                var params = new URLSearchParams(window.location.search);
                if (!saved && params.get('view') === 'tracker') params.set('view', 'prototype');
                window.location.assign(window.location.pathname + '?' + params.toString());
            }).catch(function (error) {
                control.checked = saved; status.textContent = error.message; control.disabled = false;
            });
    });
})();
