import { createHash, randomUUID } from 'node:crypto';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';

export const MIN_REFRESH_MS = 60_000;
export interface Reservation { allowed: boolean; nextAllowedAt: number }

function siteKey(endpoint: string): string {
  // Keep the historical on-disk identity so upgrading an old saved connection
  // cannot bypass an active 429 cooldown. This path is never requested.
  return createHash('sha256').update(new URL(endpoint).origin + '/cockpit/mcp').digest('hex');
}

// Only timestamps are persisted. The shared local gate also covers other VS Code
// windows and restarts; neither PATs nor account/balance data enter these files.
export class RefreshGate {
  constructor(private readonly directory: string, private readonly now = Date.now) {}

  private async update(endpoint: string, deferUntil = 0, interval = MIN_REFRESH_MS): Promise<Reservation> {
    await fs.mkdir(this.directory, {recursive: true, mode: 0o700});
    const base = path.join(this.directory, siteKey(endpoint));
    const lock = base + '.lock', file = base + '.json', nonce = randomUUID();
    let handle: fs.FileHandle | undefined;
    for (let attempt = 0; attempt < 25; attempt++) {
      try { handle = await fs.open(lock, 'wx', 0o600); break; }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        const stat = await fs.stat(lock).catch(() => undefined);
        if (stat && this.now() - stat.mtimeMs > 120_000) await fs.unlink(lock).catch(() => undefined);
        // Local lock contention only; this never retries an HTTP request.
        await pause(20);
      }
    }
    if (!handle) throw new Error('Refresh gate busy');
    const temporary = file + '.' + nonce;
    try {
      await handle.writeFile(nonce);
      let nextAllowedAt = 0, backoffUntil = 0;
      try {
        const state = JSON.parse(await fs.readFile(file, 'utf8'));
        if (!Number.isSafeInteger(state.nextAllowedAt) || state.nextAllowedAt < 0) throw new Error('Invalid refresh state');
        nextAllowedAt = state.nextAllowedAt;
        backoffUntil = state.backoffUntil ?? 0;
        if (!Number.isSafeInteger(backoffUntil) || backoffUntil < 0) throw new Error('Invalid backoff state');
      } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
      const allowed = !deferUntil && this.now() >= nextAllowedAt;
      backoffUntil = Math.max(backoffUntil, deferUntil);
      nextAllowedAt = Math.max(nextAllowedAt, backoffUntil, allowed ? this.now() + interval : 0);
      await fs.writeFile(temporary, JSON.stringify({nextAllowedAt, backoffUntil}), {mode: 0o600, flag: 'wx'});
      await fs.rename(temporary, file);
      return {allowed, nextAllowedAt};
    } finally {
      await handle.close();
      await fs.unlink(temporary).catch(() => undefined);
      if (await fs.readFile(lock, 'utf8').catch(() => '') === nonce) await fs.unlink(lock).catch(() => undefined);
    }
  }

  reserve(endpoint: string, interval = MIN_REFRESH_MS): Promise<Reservation> {
    if (!Number.isSafeInteger(interval) || interval < 1000) throw new Error('Invalid refresh interval');
    return this.update(endpoint, 0, interval);
  }
  async backoff(endpoint: string): Promise<number> {
    const file = path.join(this.directory, siteKey(endpoint) + '.json');
    try {
      const state = JSON.parse(await fs.readFile(file, 'utf8')), until = state.backoffUntil ?? 0;
      if (!Number.isSafeInteger(until) || until < 0) throw new Error('Invalid backoff state');
      return until;
    } catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return 0; throw error; }
  }
  async defer(endpoint: string, until: number): Promise<number> {
    if (!Number.isSafeInteger(until) || until < 0) throw new Error('Invalid backoff');
    const result = await this.update(endpoint, until);
    if (result.nextAllowedAt < until) throw new Error('Refresh gate busy');
    return result.nextAllowedAt;
  }
}
