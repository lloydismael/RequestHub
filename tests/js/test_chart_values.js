const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
    const create = (tag) => ({ tag, children: [], setAttribute() {}, append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = nodes; } });
    const context = { window: {}, document: { createElement: create } };
    const script = path.join(__dirname, '..', '..', 'static', 'js', 'chart-values.js');
    if (fs.existsSync(script)) vm.runInNewContext(fs.readFileSync(script, 'utf8'), context);
    return [context.window.rhChartValues, create('div')];
}

test('chart values preserve exact decimals, real zeros and literal labels', () => {
    const [values, holder] = load();
    assert.ok(values, 'Shared values renderer is not loaded');
    const formatted = [];
    values.render(holder, { labels: ['<North>', 'Missing', 'July'], series: [{ label: 'Hours', values: [0, null, 2.75] }],
        format(n) { formatted.push(n); return String(n); } });
    const table = holder.children[0];
    const rows = table.children.find(node => node.tag === 'tbody').children;
    assert.deepEqual(rows.map(row => row.children.map(cell => cell.textContent)), [['<North>', '0'], ['Missing', '—'], ['July', '2.75']]);
    assert.deepEqual(formatted, [0, 2.75]);
});
