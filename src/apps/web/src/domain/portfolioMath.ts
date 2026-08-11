export function positionReturnPct(
  quantity: number,
  averagePrice: number,
  multiplier: number,
  unrealizedPnl: number,
) {
  const basis = Math.abs(quantity * averagePrice * multiplier)
  return basis ? unrealizedPnl / basis * 100 : 0
}
