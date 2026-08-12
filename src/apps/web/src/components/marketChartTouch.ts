export const CHART_TOUCH_LONG_PRESS_MS = 420
export const CHART_TOUCH_MOVE_THRESHOLD_PX = 10

export interface ChartTouchPoint {
  x: number
  y: number
}

export interface ChartTouchSession {
  pointerId: number
  startedAt: number
  start: ChartTouchPoint
  latest: ChartTouchPoint
  moved: boolean
  observing: boolean
}

export function createChartTouchSession(
  pointerId: number,
  point: ChartTouchPoint,
  startedAt: number,
): ChartTouchSession {
  return { pointerId, startedAt, start: point, latest: point, moved: false, observing: false }
}

export function updateChartTouchSession(
  session: ChartTouchSession,
  point: ChartTouchPoint,
  threshold = CHART_TOUCH_MOVE_THRESHOLD_PX,
): ChartTouchSession {
  const distance = Math.hypot(point.x - session.start.x, point.y - session.start.y)
  return { ...session, latest: point, moved: session.moved || distance > threshold }
}

export function chartTouchReleaseAction(session: ChartTouchSession): 'observe' | 'release' | 'ignore' {
  if (session.observing) return 'release'
  return session.moved ? 'ignore' : 'observe'
}
