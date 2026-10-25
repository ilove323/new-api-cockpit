import { Balance, BalanceError, Connection, fetchBalance } from './client';
import { RefreshGate } from './gate';

export interface AccountState {
  endpoint?: string;
  balance?: Balance;
  updatedAt?: number;
  error?: string;
  authenticationFailed?: boolean;
  loading: boolean;
  nextAllowedAt: number;
}

export class AccountController {
  state: AccountState = {loading: false, nextAllowedAt: 0};
  private connection?: Connection;
  private generation = 0;
  private abort?: AbortController;
  private pending?: Promise<void>;
  constructor(private readonly gate: RefreshGate, private readonly changed: () => void,
    private readonly query = fetchBalance, private readonly now = Date.now) {}

  setConnection(connection?: Connection): void {
    this.generation++; this.abort?.abort(); this.pending = undefined;
    this.connection = connection;
    this.state = {endpoint: connection?.endpoint, loading: false, nextAllowedAt: 0};
    this.changed();
  }

  refresh(): Promise<void> {
    if (this.pending) return this.pending;
    if (!this.connection || this.now() < this.state.nextAllowedAt) { this.changed(); return Promise.resolve(); }
    const connection = this.connection, generation = this.generation, abort = new AbortController();
    this.abort = abort; this.state.loading = true; this.changed();
    const pending = (async () => {
      try {
        const reservation = await this.gate.reserve(connection.endpoint);
        if (generation !== this.generation) return;
        this.state.nextAllowedAt = reservation.nextAllowedAt;
        if (!reservation.allowed) return;
        const balance = await this.query(connection, abort.signal);
        if (generation !== this.generation) return;
        this.state.balance = balance; this.state.updatedAt = this.now(); this.state.error = undefined; this.state.authenticationFailed = false;
      } catch (error) {
        const retryAt = error instanceof BalanceError && error.status === 429 ? Math.max(error.retryAt, this.now() + 15 * 60_000) : 0;
        let backoffSaved = true;
        if (retryAt) {
          // The endpoint's rate limit remains relevant even if this account was
          // replaced while the response was in flight. Never publish its data.
          try { await this.gate.defer(connection.endpoint, retryAt); }
          catch { backoffSaved = false; }
        }
        if (generation !== this.generation) return;
        this.state.error = error instanceof BalanceError ? error.message : '无法读取刷新记录或查询余额，请稍后重试。';
        if (error instanceof BalanceError && [401, 403].includes(error.status)) {
          this.state.balance = undefined; this.state.updatedAt = undefined;
          this.state.authenticationFailed = true;
        }
        if (error instanceof BalanceError && error.status === 429) {
          this.state.nextAllowedAt = Math.max(this.state.nextAllowedAt, retryAt);
          if (!backoffSaved) this.state.error = '请求过于频繁，已暂停刷新；等待时间暂未写入本机刷新记录。';
        } else if (!(error instanceof BalanceError)) {
          this.state.nextAllowedAt = Math.max(this.state.nextAllowedAt, this.now() + 60_000);
        }
      } finally {
        if (generation === this.generation) { this.state.loading = false; this.changed(); }
      }
    })();
    this.pending = pending;
    void pending.finally(() => { if (this.pending === pending) this.pending = undefined; });
    return pending;
  }

  dispose(): void { this.generation++; this.abort?.abort(); this.connection = undefined; }
}
