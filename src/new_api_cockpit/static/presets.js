/* All datetime-local values represent Shanghai wall time, regardless of browser timezone. */
function presetRange(preset, now = Date.now()) {
  const shanghai = new Date(Math.floor(now / 1000) * 1000 + 8 * 3600000);
  let start, end = shanghai;
  if (preset === 'month') {
    start = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth() - 1, 1));
    end = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth(), 1) - 1000);
  } else if (preset === 'current-month') {
    start = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth(), 1));
  } else {
    start = new Date(shanghai.getTime() - Number(preset) * 86400000);
  }
  return {start: start.toISOString().slice(0, 19), end: end.toISOString().slice(0, 19)};
}
