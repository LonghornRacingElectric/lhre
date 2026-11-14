import {
  MAX_ACTIVE_MESSAGES,
  activeMessages,
  validateMessage,
  validateMessageLibrary,
} from './dashMessages';

// Messages arrive over MQTT from the trackside site, so the car keeps what it
// can render and drops the rest instead of crashing the dash.
describe('validateMessage', () => {
  test.each([null, undefined, 'BOX NOW', 7, {}, { text: 5 }])('rejects %p', (raw) => {
    expect(validateMessage(raw)).toBeNull();
  });

  test('fills defaults for a bare text', () => {
    const m = validateMessage({ text: 'BOX NOW' });
    expect(m).toMatchObject({ text: 'BOX NOW', label: 'BOX NOW', icon: 'none', color: 'auto', durationS: 6 });
    expect(m?.id).toMatch(/^msg-/);
    expect(m?.subtext).toBeUndefined();
  });

  test('keeps valid fields and replaces invalid ones', () => {
    expect(
      validateMessage({ id: 'a', label: 'Push', text: 'PUSH', subtext: 'go', icon: 'bolt', color: '#fff', durationS: 0 }),
    ).toEqual({
      id: 'a', label: 'Push', text: 'PUSH', subtext: 'go', icon: 'bolt', color: '#fff',
      colorDark: undefined, colorLight: undefined, durationS: 0,
    });
    const bad = validateMessage({ text: 'X', icon: 'skull', durationS: -1, color: 3, label: '' });
    expect(bad).toMatchObject({ icon: 'none', durationS: 6, color: 'auto', label: 'X' });
  });
});

describe('validateMessageLibrary', () => {
  test('rejects a library without an item list', () => {
    expect(validateMessageLibrary(null)).toBeNull();
    expect(validateMessageLibrary({ activeIds: [] })).toBeNull();
  });

  test('drops bad items and active ids that name nothing', () => {
    const lib = validateMessageLibrary({
      items: [{ id: 'a', text: 'A' }, { id: 'b' }, 'junk', { id: 'c', text: 'C' }],
      activeIds: ['c', 'b', 'a', 42, 'missing'],
    });
    expect(lib?.items.map((m) => m.id)).toEqual(['a', 'c']);
    expect(lib?.activeIds).toEqual(['c', 'a']);
    expect(activeMessages(lib!).map((m) => m.text)).toEqual(['C', 'A']);
  });

  test('caps the active set and keeps its order', () => {
    const items = Array.from({ length: MAX_ACTIVE_MESSAGES + 3 }, (_, i) => ({ id: `m${i}`, text: `T${i}` }));
    const ids = items.map((m) => m.id).reverse();
    const lib = validateMessageLibrary({ items, activeIds: ids });
    expect(lib?.activeIds).toEqual(ids.slice(0, MAX_ACTIVE_MESSAGES));
  });

  test('a library with no active ids is valid and empty on the car', () => {
    const lib = validateMessageLibrary({ items: [{ id: 'a', text: 'A' }] });
    expect(lib?.activeIds).toEqual([]);
    expect(activeMessages(lib!)).toEqual([]);
  });
});
