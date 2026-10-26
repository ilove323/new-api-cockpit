/* All datetime-local values represent Shanghai wall time, regardless of browser timezone. */
function presetRange(preset, now = Date.now()) {
  const shanghai = new Date(Math.floor(now / 1000) * 1000 + 8 * 3600000);
  let start, end = shanghai;
  if (preset === 'previous-three-months' || preset === 'month') {
    const months = preset === 'previous-three-months' ? 3 : 1;
    start = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth() - months, 1));
    end = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth(), 1) - 1000);
  } else if (preset === 'current-month') {
    start = new Date(Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth(), 1));
  } else if (preset === 'yesterday') {
    const midnight = Date.UTC(shanghai.getUTCFullYear(), shanghai.getUTCMonth(), shanghai.getUTCDate());
    start = new Date(midnight - 86400000);
    end = new Date(midnight - 1000);
  } else {
    start = new Date(shanghai.getTime() - Number(preset) * 86400000);
  }
  return {start: start.toISOString().slice(0, 19), end: end.toISOString().slice(0, 19)};
}
