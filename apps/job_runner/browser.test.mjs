import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('./static/app.js', import.meta.url), 'utf8');
const id = 'a'.repeat(32), other = 'b'.repeat(32);
const job = (jobId, status) => ({id: jobId, status, settings: {template: 'cpu', parallel: 1}, created: '2026-11-14T20:00:00Z', exit_code: null});
async function browser() {
  const nodes = new Map(), calls = [], intervals = [];
  const document = {activeElement: null};
  class Element {
    constructor(tag) {this.tagName = tag; this.children = []; this.handlers = {}; this.dataset = {}; this.value = ''; this.disabled = false; this.hidden = false; this.textContent = ''; this.classList = {toggle() {}};}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {if (this.children.some(row => row.children.includes(document.activeElement))) document.activeElement = null; this.children = children;}
    addEventListener(type, callback) {this.handlers[type] = callback;}
    async emit(type) {await this.handlers[type]?.({preventDefault() {}});}
    focus() {document.activeElement = this;}
    scrollIntoView() {}
  }
  document.getElementById = key => {if (!nodes.has(key)) nodes.set(key, new Element('div')); return nodes.get(key);};
  document.createElement = tag => new Element(tag);
  const filters = ['all', 'active', 'finished'].map(filter => {const button = new Element('button'); button.dataset.filter = filter; return button;});
  document.querySelectorAll = () => filters;
  const get = document.getElementById;
  get('template').value = 'cpu'; get('parallel').value = '1'; get('seconds').value = '15';
  let offline = false;
  const context = vm.createContext({document, console, crypto: {randomUUID: () => id}, Date: class extends Date {static now() {return 1794686405000;}},
    setInterval: (callback, delay) => intervals.push({callback, delay}),
    fetch: async (url, options) => {
      calls.push({url, options});
      if (offline && url === '/api/state') throw new Error('connection lost');
      const value = url === '/api/state' ? {connection: 'connected', online: true, jobs: [job(id, 'running'), job(other, 'succeeded')], templates: {cpu: true, fmu: true, gpu: true}} : url.endsWith('/logs') ? {stdout: 'fixture output', stderr: ''} : {id};
      return {ok: true, json: async () => value};
    }});
  vm.runInContext(source, context);
  const flush = async () => {for (let step = 0; step < 20; step++) await Promise.resolve();};
  await flush();
  return {get, calls, filters, intervals, document, flush, offline: value => {offline = value;}, refresh: () => vm.runInContext('refresh()', context)};
}

test('browser initializes CPU launch, submits settings and polls every three seconds', async () => {
  const b = await browser();
  assert.equal(b.get('submit').disabled, false);
  assert.equal(b.get('seconds').disabled, false);
  assert.equal(b.intervals[0].delay, 3000);
  await b.get('launch').emit('submit');
  const submit = b.calls.find(call => call.url === '/api/runs');
  assert.deepEqual(JSON.parse(submit.options.body), {id, template: 'cpu', parallel: 1, seconds: 15});
  const previous = b.calls.filter(call => call.url === '/api/state').length;
  await b.intervals[0].callback();
  assert.equal(b.calls.filter(call => call.url === '/api/state').length, previous + 1);
});

test('failed refresh stays offline after workload changes and launch finally; success recovers', async () => {
  const b = await browser();
  await b.get('runs').children[0].children[0].emit('click'); await b.flush();
  b.offline(true);
  await b.refresh();
  b.get('template').value = 'fmu'; await b.get('template').emit('change');
  assert.equal(b.get('submit').disabled, true);
  assert.equal(b.get('pause').disabled, true);
  assert.equal(b.get('cancel').disabled, true);
  await b.get('launch').emit('submit');
  assert.equal(b.get('submit').disabled, true);
  b.offline(false); await b.refresh();
  assert.equal(b.get('submit').disabled, false);
});

test('refresh keeps keyboard focus on the same run and filters run history', async () => {
  const b = await browser();
  b.get('runs').children[0].children[0].focus();
  await b.refresh();
  assert.equal(b.document.activeElement.dataset.runId, id);
  await b.filters[2].emit('click');
  assert.equal(b.get('runs').children.length, 1);
  assert.equal(b.get('runs').children[0].children[0].dataset.runId, other);
  await b.filters[1].emit('click');
  assert.equal(b.get('runs').children[0].children[0].dataset.runId, id);
});

test('switching a cleared CPU duration to FMU or GPU disables validation and submits a valid default', async () => {
  for (const template of ['fmu', 'gpu']) {
    const b = await browser();
    b.get('seconds').value = '';
    b.get('template').value = template; await b.get('template').emit('change');
    assert.equal(b.get('duration').hidden, true);
    assert.equal(b.get('seconds').disabled, true);
    assert.equal(Number(b.get('seconds').value), 15);
    assert.equal(b.get('parallel').disabled, template === 'gpu');
    await b.get('launch').emit('submit');
    const settings = JSON.parse(b.calls.find(call => call.url === '/api/runs').options.body);
    assert.equal(settings.template, template);
    assert.ok(settings.seconds >= 5 && settings.seconds <= 120);
    b.get('template').value = 'cpu'; await b.get('template').emit('change');
    assert.equal(b.get('seconds').disabled, false);
    assert.equal(b.get('duration').hidden, false);
  }
});
