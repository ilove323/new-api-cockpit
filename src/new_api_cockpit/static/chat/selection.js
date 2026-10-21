/* Per-account UI preferences only: never credentials, catalogs or chat data. */
(function (root, factory) {
  'use strict';
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat ||= {}, value);
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const emptySelection = () => ({v: 1, token_id: null, model: '', mode: 'chat'});
  function selectionStorageKey(ownerUserId) {
    return Number.isSafeInteger(ownerUserId) && ownerUserId > 0 && ownerUserId <= 2147483647 ? `cockpit.chat.selection.v1.u${ownerUserId}` : null;
  }
  function normalizeSelection(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 4
      || Object.keys(value).some(key => !['v', 'token_id', 'model', 'mode'].includes(key)) || value.v !== 1
      || value.token_id !== null && (!Number.isInteger(value.token_id) || value.token_id < 1 || value.token_id > 2147483647)
      || typeof value.model !== 'string' || new TextEncoder().encode(value.model).length > 256 || /[\u0000-\u001f\u007f]/.test(value.model)
      || value.token_id === null && value.model !== '' || !['chat', 'files'].includes(value.mode)) return null;
    return {v: 1, token_id: value.token_id, model: value.model, mode: value.mode};
  }
  function createSelectionCache({ownerUserId, storage}) {
    const key = selectionStorageKey(ownerUserId);
    return {
      key,
      read() {
        if (!key) return emptySelection();
        try {
          const target = storage(), raw = target?.getItem(key);
          if (raw === null || raw === undefined) return emptySelection();
          const value = raw.length <= 2048 ? normalizeSelection(JSON.parse(raw)) : null;
          if (value) return value;
          target.removeItem(key);
        } catch { /* Disabled, unavailable or corrupt storage cannot block chat. */ }
        return emptySelection();
      },
      write(value) {
        const normalized = normalizeSelection(value);
        if (!key || !normalized) return false;
        try { const target = storage(); if (!target) return false; target.setItem(key, JSON.stringify(normalized)); return true; }
        catch { return false; }
      },
    };
  }
  return {emptySelection, selectionStorageKey, normalizeSelection, createSelectionCache};
});
