(() => {
  const updates = new WeakMap();
  const initializedForms = new WeakSet();

  function refresh(form) {
    const visibility = new Map();
    function isVisible(input) {
      if (!visibility.has(input)) {
        visibility.set(input, false);
        visibility.set(input, updates.get(input)?.(isVisible));
      }
      return visibility.get(input);
    }
    form.querySelectorAll('[data-form-custom="true"]').forEach(isVisible);
  }

  function enhance(form) {
    const fields = Array.from(form.querySelectorAll('[data-form-custom="true"], [data-form-section], [data-form-width]'));
    if (!fields.length) return;
    const groups = new Map();
    fields.forEach(input => {
      const row = input.closest('[data-editor-field], .mb-3, .mb-2') || input.parentElement;
      if (!row || !row.parentElement || row.tagName === 'TD' || row.tagName === 'TR') return;
      if (!groups.has(row.parentElement)) groups.set(row.parentElement, new Map());
      const entries = groups.get(row.parentElement);
      const order = Number(input.dataset.formOrder || 0);
      entries.set(row, Math.min(entries.get(row) === undefined ? order : entries.get(row), order));
    });
    groups.forEach(entries => {
      const ordered = Array.from(entries).sort((a, b) => a[1] - b[1]);
      const slots = Array.from(entries.keys()).sort((a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
      const anchors = slots.map(row => {
        const anchor = document.createComment('form-field');
        row.before(anchor);
        return anchor;
      });
      ordered.forEach(([row], index) => anchors[index].after(row));
      anchors.forEach(anchor => anchor.remove());
    });
    fields.forEach(input => {
      if (updates.has(input)) return;
      const row = input.closest('[data-editor-field], .mb-3, .mb-2, .col-md-6, .col-12') || input.parentElement;
      if (input.dataset.formWidth === 'half') row.classList.add('editor-field-half');
      const label = Array.from(input.labels || []).find(item => item.htmlFor === input.id && form.contains(item));
      if (label && input.dataset.formLabel) label.textContent = input.dataset.formLabel;
      if (input.dataset.formSection && !row.querySelector('.editor-section')) {
        const heading = document.createElement('h6');
        heading.className = 'editor-section';
        heading.textContent = input.dataset.formSection;
        row.prepend(heading);
      }
      const condition = input.dataset.formCondition && JSON.parse(input.dataset.formCondition);
      const prefix = input.name.includes('-') ? input.name.slice(0, input.name.lastIndexOf('-') + 1) : '';
      const update = isVisible => {
        let visible = input.dataset.formArchived !== 'true' && input.dataset.formHidden !== 'true';
        if (condition && input.dataset.formArchived !== 'true') {
          const dependency = form.elements.namedItem(prefix + condition.field) || form.elements.namedItem(condition.field);
          let values = [];
          if (dependency) {
            if (dependency.options) values = Array.from(dependency.selectedOptions).map(option => option.value);
            else values = [dependency.type === 'checkbox' ? (dependency.checked ? 'True' : 'False') : dependency.value];
          }
          const empty = !values.length || values.every(value => !value);
          const expected = condition.value === null || condition.value === undefined ? '' :
            (typeof condition.value === 'boolean' ? (condition.value ? 'True' : 'False') : String(condition.value));
          const equal = values.includes(expected);
          const parentVisible = dependency?.dataset?.formCustom !== 'true' || isVisible(dependency);
          visible = parentVisible && ({equals: equal, not_equals: !equal, empty, not_empty: !empty})[condition.operator];
        }
        row.hidden = !visible;
        input.disabled = !visible;
        if (!input.dataset.editorRequired) input.dataset.editorRequired = input.required ? 'true' : 'false';
        input.required = visible && input.dataset.editorRequired === 'true';
        return visible;
      };
      updates.set(input, update);
    });
    if (!initializedForms.has(form)) {
      initializedForms.add(form);
      form.addEventListener('change', () => refresh(form));
      form.addEventListener('input', () => refresh(form));
    }
    refresh(form);
  }
  document.addEventListener('DOMContentLoaded', () => document.querySelectorAll('form').forEach(enhance));
  document.addEventListener('form-editor-enhance', event => enhance(event.detail.form));
  document.addEventListener('form-editor-refresh', event => enhance(event.detail));
  document.addEventListener('form-editor-preview', () => document.querySelectorAll('#editor-preview form').forEach(enhance));
})();
