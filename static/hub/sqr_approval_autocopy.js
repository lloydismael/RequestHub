(function () {
    'use strict';
    window.startSqrApprovalDraft = function () {
        var resolveBody;
        var rejectBody;
        var body = new Promise(function (resolve, reject) {
            resolveBody = resolve;
            rejectBody = reject;
        });
        body.catch(function () {});
        var copied = Promise.resolve(false);
        // Start clipboard permission during the button gesture, before saving or fetching.
        if (navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== 'undefined') {
            try {
                var htmlBlob = body.then(function (data) { return new Blob([data.html], {type: 'text/html'}); });
                var textBlob = body.then(function (data) { return new Blob([data.text], {type: 'text/plain'}); });
                htmlBlob.catch(function () {});
                textBlob.catch(function () {});
                copied = navigator.clipboard.write([new ClipboardItem({
                    'text/html': htmlBlob,
                    'text/plain': textBlob
                })]).then(function () { return true; }, function () { return false; });
            } catch (error) {
                copied = Promise.resolve(false);
            }
        }
        return {
            cancel: function () { rejectBody(new Error('Approval was not saved.')); },
            complete: async function (previewUrl) {
                try {
                    var response = await fetch(previewUrl, {credentials: 'same-origin'});
                    if (!response.ok) throw new Error('Unable to load email body.');
                    var preview = new DOMParser().parseFromString(await response.text(), 'text/html');
                    var html = JSON.parse(preview.getElementById('sqr-email-html').textContent);
                    var text = JSON.parse(preview.getElementById('sqr-email-text').textContent);
                    var composeUrl = preview.getElementById('sqr-email-open').getAttribute('href');
                    if (!composeUrl || !composeUrl.startsWith('mailto:')) throw new Error('Invalid compose link.');
                    resolveBody({html: html, text: text});
                    if (!await copied) {
                        window.location.href = previewUrl;
                        return;
                    }
                    var notice = document.getElementById('sqr-autocopy-notice');
                    if (!notice) {
                        notice = document.createElement('div');
                        notice.id = 'sqr-autocopy-notice';
                        notice.className = 'alert alert-success';
                        notice.setAttribute('role', 'status');
                        document.querySelector('main').prepend(notice);
                    }
                    notice.textContent = 'Approval saved. Formatted email copied. Paste into Outlook using Ctrl+V. If Outlook did not open, ';
                    var link = document.createElement('a');
                    link.href = composeUrl;
                    link.textContent = 'open the email draft';
                    notice.appendChild(link);
                    window.location.href = composeUrl;
                } catch (error) {
                    rejectBody(error);
                    window.location.href = previewUrl;
                }
            }
        };
    };
})();
