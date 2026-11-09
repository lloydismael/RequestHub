(() => {
    'use strict';

    function capture() {
        try {
            const root = document.documentElement.cloneNode(true);
            const originalNodes = document.documentElement.querySelectorAll('*');
            const copiedNodes = root.querySelectorAll('*');
            const scrolls = [];
            originalNodes.forEach((node, index) => {
                const copy = copiedNodes[index];
                if (!copy) return;
                copy.removeAttribute('data-christmas-snapshot-scroll');
                if (!node.scrollLeft && !node.scrollTop) return;
                const id = String(index);
                copy.setAttribute('data-christmas-snapshot-scroll', id);
                scrolls.push({ id, left: node.scrollLeft || 0, top: node.scrollTop || 0 });
            });
            const controls = document.querySelectorAll('input, textarea, select');
            const copies = root.querySelectorAll('input, textarea, select');
            controls.forEach((control, index) => {
                const copy = copies[index];
                if (!copy) return;
                const type = (control.getAttribute('type') || '').toLowerCase();
                if (['password', 'hidden', 'file'].includes(type)) {
                    copy.removeAttribute('value');
                    copy.value = '';
                } else if (control.tagName === 'TEXTAREA') {
                    copy.textContent = control.value;
                } else if (control.tagName === 'SELECT') {
                    Array.from(control.options).forEach((option, optionIndex) => {
                        const target = copy.options[optionIndex];
                        if (option.selected) target.setAttribute('selected', '');
                        else target.removeAttribute('selected');
                    });
                } else {
                    copy.setAttribute('value', control.value || '');
                    if (control.checked) copy.setAttribute('checked', '');
                    else copy.removeAttribute('checked');
                }
            });
            const canvases = document.querySelectorAll('canvas');
            root.querySelectorAll('canvas').forEach((copy, index) => {
                try {
                    const canvas = canvases[index];
                    const image = document.createElement('img');
                    image.setAttribute('src', canvas.toDataURL());
                    image.setAttribute('width', canvas.width);
                    image.setAttribute('height', canvas.height);
                    for (const attribute of ['class', 'style', 'id']) {
                        const value = copy.getAttribute(attribute);
                        if (value) image.setAttribute(attribute, value);
                    }
                    copy.replaceWith(image);
                } catch (_) { copy.remove(); }
            });
            root.querySelectorAll('script, iframe, frame, object, embed, audio, video, meta[http-equiv], .christmas-santa, .christmas-gifts, .christmas-frozen-viewport, .theme-reveal-overlay').forEach(node => node.remove());
            const head = root.querySelector('head');
            Array.from(head.children).forEach(node => {
                if (node.tagName !== 'STYLE' && !(node.tagName === 'LINK' && node.getAttribute('rel') === 'stylesheet')) node.remove();
            });
            const baseURL = new URL(document.baseURI);
            baseURL.search = '';
            baseURL.hash = '';
            const base = document.createElement('base');
            base.setAttribute('href', baseURL.href);
            head.appendChild(base);
            root.querySelectorAll('*').forEach(node => {
                for (const { name } of Array.from(node.attributes)) {
                    if (/^on/i.test(name) || ['autofocus', 'action', 'formaction', 'target', 'srcdoc'].includes(name)) node.removeAttribute(name);
                }
                for (const name of ['src', 'href', 'poster']) {
                    const value = node.getAttribute(name);
                    if (!value || value.startsWith('#')) continue;
                    if (name === 'href' && !['LINK', 'BASE', 'IMAGE', 'USE'].includes(node.tagName)) {
                        node.removeAttribute(name);
                        continue;
                    }
                    const url = new URL(value, baseURL);
                    if (!['http:', 'https:', 'file:', 'data:'].includes(url.protocol)) node.removeAttribute(name);
                    else node.setAttribute(name, url.href);
                }
            });
            for (const { name } of Array.from(root.attributes)) {
                if (/^on/i.test(name)) root.removeAttribute(name);
            }
            const freeze = document.createElement('style');
            freeze.textContent = 'html{scroll-behavior:auto!important}*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important;view-transition-name:none!important}';
            head.appendChild(freeze);
            return {
                html: '<!DOCTYPE html>' + root.outerHTML,
                scrolls,
                viewport: { width: window.innerWidth, height: window.innerHeight, scrollX: window.scrollX, scrollY: window.scrollY },
            };
        } catch (_) { return null; }
    }

    function mount(source, { signal, timeoutMs = 1000 } = {}) {
        if (!source || signal && signal.aborted) return Promise.reject(new Error('Snapshot unavailable'));
        const { viewport, scrolls = [] } = source;
        return new Promise((resolve, reject) => {
            const iframe = document.createElement('iframe');
            iframe.className = 'christmas-frozen-viewport';
            iframe.setAttribute('sandbox', 'allow-same-origin');
            iframe.setAttribute('aria-hidden', 'true');
            iframe.setAttribute('tabindex', '-1');
            iframe.setAttribute('inert', '');
            iframe.setAttribute('title', 'Decorative theme snapshot');
            iframe.style.width = `${viewport.width}px`;
            iframe.style.height = `${viewport.height}px`;
            iframe.style.visibility = 'hidden';
            let disposed = false;
            let settled = false;
            let timer = null;
            const cleanup = () => {
                if (timer !== null) window.clearTimeout(timer);
                timer = null;
                iframe.removeEventListener('load', loaded);
                iframe.removeEventListener('error', failed);
                if (signal) signal.removeEventListener('abort', dispose);
            };
            const dispose = () => {
                if (disposed) return;
                disposed = true;
                cleanup();
                iframe.remove();
                iframe.removeAttribute('srcdoc');
                if (!settled) {
                    settled = true;
                    reject(new Error('Snapshot cancelled'));
                }
            };
            const failed = () => dispose();
            const loaded = async () => {
                try {
                    if (iframe.contentDocument && iframe.contentDocument.fonts) await iframe.contentDocument.fonts.ready;
                    if (disposed) return;
                    iframe.contentWindow.scrollTo(viewport.scrollX, viewport.scrollY);
                    scrolls.forEach(scroll => {
                        const node = iframe.contentDocument.querySelector(`[data-christmas-snapshot-scroll="${scroll.id}"]`);
                        if (!node) return;
                        node.scrollLeft = scroll.left;
                        node.scrollTop = scroll.top;
                    });
                    iframe.style.visibility = 'visible';
                    settled = true;
                    cleanup();
                    resolve({ element: iframe, dispose });
                } catch (_) { dispose(); }
            };
            iframe.addEventListener('load', loaded);
            iframe.addEventListener('error', failed);
            if (signal) signal.addEventListener('abort', dispose, { once: true });
            timer = window.setTimeout(dispose, timeoutMs);
            iframe.srcdoc = source.html;
            source = null;
            document.body.appendChild(iframe);
            if (signal && signal.aborted) dispose();
        });
    }

    window.requestHubChristmasSnapshot = { capture, mount };
})();
