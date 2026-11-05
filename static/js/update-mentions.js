(function () {
    'use strict';
    var composer = document.querySelector('[data-mention-composer]');
    if (!composer) return;
    var input = composer.querySelector('textarea');
    var data = composer.querySelector('#request-mention-people');
    var list = composer.querySelector('#request-mention-suggestions');
    var trigger = composer.querySelector('[data-insert-mention]');
    var status = composer.querySelector('[data-mention-status]');
    if (!input || !data) return;
    var people = JSON.parse(data.textContent);
    var matches = [];
    var active = 0;
    var tokenStart = -1;
    input.setAttribute('aria-describedby', 'request-mention-help');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', list.id);

    function close() {
        list.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function select(index) {
        var person = matches[index];
        if (!person || tokenStart < 0) return;
        var end = input.selectionStart;
        var value = '@' + person.username + ' ';
        input.setRangeText(value, tokenStart, end, 'end');
        input.focus();
        close();
        status.textContent = person.name + ' mentioned.';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function highlight() {
        Array.prototype.forEach.call(list.querySelectorAll('[role="option"]'), function (option, index) {
            option.setAttribute('aria-selected', index === active ? 'true' : 'false');
            if (index === active) {
                input.setAttribute('aria-activedescendant', option.id);
                option.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    function update() {
        var prefix = input.value.slice(0, input.selectionStart);
        var match = prefix.match(/(?:^|[\s([{])@([\p{L}\p{N}\p{M}_.+@-]*)$/u);
        if (!match) { close(); return; }
        tokenStart = prefix.length - match[1].length - 1;
        var query = match[1].toLocaleLowerCase();
        matches = people.filter(function (person) {
            return person.name.toLocaleLowerCase().includes(query) || person.username.toLocaleLowerCase().includes(query);
        }).slice(0, 8);
        active = 0;
        list.replaceChildren();
        matches.forEach(function (person, index) {
            var option = document.createElement('button');
            option.type = 'button';
            option.className = 'update-mention-option';
            option.id = 'request-mention-option-' + index;
            option.setAttribute('role', 'option');
            option.tabIndex = -1;
            var name = document.createElement('strong');
            name.textContent = person.name;
            var details = document.createElement('span');
            details.className = 'update-mention-option__details';
            details.textContent = '@' + person.username + ' · ' + person.role;
            option.append(name, details);
            option.addEventListener('mousedown', function (event) { event.preventDefault(); });
            option.addEventListener('click', function () { select(index); });
            list.appendChild(option);
        });
        if (!matches.length) {
            var empty = document.createElement('p');
            empty.className = 'update-mention-empty';
            empty.textContent = 'No related people match this name.';
            list.appendChild(empty);
            input.removeAttribute('aria-activedescendant');
        }
        list.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        status.textContent = matches.length + ' related people available. Use arrow keys and Enter to select.';
        highlight();
    }

    input.addEventListener('input', update);
    input.addEventListener('click', update);
    input.addEventListener('keydown', function (event) {
        if (list.hidden) return;
        if (event.key === 'Escape') { event.preventDefault(); close(); return; }
        if (!matches.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            active = (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
            highlight();
        } else if (event.key === 'Enter' && !event.isComposing) {
            event.preventDefault();
            select(active);
        }
    });
    trigger.addEventListener('click', function () {
        input.focus();
        var before = input.value.slice(0, input.selectionStart);
        input.setRangeText((before && !/\s$/.test(before) ? ' ' : '') + '@', input.selectionStart, input.selectionEnd, 'end');
        update();
    });
    composer.addEventListener('focusout', function (event) {
        if (!composer.contains(event.relatedTarget)) close();
    });
    document.addEventListener('click', function (event) {
        if (!composer.contains(event.target)) close();
    });
}());
