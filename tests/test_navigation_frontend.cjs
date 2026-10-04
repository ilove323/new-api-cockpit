/* Navigation contracts with a DOM fixture, never a browser. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../src/new_api_statistics/static/navigation.js'), 'utf8');

function navigation({small = false, saved = '0', blockedStorage = false} = {}) {
  const classes = new Set(), writes = [], events = {};
  const classList = {add: k => classes.add(k), remove: k => classes.delete(k), contains: k => classes.has(k), toggle(k) {if (classes.has(k)) classes.delete(k); else classes.add(k);}};
  const element = () => ({events: {}, attrs: {}, children: ['fixture-svg'], hidden: true, focused: false, addEventListener(k, fn) {this.events[k] = fn;}, setAttribute(k, v) {this.attrs[k] = v;}, focus() {this.focused = true;}});
  const button = element(), backdrop = element();
  const media = {matches: small, addEventListener(k, fn) {this.change = fn;}};
  const context = {document: {getElementById: id => id === 'sidebar-toggle' ? button : backdrop, body: {classList}, addEventListener(k, fn) {events[k] = fn;}}, window: {matchMedia: () => media}, localStorage: {getItem() {if (blockedStorage) throw new Error('denied'); return saved;}, setItem(k, v) {if (blockedStorage) throw new Error('denied'); writes.push([k, v]);}}};
  vm.runInNewContext(code, context);
  return {button, backdrop, media, classes, writes, events};
}

test('desktop collapse preserves its SVG, accessible name and saved preference', () => {
  const h = navigation();
  assert.equal(h.button.attrs['aria-expanded'], 'true');
  h.button.events.click();
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  assert.equal(h.button.attrs['aria-label'], '展开导航');
  assert.equal(h.button.title, '展开导航');
  assert.deepEqual(h.button.children, ['fixture-svg']);
  assert.deepEqual(h.writes, [['navigation-collapsed', '1']]);
  h.button.events.click();
  assert.equal(h.button.attrs['aria-expanded'], 'true');
  assert.equal(h.backdrop.hidden, true);
});

test('narrow-screen overlay closes on backdrop or Escape and restores focus', () => {
  const h = navigation({small: true, saved: '1'});
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  h.button.events.click();
  assert.equal(h.backdrop.hidden, false);
  assert.equal(h.button.attrs['aria-expanded'], 'true');
  h.events.keydown({key: 'Escape'});
  assert.equal(h.backdrop.hidden, true);
  assert.equal(h.button.focused, true);
  h.button.events.click();
  h.backdrop.events.click();
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  assert.deepEqual(h.writes, []);
});

test('breakpoint changes clear the overlay without losing the desktop preference', () => {
  const h = navigation({saved: '1'});
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  h.media.matches = true; h.media.change(); h.button.events.click();
  assert.equal(h.backdrop.hidden, false);
  h.media.matches = false; h.media.change();
  assert.equal(h.backdrop.hidden, true);
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  assert.ok(!h.classes.has('sidebar-expanded'));
  h.button.events.click();
  assert.deepEqual(h.writes, [['navigation-collapsed', '0']]);
});

test('blocked local storage never disables navigation or replaces the icon', () => {
  const h = navigation({blockedStorage: true});
  h.button.events.click();
  assert.equal(h.button.attrs['aria-expanded'], 'false');
  assert.deepEqual(h.button.children, ['fixture-svg']);
});
