(function () {
    'use strict';
    var microsoftForm = document.getElementById('sqr-email-microsoft');
    if (microsoftForm && microsoftForm.getAttribute('data-autostart') === 'true') {
        // After Microsoft sign-in, continue automatically once; the guard stops a sign-in redirect loop.
        var guardKey = 'sqr-outlook-resume-' + microsoftForm.getAttribute('data-key');
        var lastResume = 0;
        try { lastResume = Number(window.sessionStorage.getItem(guardKey)) || 0; } catch (error) {}
        if (Date.now() - lastResume > 120000) {
            try { window.sessionStorage.setItem(guardKey, String(Date.now())); } catch (error) {}
            microsoftForm.submit();
            return;
        }
    }
    var button = document.getElementById('sqr-email-copy');
    if (!button) return;
    var feedback = document.getElementById('sqr-email-feedback');
    var plain = JSON.parse(document.getElementById('sqr-email-text').textContent);
    var html = JSON.parse(document.getElementById('sqr-email-html').textContent);

    function manualCopy() {
        document.getElementById('sqr-email-plain-fallback').open = true;
        var field = document.getElementById('sqr-email-plain');
        field.focus();
        field.select();
        feedback.textContent = 'Clipboard access is unavailable. Copy the selected plain text, or download the formatted .eml template. Your approval is still saved.';
    }

    async function copyPlain() {
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
            manualCopy();
            return;
        }
        try {
            await navigator.clipboard.writeText(plain);
            feedback.textContent = 'Plain-text body copied. Rich formatting is unavailable here; download the .eml template to preserve the layout.';
        } catch (error) {
            manualCopy();
        }
    }

    button.addEventListener('click', async function () {
        button.disabled = true;
        try {
            if (navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== 'undefined') {
                try {
                    await navigator.clipboard.write([new ClipboardItem({
                        'text/html': new Blob([html], {type: 'text/html'}),
                        'text/plain': new Blob([plain], {type: 'text/plain'})
                    })]);
                    feedback.textContent = 'Formatted body copied. Open the email draft and paste into the message. If Outlook strips formatting, use the .eml template.';
                } catch (error) {
                    await copyPlain();
                }
            } else {
                await copyPlain();
            }
        } finally {
            button.disabled = false;
        }
    });
})();
