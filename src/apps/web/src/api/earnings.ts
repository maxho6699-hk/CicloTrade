import {
  decodeEarningsDetail,
  decodeEarningsHistory,
  decodeEarningsOptionDetail,
  decodeEarningsOverview,
  decodeEarningsStatistics,
  type EarningsDetail,
  type EarningsHistory,
  type EarningsOptionDetail,
  type EarningsOverview,
  type EarningsStatistics,
} from '../domain/earningsForecast.ts'
import { authenticatedJsonRequest, BrowserApiError } from './client.ts'

const EARNINGS_BASE = '/api/rewrite/v1/earnings-forecasts'
const OPAQUE_ID = /^[A-Za-z0-9_-]{20,64}$/

export class EarningsApiError extends Error {
  status: number

  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

export type EarningsTransport = (path: string, signal?: AbortSignal) => Promise<unknown>

export interface EarningsApi {
  fetchOverview: (signal?: AbortSignal) => Promise<EarningsOverview>
  fetchDetail: (eventId: string, signal?: AbortSignal) => Promise<EarningsDetail>
  fetchHistory: (cursor?: string | null, signal?: AbortSignal) => Promise<EarningsHistory>
  fetchStatistics: (signal?: AbortSignal) => Promise<EarningsStatistics>
  fetchOptionDetail: (eventId: string, optionId: string, signal?: AbortSignal) => Promise<EarningsOptionDetail>
}

function safeOpaqueId(value: string, label: string) {
  if (!OPAQUE_ID.test(value)) throw new EarningsApiError(`${label} 无效。`, 400)
  return encodeURIComponent(value)
}

async function browserTransport(path: string, signal?: AbortSignal): Promise<unknown> {
  try {
    return await authenticatedJsonRequest<unknown>(path, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal,
    })
  } catch (error) {
    if (error instanceof BrowserApiError) {
      throw new EarningsApiError(error.message.slice(0, 300), error.status)
    }
    throw error
  }
}

export function createEarningsApi(transport: EarningsTransport = browserTransport): EarningsApi {
  return {
    async fetchOverview(signal) {
      return decodeEarningsOverview(await transport(`${EARNINGS_BASE}?window_days=7&limit=100`, signal))
    },
    async fetchDetail(eventId, signal) {
      return decodeEarningsDetail(await transport(`${EARNINGS_BASE}/${safeOpaqueId(eventId, 'event_id')}`, signal))
    },
    async fetchHistory(cursor, signal) {
      const params = new URLSearchParams({ limit: '50' })
      if (cursor) params.set('cursor', safeOpaqueId(cursor, 'cursor'))
      return decodeEarningsHistory(await transport(`${EARNINGS_BASE}/history?${params}`, signal))
    },
    async fetchStatistics(signal) {
      return decodeEarningsStatistics(await transport(`${EARNINGS_BASE}/statistics`, signal))
    },
    async fetchOptionDetail(eventId, optionId, signal) {
      return decodeEarningsOptionDetail(await transport(
        `${EARNINGS_BASE}/${safeOpaqueId(eventId, 'event_id')}/options/${safeOpaqueId(optionId, 'option_id')}`,
        signal,
      ))
    },
  }
}

export const earningsApi = createEarningsApi()

export async function loadEarningsInitialState(api: EarningsApi, signal?: AbortSignal): Promise<{
  overview: EarningsOverview
  detail: EarningsDetail | null
}> {
  const overview = await api.fetchOverview(signal)
  if (overview.state === 'locked' || overview.items.length === 0) return { overview, detail: null }
  return { overview, detail: await api.fetchDetail(overview.items[0].event_id, signal) }
}
