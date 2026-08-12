export interface PendingWithdrawalRequest {
  amountMinor: number
  key: string
}

export function withdrawalIdempotencyKey(
  amountMinor: number,
  existing: PendingWithdrawalRequest | null,
): PendingWithdrawalRequest {
  if (existing?.amountMinor === amountMinor) return existing
  return { amountMinor, key: crypto.randomUUID() }
}
