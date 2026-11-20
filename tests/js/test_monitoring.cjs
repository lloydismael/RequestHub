const test = require('node:test');
const assert = require('node:assert/strict');

const {createPoller, addLatencySample, transactionRate} = require('../../static/hub/monitoring.js');

function harness() {
    const timers = new Map();
    const calls = [];
    const results = [];
    let id = 0;
    let visible = true;
    const controller = createPoller({
        sources: {live: 5000, platform: 60000},
        isVisible: () => visible,
        schedule: (fn, delay) => { timers.set(++id, {fn, delay}); return id; },
        cancel: key => timers.delete(key),
        load: (name, signal) => new Promise(resolve => calls.push({name, signal, resolve})),
        update: (name, result) => results.push({name, result}),
    });
    const settle = () => new Promise(resolve => setImmediate(resolve));
    return {controller, calls, results, timers, settle,
        setVisible: value => { visible = value; controller.visibilityChanged(); },
        tick: delay => {
            const [key, job] = [...timers.entries()].find(([, entry]) => entry.delay === delay);
            timers.delete(key);
            job.fn();
        },
    };
}

test('sources poll independently, pause while hidden and allow a manual refresh while paused', async () => {
    const {controller, calls, timers, settle, setVisible, tick} = harness();
    controller.refresh();
    assert.deepEqual(calls.map(call => call.name), ['live', 'platform']);
    calls[0].resolve({status: 'ok'});
    await settle();
    tick(5000);
    assert.deepEqual(calls.map(call => call.name), ['live', 'platform', 'live']);
    setVisible(false);
    assert.equal(timers.size, 0);
    assert.equal(calls[1].signal.aborted, true);
    assert.equal(calls[2].signal.aborted, true);
    setVisible(true);
    assert.equal(calls.length, 5);
    controller.setPaused(true);
    controller.refresh();
    assert.equal(calls.length, 7);
    calls[5].resolve({status: 'ok'});
    calls[6].resolve({status: 'ok'});
    await settle();
    assert.equal(timers.size, 0);
});

test('failure backs off and lost authorization stops both sources, including late responses', async () => {
    const {controller, calls, results, timers, settle, tick} = harness();
    controller.refresh();
    calls[0].resolve({status: 'unavailable'});
    await settle();
    tick(10000);
    calls[2].resolve({status: 'denied'});
    await settle();
    assert.equal(calls[1].signal.aborted, true);
    calls[1].resolve({status: 'ok', payload: {sensitive: 'stale'}});
    await settle();
    controller.refresh();
    assert.equal(calls.length, 3);
    assert.equal(timers.size, 0);
    assert.deepEqual(results.map(({result}) => result.status), ['unavailable', 'denied']);
});

test('latency history restarts rather than blending workers', () => {
    const first = {worker: 'A', sampled_at: '2025-01-01T00:00:00Z', p95_ms: 15, average_ms: 10};
    const second = {...first, sampled_at: '2025-01-01T00:00:05Z', p95_ms: 24};
    const third = {...second, worker: 'B', sampled_at: '2025-01-01T00:00:10Z'};
    let history = addLatencySample([], first);
    history = addLatencySample(history, second);
    assert.equal(history.length, 2);
    history = addLatencySample(history, third);
    assert.deepEqual(history.map(({worker}) => worker), ['B']);
});

test('transaction rate uses elapsed time, preserves zero and rejects reset counters', () => {
    const before = {transactions: 20, stats_reset_at: 'old', sampled_at: '2025-01-01T00:00:00Z'};
    const after = {...before, transactions: 30, sampled_at: '2025-01-01T00:00:05Z'};
    assert.equal(transactionRate(before, after), 2);
    assert.equal(transactionRate(before, {...after, transactions: 20}), 0);
    assert.equal(transactionRate(before, {...after, transactions: 8}), null);
    assert.equal(transactionRate(before, {...after, stats_reset_at: 'new'}), null);
});
