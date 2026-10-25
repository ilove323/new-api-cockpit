import type { AccountState } from './controller';
import { formatAmount } from './client';

export function statusBarLabel(state: AccountState): {text: string; accessibleLabel: string} {
  const b = state.balance;
  const label = b ? b.user.username + ' · ' + (state.error ? '上次余额 ' : '可用余额 ') + formatAmount(b.remaining_amount) : 'Cockpit 余额';
  // Status bar text supports codicons. An account name must stay literal.
  return {text: label.replace(/\$\(/g, '\\$('), accessibleLabel: label};
}
