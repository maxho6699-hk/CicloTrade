import {
  ChevronDown,
  Eye,
  EyeOff,
  Magnet,
  Minus,
  MousePointer2,
  MoveDiagonal2,
  Pentagon,
  Redo2,
  Repeat2,
  Ruler,
  Square,
  Trash2,
  Triangle,
  Undo2,
  Waves,
  X,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import type { Candle } from '../types'
import { BrowserApiError, getChartDrawings, syncChartDrawings } from '../api/client'
import {
  decideConflictRefresh,
  drawingOperations,
  drawingOperationKey,
  drawingScopeToken,
  hasLegacyChartDrawings,
  isCurrentDrawingScope,
  isValidChartDrawing,
  mergeDrawingTombstoneRevisions,
  readCachedDrawings,
  translateDrawingScreenPoints,
  writeCachedDrawings,
  type ChartDrawing as Drawing,
  type ChartDrawingPoint as DrawingPoint,
  type DrawingPersistence,
  type DrawingScope,
  type DrawingTombstone,
} from '../data/chartDrawings'

type ToolFamily = 'line' | 'shape' | 'pattern' | 'fibonacci' | 'measure' | 'label'
type ScreenPoint = { x: number; y: number }
interface DrawingDragState {
  drawingId: string
  pointerId: number
  start: ScreenPoint
  originalScreenPoints: ScreenPoint[]
  previewPoints: DrawingPoint[]
}

interface ToolDefinition {
  id: string
  label: string
  family: ToolFamily
  points: number
}


export interface ChartCoordinateApi {
  coordinateToPoint: (x: number, y: number) => DrawingPoint | null
  pointToCoordinate: (point: DrawingPoint) => ScreenPoint | null
}

export interface ChartPlotBounds {
  left: number
  top: number
  width: number
  height: number
}

export type DrawingMagnet = 'off' | 'weak' | 'strong'

export interface DrawingToolState {
  tool: string
  continuous: boolean
  magnet: DrawingMagnet
  visible: boolean
  crossTimeframe: boolean
}

export interface DrawingCommand {
  id: number
  type: 'undo' | 'redo' | 'clear'
  targetMarkerId?: string
}

export interface DrawingHistoryStatus {
  drawingCount: number
  undoCount: number
  redoCount: number
  persistence: DrawingPersistence
  legacyDetected?: boolean
}

const TOOL_GROUPS: Array<{ label: string; tools: ToolDefinition[] }> = [
  {
    label: '趋势线', tools: [
      ['segment', '线段', 'line', 2], ['horizontal', '水平直线', 'line', 1],
      ['horizontal-segment', '水平线段', 'line', 2], ['vertical', '垂直线', 'line', 1],
      ['ray', '射线', 'line', 2], ['straight', '直线', 'line', 2],
      ['parallel', '平行线', 'line', 3], ['channel', '通道线', 'line', 3],
      ['periodic', '等周期线', 'line', 2], ['info-line', '信息线', 'line', 2],
      ['smooth-top', '平滑顶/底', 'line', 3], ['cross', '十字线', 'line', 1],
    ].map(([id, label, family, points]) => ({ id: String(id), label: String(label), family: family as ToolFamily, points: Number(points) })),
  },
  {
    label: '几何形状', tools: [
      ['rectangle', '矩形', 'shape', 2], ['triangle', '三角形', 'shape', 3],
      ['parallelogram', '平行四边形', 'shape', 3], ['circle', '圆', 'shape', 2],
      ['ellipse', '椭圆', 'shape', 2], ['path', '路径', 'shape', 5],
    ].map(([id, label, family, points]) => ({ id: String(id), label: String(label), family: family as ToolFamily, points: Number(points) })),
  },
  {
    label: '形态线', tools: [
      ['wave3', '三浪', 'pattern', 4], ['wave5', '五浪', 'pattern', 6],
      ['wave8', '八浪', 'pattern', 9], ['head-shoulders', '头肩顶/头肩底', 'pattern', 5],
      ['triangle-pattern', '三角形态', 'pattern', 5], ['mw', 'M头/W底', 'pattern', 5],
      ['abcd', 'ABCD形态', 'pattern', 4], ['xabcd', 'XABCD形态', 'pattern', 5],
      ['three-drive', '三驱形态', 'pattern', 7], ['sine', '正弦线', 'pattern', 6],
    ].map(([id, label, family, points]) => ({ id: String(id), label: String(label), family: family as ToolFamily, points: Number(points) })),
  },
  {
    label: '江恩与斐波那契', tools: [
      ['fib-retracement', '斐波那契回撤', 'fibonacci', 2], ['fib-time', '斐波那契周期线', 'fibonacci', 2],
      ['fib-extension', '斐波那契趋势扩展', 'fibonacci', 3], ['speed-resistance', '速阻线', 'fibonacci', 2],
      ['gann-box', '江恩箱', 'fibonacci', 2], ['gann-angle', '江恩角度线', 'fibonacci', 2],
      ['grid-line', '栅形线', 'fibonacci', 2], ['pitchfork', '分叉线', 'fibonacci', 3],
      ['schiff', '希夫分叉线', 'fibonacci', 3], ['modified-schiff', '调整希夫分叉线', 'fibonacci', 3],
      ['inside-pitchfork', '内部分叉线', 'fibonacci', 3], ['fan', '倾斜扇形', 'fibonacci', 2],
    ].map(([id, label, family, points]) => ({ id: String(id), label: String(label), family: family as ToolFamily, points: Number(points) })),
  },
  {
    label: '测量与标注', tools: [
      ['time-ruler', '时间尺', 'measure', 2], ['space-ruler', '空间尺', 'measure', 2],
      ['time-space-ruler', '时空尺', 'measure', 2], ['long-position', '多头测量', 'measure', 2],
      ['short-position', '空头测量', 'measure', 2], ['price-label', '价格标注', 'label', 1],
      ['arrow', '箭头', 'label', 2], ['up-arrow', '上涨箭头', 'label', 2],
      ['down-arrow', '下跌箭头', 'label', 2],
    ].map(([id, label, family, points]) => ({ id: String(id), label: String(label), family: family as ToolFamily, points: Number(points) })),
  },
]

const TOOLS = TOOL_GROUPS.flatMap((group) => group.tools)
const QUICK_TOOLS = ['segment', 'horizontal', 'rectangle', 'triangle', 'wave3', 'fib-retracement', 'time-space-ruler']

function validDrawingList(value: unknown): Drawing[] {
  return Array.isArray(value) ? value.filter(isValidChartDrawing).slice(0, 200) : []
}

function iconFor(tool: ToolDefinition) {
  if (tool.id === 'horizontal') return <Minus size={17} />
  if (tool.family === 'shape') return tool.id === 'triangle' ? <Triangle size={17} /> : <Square size={17} />
  if (tool.family === 'pattern') return <Waves size={17} />
  if (tool.family === 'fibonacci') return <Pentagon size={17} />
  if (tool.family === 'measure') return <Ruler size={17} />
  return <MoveDiagonal2 size={17} />
}

function DrawingShape({ drawing, width, height, coordinateApi, markerId }: {
  drawing: Drawing
  width: number
  height: number
  coordinateApi: ChartCoordinateApi
  markerId: string
}) {
  const tool = TOOLS.find((item) => item.id === drawing.tool)
  if (!Array.isArray(drawing.points)) return null
  const points = drawing.points.map(coordinateApi.pointToCoordinate)
  if (!tool || !points.length || points.some((point) => point === null)) return null
  const visiblePoints = points as ScreenPoint[]
  const [first, second = first, third = second] = visiblePoints
  const color = tool.family === 'measure' ? '#e0a33c' : tool.family === 'fibonacci' ? '#7d8df6' : '#20a67a'
  const common = { stroke: color, strokeWidth: 1.6, vectorEffect: 'non-scaling-stroke' as const, fill: 'none' }
  if (tool.id === 'horizontal') return <line {...common} x1={0} y1={first.y} x2={width} y2={first.y} />
  if (tool.id === 'vertical') return <line {...common} x1={first.x} y1={0} x2={first.x} y2={height} />
  if (tool.id === 'cross') return <g><line {...common} x1={0} y1={first.y} x2={width} y2={first.y} /><line {...common} x1={first.x} y1={0} x2={first.x} y2={height} /></g>
  if (tool.id === 'rectangle' || tool.id === 'gann-box') return <rect {...common} x={Math.min(first.x, second.x)} y={Math.min(first.y, second.y)} width={Math.abs(second.x - first.x)} height={Math.abs(second.y - first.y)} fill={`${color}16`} />
  if (tool.id === 'circle' || tool.id === 'ellipse') return <ellipse {...common} cx={(first.x + second.x) / 2} cy={(first.y + second.y) / 2} rx={Math.abs(second.x - first.x) / 2} ry={tool.id === 'circle' ? Math.abs(second.x - first.x) / 2 : Math.abs(second.y - first.y) / 2} />
  if (tool.id === 'triangle') return <polygon {...common} points={visiblePoints.slice(0, 3).map((point) => `${point.x},${point.y}`).join(' ')} fill={`${color}12`} />
  if (tool.id === 'parallelogram') {
    const fourth = { x: first.x + third.x - second.x, y: first.y + third.y - second.y }
    return <polygon {...common} points={[first, second, third, fourth].map((point) => `${point.x},${point.y}`).join(' ')} fill={`${color}12`} />
  }
  if (tool.family === 'fibonacci') {
    if (tool.id === 'fib-time') {
      const distance = Math.max(8, Math.abs(second.x - first.x))
      return <g>{[0, 1, 2, 3, 5, 8].map((ratio) => <line key={ratio} {...common} opacity={0.65} x1={first.x + distance * ratio} y1={0} x2={first.x + distance * ratio} y2={height} />)}</g>
    }
    if (tool.id.includes('pitchfork')) {
      const slope = (second.y - first.y) / Math.max(1, second.x - first.x)
      return <g><line {...common} x1={first.x} y1={first.y} x2={width} y2={first.y + (width - first.x) * slope} /><line {...common} opacity={0.68} x1={third.x} y1={third.y} x2={width} y2={third.y + (width - third.x) * slope} /></g>
    }
    if (tool.id === 'fan' || tool.id === 'gann-angle' || tool.id === 'speed-resistance') {
      return <g>{[0.25, 0.5, 0.75, 1].map((ratio) => <line key={ratio} {...common} opacity={0.72} x1={first.x} y1={first.y} x2={second.x} y2={first.y + (second.y - first.y) * ratio} />)}</g>
    }
    const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
    return <g>{levels.map((level) => { const y = first.y + (second.y - first.y) * level; return <g key={level}><line {...common} opacity={level === 0 || level === 1 ? 1 : 0.65} x1={Math.min(first.x, second.x)} y1={y} x2={Math.max(first.x, second.x)} y2={y} /><text x={Math.max(first.x, second.x) + 4} y={y - 2} fill={color} fontSize="10">{level}</text></g> })}</g>
  }
  if (tool.family === 'measure') {
    const startPrice = drawing.points[0].price
    const endPrice = drawing.points[1]?.price ?? startPrice
    const change = startPrice ? (endPrice - startPrice) / startPrice * 100 : 0
    return <g><line {...common} x1={first.x} y1={first.y} x2={second.x} y2={second.y} /><rect x={Math.min(first.x, second.x)} y={Math.min(first.y, second.y)} width={Math.abs(second.x - first.x)} height={Math.abs(second.y - first.y)} fill={change >= 0 ? '#20a67a18' : '#d9576318'} stroke="none" /><text x={second.x + 6} y={second.y - 6} fill={change >= 0 ? '#20a67a' : '#d95763'} fontSize="11">{change >= 0 ? '+' : ''}{change.toFixed(2)}%</text></g>
  }
  if (tool.id === 'price-label') return <g><circle cx={first.x} cy={first.y} r={3} fill={color} /><text x={first.x + 7} y={first.y - 6} fill={color} fontSize="11">{drawing.points[0].price.toFixed(2)}</text></g>
  if (tool.id === 'parallel' || tool.id === 'channel') {
    const offsetY = third.y - second.y
    return <g><line {...common} x1={first.x} y1={first.y} x2={second.x} y2={second.y} /><line {...common} x1={first.x} y1={first.y + offsetY} x2={second.x} y2={second.y + offsetY} /></g>
  }
  if (tool.id === 'ray') {
    const scale = (width - first.x) / Math.max(second.x - first.x, 1)
    return <line {...common} x1={first.x} y1={first.y} x2={width} y2={first.y + (second.y - first.y) * scale} />
  }
  if (tool.id === 'straight') {
    const dx = second.x - first.x || 1
    const leftY = first.y - first.x * (second.y - first.y) / dx
    const rightY = first.y + (width - first.x) * (second.y - first.y) / dx
    return <line {...common} x1={0} y1={leftY} x2={width} y2={rightY} />
  }
  const rendered = tool.id === 'horizontal-segment' ? [first, { x: second.x, y: first.y }] : visiblePoints
  return <polyline {...common} points={rendered.map((point) => `${point.x},${point.y}`).join(' ')} markerEnd={tool.id.includes('arrow') || tool.id === 'arrow' ? `url(#${markerId})` : undefined} />
}

function DrawingHitTarget({ drawing, width, height, coordinateApi, onPointerDown }: {
  drawing: Drawing
  width: number
  height: number
  coordinateApi: ChartCoordinateApi
  onPointerDown: (event: ReactPointerEvent<SVGElement>) => void
}) {
  const tool = TOOLS.find((item) => item.id === drawing.tool)
  const points = drawing.points.map(coordinateApi.pointToCoordinate)
  if (!tool || !points.length || points.some((point) => point === null)) return null
  const visiblePoints = points as ScreenPoint[]
  const [first, second = first] = visiblePoints
  const lineProps = { className: 'drawing-hit-target', stroke: 'transparent', strokeWidth: 16, fill: 'none', vectorEffect: 'non-scaling-stroke' as const, onPointerDown }
  if (tool.id === 'horizontal') return <line {...lineProps} x1={0} y1={first.y} x2={width} y2={first.y} />
  if (tool.id === 'vertical') return <line {...lineProps} x1={first.x} y1={0} x2={first.x} y2={height} />
  if (tool.id === 'cross') return <g><line {...lineProps} x1={0} y1={first.y} x2={width} y2={first.y} /><line {...lineProps} x1={first.x} y1={0} x2={first.x} y2={height} /></g>
  if (tool.family === 'shape' || tool.family === 'fibonacci' || tool.family === 'measure') {
    const x = Math.min(...visiblePoints.map((point) => point.x))
    const y = Math.min(...visiblePoints.map((point) => point.y))
    const targetWidth = Math.max(Math.max(...visiblePoints.map((point) => point.x)) - x, 18)
    const targetHeight = Math.max(Math.max(...visiblePoints.map((point) => point.y)) - y, 18)
    return <rect className="drawing-hit-target drawing-hit-area" x={x - 9} y={y - 9} width={targetWidth + 18} height={targetHeight + 18} fill="transparent" stroke="transparent" strokeWidth={1} onPointerDown={onPointerDown} />
  }
  if (visiblePoints.length === 1) return <circle className="drawing-hit-target drawing-hit-area" cx={first.x} cy={first.y} r={12} fill="transparent" onPointerDown={onPointerDown} />
  const rendered = tool.id === 'horizontal-segment' ? [first, { x: second.x, y: first.y }] : visiblePoints
  return <polyline {...lineProps} points={rendered.map((point) => `${point.x},${point.y}`).join(' ')} />
}

export function SharedDrawingToolbar({ state, history, onChange, onCommand }: {
  state: DrawingToolState
  history: DrawingHistoryStatus
  onChange: (patch: Partial<DrawingToolState>) => void
  onCommand: (type: DrawingCommand['type']) => void
}) {
  const horizontalDockQuery = '(max-width: 760px), (max-width: 980px) and (max-height: 560px) and (orientation: landscape)'
  const [toolboxOpen, setToolboxOpen] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const dockRef = useRef<HTMLDivElement>(null)
  const dockScrollRef = useRef<HTMLDivElement>(null)
  const active = TOOLS.find((item) => item.id === state.tool)

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!dockRef.current?.contains(event.target as Node)) {
        setToolboxOpen(false)
        setConfirmClear(false)
      }
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])

  const chooseTool = (tool: string) => {
    onChange({ tool })
    setToolboxOpen(false)
  }

  const scrollHorizontalDock = (event: ReactWheelEvent<HTMLDivElement>) => {
    if (!window.matchMedia(horizontalDockQuery).matches) return
    const dock = dockScrollRef.current
    if (!dock || dock.scrollWidth <= dock.clientWidth) return
    const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX
    if (!delta) return
    const next = Math.max(0, Math.min(dock.scrollWidth - dock.clientWidth, dock.scrollLeft + delta))
    if (next === dock.scrollLeft) return
    event.preventDefault()
    dock.scrollLeft = next
  }

  return (
    <div className="drawing-tools-root" ref={dockRef}>
      <div className="drawing-tool-dock" ref={dockScrollRef} aria-label="共用 K 线画线工具" onWheel={scrollHorizontalDock}>
        <button className={state.tool === 'cursor' ? 'active' : ''} type="button" title="选择与移动图表" aria-label="选择与移动图表" onClick={() => chooseTool('cursor')}><MousePointer2 size={17} /></button>
        {QUICK_TOOLS.map((id) => { const item = TOOLS.find((candidate) => candidate.id === id)!; return <button className={state.tool === id ? 'active' : ''} type="button" title={item.label} aria-label={item.label} onClick={() => chooseTool(id)} key={id}>{iconFor(item)}</button> })}
        <button className={toolboxOpen ? 'active' : ''} type="button" title="全部画线工具" aria-label="全部画线工具" aria-expanded={toolboxOpen} onClick={() => setToolboxOpen(!toolboxOpen)}><ChevronDown size={17} /></button>
        <span />
        <button className={state.continuous ? 'active' : ''} type="button" title="连续画线" aria-label="连续画线" aria-pressed={state.continuous} onClick={() => onChange({ continuous: !state.continuous })}><Repeat2 size={17} /></button>
        <button className={state.magnet !== 'off' ? 'active' : ''} type="button" title={`磁吸：${state.magnet === 'off' ? '关闭' : state.magnet === 'weak' ? '弱' : '强'}`} aria-label="切换磁吸强度" onClick={() => onChange({ magnet: state.magnet === 'off' ? 'weak' : state.magnet === 'weak' ? 'strong' : 'off' })}><Magnet size={17} /><small>{state.magnet === 'strong' ? '强' : state.magnet === 'weak' ? '弱' : ''}</small></button>
        <button className={state.crossTimeframe ? 'active' : ''} type="button" title="跨周期显示" aria-label="跨周期显示" aria-pressed={state.crossTimeframe} onClick={() => onChange({ crossTimeframe: !state.crossTimeframe })}><Waves size={17} /></button>
        <button type="button" title={state.visible ? '隐藏全部画线' : '显示全部画线'} aria-label={state.visible ? '隐藏全部画线' : '显示全部画线'} onClick={() => onChange({ visible: !state.visible })}>{state.visible ? <Eye size={17} /> : <EyeOff size={17} />}</button>
        <button type="button" title="撤销上一项" aria-label="撤销上一项" disabled={!history.undoCount} onClick={() => onCommand('undo')}><Undo2 size={17} /></button>
        <button type="button" title="重做" aria-label="重做" disabled={!history.redoCount} onClick={() => onCommand('redo')}><Redo2 size={17} /></button>
        <button className="danger" type="button" title="删除全部画线" aria-label="删除全部画线" disabled={!history.drawingCount} onClick={() => setConfirmClear(true)}><Trash2 size={17} /></button>
        <small className={`drawing-save-state ${history.persistence}`}>{history.persistence === 'synced' ? '已同步到账号' : history.persistence === 'syncing' ? '正在同步账号…' : history.persistence === 'device-only' ? '仅此设备（离线）' : history.persistence === 'conflict' ? '同步冲突，请刷新后处理' : '保存失败'}</small>
        {history.legacyDetected && <small className="drawing-save-state device-only">发现旧本机画线待确认</small>}
      </div>

      {toolboxOpen && (
        <section className="drawing-toolbox" aria-label="全部画线工具">
          <header><span><strong>画线工具</strong><small>已选：{active?.label ?? '选择/移动'}</small></span><button className="icon-button" type="button" aria-label="关闭画线工具" onClick={() => setToolboxOpen(false)}><X size={16} /></button></header>
          <div>{TOOL_GROUPS.map((group) => <section key={group.label}><h3>{group.label}</h3><div>{group.tools.map((item) => <button className={state.tool === item.id ? 'active' : ''} type="button" onClick={() => chooseTool(item.id)} key={item.id}>{item.label}</button>)}</div></section>)}</div>
        </section>
      )}

      {confirmClear && (
        <div className="drawing-clear-confirm" role="alertdialog" aria-label="确认删除全部画线">
          <strong>删除当前股票的全部画线？</strong>
          <span>删除后仍可立即撤销。</span>
          <div><button type="button" onClick={() => setConfirmClear(false)}>取消</button><button className="danger" type="button" onClick={() => { onCommand('clear'); setConfirmClear(false) }}>删除全部</button></div>
        </div>
      )}
    </div>
  )
}

export function ChartDrawingLayer({
  active,
  userId,
  candles,
  market,
  symbol,
  timeframe,
  coordinateApi,
  coordinateVersion,
  plotBounds,
  toolState,
  command,
  markerId,
  onHistoryChange,
  onToolComplete,
  onWheelZoom,
}: {
  active: boolean
  userId: number | null | undefined
  candles: Candle[]
  market: 'US' | 'CN'
  symbol: string
  timeframe: string
  coordinateApi: ChartCoordinateApi | null
  coordinateVersion: number
  plotBounds: ChartPlotBounds
  toolState: DrawingToolState
  command: DrawingCommand
  markerId: string
  onHistoryChange: (status: DrawingHistoryStatus) => void
  onToolComplete: () => void
  onWheelZoom: (deltaY: number) => void
}) {
  const scope = useMemo<DrawingScope | null>(() => Number.isInteger(userId) && (userId ?? 0) > 0 ? {
    userId: userId!, market, symbol, timeframe, crossTimeframe: toolState.crossTimeframe,
  } : null, [market, symbol, timeframe, toolState.crossTimeframe, userId])
  const scopeToken = scope ? drawingScopeToken(scope) : ''
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [undoStack, setUndoStack] = useState<Drawing[][]>([])
  const [redoStack, setRedoStack] = useState<Drawing[][]>([])
  const [persistence, setPersistence] = useState<DrawingPersistence>('syncing')
  const [legacyDetected, setLegacyDetected] = useState(false)
  const [draft, setDraft] = useState<DrawingPoint[]>([])
  const [preview, setPreview] = useState<DrawingPoint | null>(null)
  const [selectedDrawingId, setSelectedDrawingId] = useState<string | null>(null)
  const [movingDrawing, setMovingDrawing] = useState(false)
  const movingDrawingRef = useRef<DrawingDragState | null>(null)
  const handledCommand = useRef(0)
  const remoteDrawings = useRef<Drawing[]>([])
  const tombstones = useRef(new Map<string, DrawingTombstone>())
  const currentScopeToken = useRef(scopeToken)
  const latestDrawings = useRef<Drawing[]>([])
  const syncing = useRef(false)
  const syncRequested = useRef(false)
  const scopeLoaded = useRef(false)
  const editGeneration = useRef(0)
  const requestGeneration = useRef(0)
  const lastConflictRetryKey = useRef<string | null>(null)
  const activeTool = TOOLS.find((item) => item.id === toolState.tool)

  const runSync = useCallback(() => {
    if (!scope || !scopeLoaded.current || currentScopeToken.current !== scopeToken) return
    if (syncing.current) { syncRequested.current = true; return }
    const sync = async () => {
      syncing.current = true
      syncRequested.current = false
      while (currentScopeToken.current === scopeToken) {
        const target = latestDrawings.current
        let operations
        try { operations = drawingOperations(remoteDrawings.current, target, scope, [...tombstones.current.values()]) } catch { setPersistence('failed'); break }
        if (!operations.length) {
          latestDrawings.current = remoteDrawings.current
          setDrawings(remoteDrawings.current)
          try { writeCachedDrawings(localStorage, scope, remoteDrawings.current) } catch { setPersistence('failed'); break }
          setPersistence('synced')
          break
        }
        setPersistence('syncing')
        try {
          const batch = operations.slice(0, 100)
          const response = await syncChartDrawings(scope, batch)
          if (currentScopeToken.current !== scopeToken) break
          if (!Array.isArray(response.items)) { setPersistence('failed'); break }
          lastConflictRetryKey.current = null
          const results = new Map(response.items.map((item) => [drawingOperationKey(item), item]))
          const remoteByKey = new Map(remoteDrawings.current.map((drawing) => {
            const origin_timeframe = drawing.origin_timeframe ?? scope.timeframe
            const cross_timeframe = drawing.cross_timeframe ?? scope.crossTimeframe
            return [drawingOperationKey({ drawing, origin_timeframe, cross_timeframe }), drawing] as const
          }))
          for (const operation of batch) {
            const key = drawingOperationKey(operation)
            const result = results.get(key)
            if (!result) continue
            if (result.deleted) {
              const deletedDrawing = remoteByKey.get(key)
              if (deletedDrawing) tombstones.current.set(key, {
                drawing: deletedDrawing,
                revision: result.revision,
                origin_timeframe: result.origin_timeframe,
                cross_timeframe: result.cross_timeframe,
              })
              remoteByKey.delete(key)
              continue
            }
            const restored = target.find((drawing) => {
              const origin_timeframe = drawing.origin_timeframe ?? scope.timeframe
              const cross_timeframe = drawing.cross_timeframe ?? scope.crossTimeframe
              return drawingOperationKey({ drawing, origin_timeframe, cross_timeframe }) === key
            })
            if (restored) remoteByKey.set(key, {
              ...restored,
              origin_timeframe: result.origin_timeframe,
              cross_timeframe: result.cross_timeframe,
              revision: result.revision,
            })
            tombstones.current.delete(key)
          }
          remoteDrawings.current = [...remoteByKey.values()]
        } catch (error) {
          if (currentScopeToken.current !== scopeToken) break
          if (error instanceof BrowserApiError && error.status === 409) {
            setPersistence('conflict')
            const refreshRequestGeneration = ++requestGeneration.current
            try {
              const {
                items,
                truncated,
                tombstones: refreshedTombstones,
                tombstones_truncated: tombstonesTruncated,
              } = await getChartDrawings(scope.market, scope.symbol, scope.timeframe, scope.crossTimeframe)
              const safeItems = validDrawingList(items)
              const safeTombstones = Array.isArray(refreshedTombstones) ? refreshedTombstones : []
              const refreshedLocalTombstones = mergeDrawingTombstoneRevisions(
                [...tombstones.current.values()],
                safeTombstones,
                latestDrawings.current,
                scope,
              )
              const decision = decideConflictRefresh({
                capturedScopeToken: scopeToken,
                currentScopeToken: currentScopeToken.current,
                capturedRequestGeneration: refreshRequestGeneration,
                currentRequestGeneration: requestGeneration.current,
                currentEditGeneration: editGeneration.current,
                refreshed: safeItems,
                latest: latestDrawings.current,
                fallback: scope,
                tombstones: refreshedLocalTombstones,
                refreshedTombstones: safeTombstones,
                previousRetryKey: lastConflictRetryKey.current,
              })
              if (decision.kind === 'stale') break
              remoteDrawings.current = safeItems
              tombstones.current = new Map(refreshedLocalTombstones.map((tombstone) => [drawingOperationKey(tombstone), tombstone]))
              if (truncated || tombstonesTruncated || decision.kind === 'failed') { setPersistence('failed'); break }
              if (decision.kind === 'retry') {
                lastConflictRetryKey.current = decision.retryKey
                syncRequested.current = true
                setPersistence('conflict')
                break
              }
              lastConflictRetryKey.current = null
              latestDrawings.current = safeItems
              tombstones.current.clear()
              setDrawings(safeItems)
              setUndoStack([])
              setRedoStack([])
              try { writeCachedDrawings(localStorage, scope, safeItems) } catch { /* refreshed remote data is still displayed */ }
              setPersistence('synced')
            } catch {
              if (currentScopeToken.current === scopeToken && requestGeneration.current === refreshRequestGeneration) setPersistence('failed')
            }
          } else setPersistence('failed')
          break
        }
      }
      syncing.current = false
      if (syncRequested.current && currentScopeToken.current === scopeToken) void sync()
    }
    void sync()
  }, [scope, scopeToken])

  const persist = useCallback((next: Drawing[]) => {
    editGeneration.current += 1
    latestDrawings.current = next
    setDrawings(next)
    if (!scope) { setPersistence('failed'); return }
    try { writeCachedDrawings(localStorage, scope, next) } catch { setPersistence('failed'); return }
    if (!scopeLoaded.current) { setPersistence('device-only'); return }
    runSync()
  }, [runSync, scope])

  useEffect(() => {
    currentScopeToken.current = scopeToken
    editGeneration.current += 1
    const initialEditGeneration = editGeneration.current
    const loadRequestGeneration = ++requestGeneration.current
    const cached = scope ? readCachedDrawings(localStorage, scope) : null
    remoteDrawings.current = []
    tombstones.current.clear()
    lastConflictRetryKey.current = null
    scopeLoaded.current = false
    syncing.current = false
    syncRequested.current = false
    latestDrawings.current = cached?.drawings ?? []
    setDrawings(cached?.drawings ?? [])
    setUndoStack([])
    setRedoStack([])
    setPersistence(scope ? 'syncing' : 'failed')
    setLegacyDetected(Boolean(scope && hasLegacyChartDrawings(localStorage, scope)))
    setDraft([])
    setPreview(null)
    setSelectedDrawingId(null)
    setMovingDrawing(false)
    movingDrawingRef.current = null
    if (!scope) return
    const requested = scopeToken
    void getChartDrawings(scope.market, scope.symbol, scope.timeframe, scope.crossTimeframe).then(({ items, truncated, tombstones_truncated: tombstonesTruncated }) => {
      if (!isCurrentDrawingScope(requested, scope) || currentScopeToken.current !== requested || requestGeneration.current !== loadRequestGeneration) return
      const safeItems = validDrawingList(items)
      remoteDrawings.current = safeItems
      scopeLoaded.current = true
      if (editGeneration.current !== initialEditGeneration) { runSync(); return }
      latestDrawings.current = safeItems
      setDrawings(safeItems)
      setUndoStack([])
      setRedoStack([])
      try { writeCachedDrawings(localStorage, scope, safeItems) } catch { setPersistence('failed'); return }
      setPersistence(truncated || tombstonesTruncated ? 'failed' : 'synced')
    }).catch(() => {
      if (currentScopeToken.current === requested && requestGeneration.current === loadRequestGeneration) setPersistence(cached ? 'device-only' : 'failed')
    })
  }, [runSync, scope, scopeToken])

  useEffect(() => {
    if (!active) return
    onHistoryChange({ drawingCount: drawings.length, undoCount: undoStack.length, redoCount: redoStack.length, persistence, legacyDetected })
  }, [active, drawings.length, legacyDetected, onHistoryChange, persistence, redoStack.length, undoStack.length])

  useEffect(() => {
    if (command.id === handledCommand.current) return
    handledCommand.current = command.id
    if (command.targetMarkerId && command.targetMarkerId !== markerId) return
    if (!active) return
    if (command.type === 'undo') {
      setUndoStack((current) => {
        const snapshot = current.at(-1)
        if (!snapshot) return current
        setRedoStack((redo) => [...redo, latestDrawings.current])
        persist(snapshot)
        return current.slice(0, -1)
      })
    } else if (command.type === 'redo') {
      setRedoStack((current) => {
        const snapshot = current.at(-1)
        if (!snapshot) return current
        setUndoStack((undo) => [...undo, latestDrawings.current])
        persist(snapshot)
        return current.slice(0, -1)
      })
    } else if (command.type === 'clear') {
      const current = latestDrawings.current
      if (!current.length) return
      setUndoStack((undo) => [...undo, current])
      setRedoStack([])
      persist([])
    }
  }, [active, command, markerId, persist])

  useEffect(() => {
    if (!active || toolState.tool === 'cursor') {
      setDraft([])
      setPreview(null)
    }
    if (!active || toolState.tool !== 'cursor') {
      setSelectedDrawingId(null)
      setMovingDrawing(false)
      movingDrawingRef.current = null
      setDrawings(latestDrawings.current)
    }
  }, [active, toolState.tool])

  useEffect(() => {
    if (!active) return
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (movingDrawingRef.current || selectedDrawingId) {
        movingDrawingRef.current = null
        setMovingDrawing(false)
        setSelectedDrawingId(null)
        setDrawings(latestDrawings.current)
        return
      }
      setDraft([])
      setPreview(null)
      onToolComplete()
    }
    window.addEventListener('keydown', cancel)
    return () => window.removeEventListener('keydown', cancel)
  }, [active, onToolComplete, selectedDrawingId])

  const candleLevels = useMemo(() => (Array.isArray(candles) ? candles : [])
    .flatMap((item) => [item.open, item.high, item.low, item.close])
    .filter(Number.isFinite), [candles])
  const size = useMemo(() => ({
    width: Math.max(plotBounds.width, 1),
    height: Math.max(plotBounds.height, 1),
  }), [plotBounds.height, plotBounds.width])
  const snap = (point: DrawingPoint) => {
    if (toolState.magnet === 'off' || !candleLevels.length) return point
    const nearest = candleLevels.reduce((best, price) => Math.abs(price - point.price) < Math.abs(best - point.price) ? price : best, candleLevels[0])
    const threshold = Math.max(Math.abs(point.price), 1) * (toolState.magnet === 'strong' ? 0.008 : 0.003)
    return Math.abs(nearest - point.price) <= threshold ? { ...point, price: nearest } : point
  }

  const pointFromEvent = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!coordinateApi) return null
    const rect = event.currentTarget.getBoundingClientRect()
    const point = coordinateApi.coordinateToPoint(event.clientX - rect.left, event.clientY - rect.top)
    return point ? snap(point) : null
  }

  const beginMovingDrawing = (drawing: Drawing, event: ReactPointerEvent<SVGElement>) => {
    const svg = event.currentTarget.ownerSVGElement
    if (!active || toolState.tool !== 'cursor' || !coordinateApi || !svg) return
    const originalScreenPoints = drawing.points.map(coordinateApi.pointToCoordinate)
    if (originalScreenPoints.some((point) => point === null)) return
    const rect = svg.getBoundingClientRect()
    event.preventDefault()
    event.stopPropagation()
    svg.setPointerCapture(event.pointerId)
    movingDrawingRef.current = {
      drawingId: drawing.id,
      pointerId: event.pointerId,
      start: { x: event.clientX - rect.left, y: event.clientY - rect.top },
      originalScreenPoints: originalScreenPoints as ScreenPoint[],
      previewPoints: drawing.points,
    }
    setSelectedDrawingId(drawing.id)
    setMovingDrawing(true)
  }

  const moveSelectedDrawing = (event: ReactPointerEvent<SVGSVGElement>) => {
    const moving = movingDrawingRef.current
    if (!moving || moving.pointerId !== event.pointerId || !coordinateApi) {
      if (activeTool && draft.length) setPreview(pointFromEvent(event))
      return
    }
    const rect = event.currentTarget.getBoundingClientRect()
    const translated = translateDrawingScreenPoints(
      moving.originalScreenPoints,
      event.clientX - rect.left - moving.start.x,
      event.clientY - rect.top - moving.start.y,
      coordinateApi.coordinateToPoint,
    )
    if (!translated) return
    event.preventDefault()
    event.stopPropagation()
    moving.previewPoints = translated
    setDrawings((current) => current.map((drawing) => drawing.id === moving.drawingId ? { ...drawing, points: translated } : drawing))
  }

  const finishMovingDrawing = (event: ReactPointerEvent<SVGSVGElement>) => {
    const moving = movingDrawingRef.current
    if (!moving || moving.pointerId !== event.pointerId) return
    event.preventDefault()
    event.stopPropagation()
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    movingDrawingRef.current = null
    setMovingDrawing(false)
    const current = latestDrawings.current
    const original = current.find((drawing) => drawing.id === moving.drawingId)
    if (!original || JSON.stringify(original.points) === JSON.stringify(moving.previewPoints)) {
      setDrawings(current)
      return
    }
    setUndoStack((undo) => [...undo, current])
    setRedoStack([])
    persist(current.map((drawing) => drawing.id === moving.drawingId ? { ...drawing, points: moving.previewPoints } : drawing))
  }

  const cancelMovingDrawing = (event?: ReactPointerEvent<SVGSVGElement>) => {
    const moving = movingDrawingRef.current
    if (event && moving && event.currentTarget.hasPointerCapture(moving.pointerId)) event.currentTarget.releasePointerCapture(moving.pointerId)
    movingDrawingRef.current = null
    setMovingDrawing(false)
    setDrawings(latestDrawings.current)
  }

  const placePoint = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!activeTool || !active || !coordinateApi) return
    const point = pointFromEvent(event)
    if (!point) return
    event.preventDefault()
    event.stopPropagation()
    const nextDraft = [...draft, point]
    if (nextDraft.length < activeTool.points) {
      setDraft(nextDraft)
      return
    }
    const current = latestDrawings.current
    setUndoStack((undo) => [...undo, current])
    persist([...current, { id: crypto.randomUUID(), tool: activeTool.id, points: nextDraft }])
    setRedoStack([])
    setDraft([])
    setPreview(null)
    if (!toolState.continuous) onToolComplete()
  }

  const previewDrawing: Drawing | null = activeTool && draft.length && preview
    ? { id: 'preview', tool: activeTool.id, points: [...draft, preview].slice(0, activeTool.points) }
    : null
  const draftScreenPoints = coordinateApi ? draft.map(coordinateApi.pointToCoordinate).filter((point): point is ScreenPoint => Boolean(point)) : []
  void coordinateVersion

  return (
    <>
      <svg
        className={`drawing-overlay ${active && activeTool ? 'active' : ''} ${active && toolState.tool === 'cursor' && drawings.length ? 'cursor-mode' : ''} ${movingDrawing ? 'is-dragging' : ''}`}
        style={{ left: plotBounds.left, top: plotBounds.top, width: size.width, height: size.height }}
        viewBox={`0 0 ${size.width} ${size.height}`}
        preserveAspectRatio="none"
        aria-label={activeTool ? `正在使用${activeTool.label}，需要 ${activeTool.points} 个点` : 'K线画线图层'}
        onPointerDown={placePoint}
        onPointerMove={moveSelectedDrawing}
        onPointerUp={finishMovingDrawing}
        onPointerCancel={cancelMovingDrawing}
        onPointerLeave={() => { if (!movingDrawingRef.current) setPreview(null) }}
        onWheel={(event) => {
          if (!active || !activeTool) return
          event.preventDefault()
          event.stopPropagation()
          onWheelZoom(event.deltaY)
        }}
      >
        <defs><marker id={markerId} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#20a67a" /></marker></defs>
        {toolState.visible && coordinateApi && drawings.map((drawing) => <g className={`drawing-shape-group ${selectedDrawingId === drawing.id ? 'is-selected' : ''}`} key={drawing.id}>
          <DrawingShape drawing={drawing} width={size.width} height={size.height} coordinateApi={coordinateApi} markerId={markerId} />
          {active && toolState.tool === 'cursor' && <DrawingHitTarget drawing={drawing} width={size.width} height={size.height} coordinateApi={coordinateApi} onPointerDown={(event) => beginMovingDrawing(drawing, event)} />}
        </g>)}
        {previewDrawing && coordinateApi && <DrawingShape drawing={previewDrawing} width={size.width} height={size.height} coordinateApi={coordinateApi} markerId={markerId} />}
        {draftScreenPoints.map((point, index) => <circle key={`${point.x}-${point.y}-${index}`} cx={point.x} cy={point.y} r="4" fill="#20a67a" />)}
      </svg>
      {active && activeTool && <span className="drawing-hint">{activeTool.label} · {draft.length}/{activeTool.points} 个点{toolState.continuous ? ' · 连续绘制' : ''}{toolState.magnet !== 'off' ? ` · ${toolState.magnet === 'strong' ? '强' : '弱'}磁吸` : ''}</span>}
    </>
  )
}
