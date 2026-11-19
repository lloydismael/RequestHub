(function () {
    'use strict';

    const dialog = document.getElementById('rptDetailDialog');
    if (typeof dialog?.showModal !== 'function') return;
    const heading = document.getElementById('rptDetailTitle');
    const content = dialog.querySelector('[data-report-detail-content]');
    const template = document.getElementById('rptLoadMoreTemplate');
    const shell = dialog.closest('[data-report-preview-limit]');
    const limit = Number(shell.dataset.reportPreviewLimit);
    let opener;

    shell.querySelectorAll('[data-report-preview]').forEach(preview => {
        const items = [...preview.querySelectorAll(':scope > li, :scope > table > tbody > tr')];
        if (items.length <= limit) return;
        const labelId = preview.getAttribute('aria-labelledby');
        const overflow = items.slice(limit);
        const footer = template.content.cloneNode(true);
        const button = footer.querySelector('button');
        footer.querySelector('.rpt-preview-count').textContent = `Showing ${limit} of ${items.length}`;
        button.setAttribute('aria-describedby', labelId);
        button.addEventListener('click', () => {
            if (dialog.open) return;
            const detail = preview.cloneNode(true);
            detail.removeAttribute('data-report-preview');
            detail.setAttribute('aria-labelledby', heading.id);
            detail.querySelectorAll('[hidden]').forEach(item => { item.hidden = false; });
            heading.textContent = document.getElementById(labelId).textContent.trim();
            content.replaceChildren(detail);
            opener = button;
            dialog.showModal();
            content.scrollTop = 0;
        });
        preview.after(footer);
        overflow.forEach(item => { item.hidden = true; });
    });
    dialog.addEventListener('close', () => {
        content.replaceChildren();
        opener?.focus({ preventScroll: true });
        opener = null;
    });
}());
