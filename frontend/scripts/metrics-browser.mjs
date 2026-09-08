import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright';
import { createServer } from 'vite';

// Real React mount with controlled HTTP/socket adapters and visibility events.
const fixture = `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useMetrics } from '/src/hooks/useMetrics.js';
import api from '/src/services/api/index.js';
import socket from '/src/services/socket.js';
import { WorkspaceProvider } from '/src/contexts/WorkspaceContext.jsx';
import { workspaceStore } from '/src/services/workspaceStore.js';
import { useMetricHistory } from '/src/components/dashboard/widgets/useWidgetData.js';
import MetricsTab from '/src/components/service-detail/MetricsTab.jsx';
const handlers = new Map();
let hidden = false;
const state = { calls: 0, pending: [] };
api.getSystemMetrics = () => {
    state.calls += 1;
    return new Promise((resolve) => state.pending.push(resolve));
};
socket.socket = { connected: false };
socket.connect = () => {};
socket.on = (name, fn) => { handlers.set(name, fn); return () => handlers.delete(name); };
socket.subscribeMetrics = () => {};
socket.unsubscribeMetrics = () => {};
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => hidden ? 'hidden' : 'visible' });
function App() {
    const [options, setOptions] = useState({ enabled: true, autoRefresh: true });
    const metrics = useMetrics(true, 60, options);
    window.metricsFixture = {
        state, setOptions, metrics,
        emit: (name, data) => handlers.get(name)?.(data),
        resolve: () => state.pending.splice(0).forEach((resolve) => resolve({ cpu: 42 })),
        hide: (value) => { hidden = value; document.dispatchEvent(new Event('visibilitychange')); },
    };
    return React.createElement('output', {}, metrics.loading ? 'loading' : 'ready');
}
createRoot(document.getElementById('root')).render(React.createElement(App));
const widgetState = { calls: 0, pending: [], snapshots: [] };
api.getMetricsHistory = () => {
    widgetState.calls += 1;
    return new Promise((resolve) => widgetState.pending.push(resolve));
};
function Widget({ index, tick }) {
    widgetState.snapshots[index] = useMetricHistory('local', '1h', tick);
    return null;
}
function Widgets() {
    const [tick, setTick] = useState(0);
    window.widgetFixture = {
        state: widgetState, refresh: () => setTick((value) => value + 1),
        resolve: () => widgetState.pending.splice(0).forEach((resolve) => resolve({ points: [widgetState.calls] })),
        workspace: () => workspaceStore.setActiveWorkspace({ id: 'other-workspace', name: 'Other' }),
    };
    return React.createElement(React.Fragment, {},
        React.createElement(Widget, { index: 0, tick }), React.createElement(Widget, { index: 1, tick }));
}
createRoot(document.getElementById('widgets')).render(React.createElement(WorkspaceProvider, {}, React.createElement(Widgets)));
const serviceState = { containers: [], pending: [], python: [] };
api.getContainers = async () => ({ containers: serviceState.containers });
api.getContainerStats = (id) => new Promise((resolve, reject) => serviceState.pending.push({ id, resolve, reject }));
api.getPythonAppStatus = () => new Promise((resolve) => serviceState.python.push(resolve));
function Service() {
    const [app, setApp] = useState(null);
    window.serviceFixture = { state: serviceState, setApp };
    return app ? React.createElement(MetricsTab, { app }) : null;
}
createRoot(document.getElementById('service')).render(React.createElement(Service));
`;
const server = await createServer({
    server: { host: '127.0.0.1', port: 0, open: false },
    plugins: [{
        name: 'metrics-regression-fixture',
        resolveId(id) { if (id === 'virtual:metrics-regression') return '\0metrics-regression'; },
        load(id) { if (id === '\0metrics-regression') return fixture; },
        configureServer(vite) {
            vite.middlewares.use('/__metrics-regression', async (_req, res) => {
                res.setHeader('Content-Type', 'text/html');
                res.end(await vite.transformIndexHtml('/__metrics-regression',
                    '<div id="root"></div><div id="widgets"></div><div id="service"></div><script type="module">import "virtual:metrics-regression"</script>'));
            });
        },
    }],
});
await server.listen();
const executablePath = [
    process.env.CHROME_PATH, chromium.executablePath(),
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find((path) => path && existsSync(path));
let browser;
try {
    browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__metrics-regression`);
    await page.waitForFunction(() => window.metricsFixture?.state.calls === 1);
    const calls = () => page.evaluate(() => window.metricsFixture.state.calls);
    const action = (fn) => page.evaluate(fn);
    await page.waitForTimeout(240);
    assert.equal(await calls(), 1, 'slow initial response cannot overlap fallback');
    await action(() => { window.metricsFixture.metrics.refresh(); window.metricsFixture.metrics.refresh(); });
    assert.equal(await calls(), 1, 'manual refresh shares initial request');
    await action(() => window.metricsFixture.emit('connected'));
    await action(() => window.metricsFixture.resolve());
    await page.waitForTimeout(180);
    assert.equal(await calls(), 1, 'connected socket stops fallback');
    await action(() => window.metricsFixture.emit('disconnected'));
    await page.waitForFunction(() => window.metricsFixture.state.calls === 2);
    await action(() => window.metricsFixture.hide(true));
    await action(() => window.metricsFixture.resolve());
    await page.waitForTimeout(180);
    assert.equal(await calls(), 2, 'hidden tabs suspend fallback');
    await action(() => window.metricsFixture.hide(false));
    await page.waitForFunction(() => window.metricsFixture.state.calls === 3);
    await action(() => window.metricsFixture.setOptions({ enabled: false, autoRefresh: true }));
    await page.waitForTimeout(30);
    await action(() => window.metricsFixture.resolve());
    await page.waitForTimeout(180);
    assert.equal(await calls(), 3, 'remote selection stops local HTTP work');
    await action(() => window.metricsFixture.setOptions({ enabled: true, autoRefresh: false }));
    await page.waitForFunction(() => window.metricsFixture.state.calls === 4);
    await action(() => window.metricsFixture.resolve());
    await page.waitForTimeout(180);
    assert.equal(await calls(), 4, 'refresh off loads one snapshot without polling');
    await action(() => { window.metricsFixture.metrics.refresh(); window.metricsFixture.metrics.refresh(); });
    await page.waitForFunction(() => window.metricsFixture.state.calls === 5);
    await action(() => window.metricsFixture.resolve());
    await page.waitForTimeout(120);
    assert.equal(await calls(), 5, 'manual refresh while off does not restart timer');
    await page.waitForFunction(() => window.widgetFixture?.state.calls === 1);
    await action(() => window.widgetFixture.resolve());
    await page.waitForFunction(() => window.widgetFixture.state.snapshots.every((item) => !item.loading));
    await action(() => window.widgetFixture.refresh());
    await page.waitForFunction(() => window.widgetFixture.state.calls === 2);
    assert.deepEqual(await action(() => window.widgetFixture.state.snapshots.map((item) => item.data)),
        [{ points: [1] }, { points: [1] }], 'refresh retains both widgets while sharing one next request');
    await action(() => window.widgetFixture.resolve());
    await page.waitForFunction(() => window.widgetFixture.state.snapshots.every((item) => !item.loading));
    await action(() => window.widgetFixture.workspace());
    await page.waitForFunction(() => window.widgetFixture.state.calls === 3);
    assert.deepEqual(await action(() => window.widgetFixture.state.snapshots.map((item) => item.data)),
        [null, null], 'workspace switching cannot display another workspace payload');
    await action(() => window.widgetFixture.resolve());
    await page.waitForFunction(() => window.widgetFixture.state.snapshots.every((item) => !item.loading));
    // Exercise the actual service component: disappearance, rejected polls,
    // runtime switches and a late response from the previous application.
    await page.clock.install();
    await action(() => {
        window.serviceFixture.state.containers = [{ Id: 'a', Names: ['alpha'] }];
        window.serviceFixture.setApp({ id: 1, name: 'alpha', app_type: 'docker' });
    });
    await page.waitForFunction(() => window.serviceFixture.state.pending.length === 1);
    await action(() => window.serviceFixture.setApp({
        id: 1, name: 'alpha', app_type: 'docker', status: 'running',
    }));
    await page.waitForTimeout(50);
    assert.equal(await action(() => window.serviceFixture.state.pending.length), 1,
        'recreated app metadata does not restart an in-flight metrics request');
    await action(() => window.serviceFixture.state.pending.shift().resolve({ cpu_percent: 42 }));
    await page.locator('#service .metrics-tab').waitFor();
    assert.match(await page.locator('#service').innerText(), /42\.0%/);
    await action(() => { window.serviceFixture.state.containers = []; });
    await page.clock.fastForward(10001);
    await page.locator('#service .empty-state__title').waitFor();
    assert.equal(await page.locator('#service .metrics-tab').count(), 0, 'missing container clears previous stats');
    await action(() => { window.serviceFixture.state.containers = [{ Id: 'a', Names: ['alpha'] }]; });
    await page.clock.fastForward(10001);
    await page.waitForFunction(() => window.serviceFixture.state.pending.length === 1);
    await action(() => window.serviceFixture.state.pending.shift().resolve({ cpu_percent: 51 }));
    await page.locator('#service .metrics-tab').waitFor();
    await page.clock.fastForward(10001);
    await page.waitForFunction(() => window.serviceFixture.state.pending.length === 1);
    await action(() => window.serviceFixture.state.pending.shift().reject(new Error('container stopped')));
    await page.locator('#service .empty-state__title').waitFor();
    await page.clock.fastForward(10001);
    await page.waitForFunction(() => window.serviceFixture.state.pending.length === 1);
    await action(() => {
        window.serviceFixture.state.containers = [{ Id: 'b', Names: ['beta'] }];
        window.serviceFixture.setApp({ id: 2, name: 'beta', app_type: 'docker' });
    });
    await page.waitForFunction(() => window.serviceFixture.state.pending.length === 2);
    assert.equal(await page.locator('#service [aria-busy="true"]').count(), 1, 'new app starts loading');
    await action(() => window.serviceFixture.state.pending.pop().resolve({ cpu_percent: 17 }));
    await page.locator('#service .metrics-tab').waitFor();
    await action(() => window.serviceFixture.state.pending.shift().resolve({ cpu_percent: 99 }));
    await page.waitForTimeout(50);
    assert.match(await page.locator('#service').innerText(), /17\.0%/, 'late previous app response is ignored');
    assert.doesNotMatch(await page.locator('#service').innerText(), /99\.0%/);
    await action(() => window.serviceFixture.setApp({ id: 3, name: 'python', app_type: 'flask' }));
    await page.waitForFunction(() => window.serviceFixture.state.python.length === 1);
    assert.equal(await page.locator('#service .metrics-tab').count(), 0, 'Python switch clears Docker state');
    await action(() => window.serviceFixture.state.python.shift()({ active: true, pid: 123 }));
    await page.locator('#service .metrics-tab').waitFor();
    await action(() => window.serviceFixture.setApp({ id: 4, name: 'other-python', app_type: 'flask' }));
    await page.waitForFunction(() => window.serviceFixture.state.python.length === 1);
    assert.equal(await page.locator('#service [aria-busy="true"]').count(), 1, 'Python switch also resets loading');
    assert.doesNotMatch(await page.locator('#service').innerText(), /123/);
    await action(() => window.serviceFixture.state.python.shift()({ active: false }));
    await page.locator('#service .metrics-tab').waitFor();
    assert.deepEqual(errors, []);
    console.log('Service metrics regression passed: stable metadata refresh, missing container, failed poll, app switch, late response and Python state reset.');
    console.log('Metrics browser regression passed: slow response, manual sharing, socket loss/reconnect, hidden tab, remote selection, refresh off; widget sharing, refresh retention, workspace isolation.');
} finally {
    await browser?.close();
    await server.close();
}
