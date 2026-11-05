(function () {
    'use strict';
    function isLocalPath(url) {
        return !!url && url.charAt(0) === '/' && url.charAt(1) !== '/';
    }
    function submitMicrosoftDraft(url, csrfToken) {
        var submitForm = document.createElement('form');
        submitForm.method = 'post';
        submitForm.action = url;
        var token = document.createElement('input');
        token.type = 'hidden';
        token.name = 'csrfmiddlewaretoken';
        token.value = csrfToken;
        submitForm.appendChild(token);
        document.body.appendChild(submitForm);
        submitForm.submit();
    }
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
            // options.microsoftUrl + options.csrfToken launch the Microsoft 365 draft without loading the preview.
            complete: async function (previewUrl, options) {
                options = options || {};
                if (isLocalPath(options.microsoftUrl) && options.csrfToken) {
                    // Microsoft 365 builds the whole draft server side, so the clipboard copy is not needed.
                    rejectBody(new Error('Clipboard copy is not needed.'));
                    submitMicrosoftDraft(options.microsoftUrl, options.csrfToken);
                    return;
                }
                try {
                    var response = await fetch(previewUrl, {credentials: 'same-origin'});
                    if (!response.ok) throw new Error('Unable to load email body.');
                    var preview = new DOMParser().parseFromString(await response.text(), 'text/html');
                    var html = JSON.parse(preview.getElementById('sqr-email-html').textContent);
                    var text = JSON.parse(preview.getElementById('sqr-email-text').textContent);
                    var openLink = preview.getElementById('sqr-email-open');
                    var composeUrl = openLink.getAttribute('href');
                    if (!composeUrl || !composeUrl.startsWith('mailto:')) throw new Error('Invalid compose link.');
                    resolveBody({html: html, text: text});
                    var microsoftForm = preview.getElementById('sqr-email-microsoft');
                    var microsoftUrl = microsoftForm ? microsoftForm.getAttribute('action') : '';
                    var csrfField = microsoftForm && microsoftForm.querySelector ? microsoftForm.querySelector('input[name="csrfmiddlewaretoken"]') : null;
                    // Microsoft 365 builds the whole draft server side, so no paste step is needed.
                    if (isLocalPath(microsoftUrl) && csrfField) {
                        submitMicrosoftDraft(microsoftUrl, csrfField.value);
                        return;
                    }
                    // mailto cannot insert HTML, so keep the body blank for the formatted paste.
                    var clipboardCopied = await copied;
                    if (!clipboardCopied) {
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
                    notice.textContent = 'Approval saved. Formatted email copied. Click the blank message body in Outlook and paste with Ctrl+V. If Outlook did not open, ';
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
