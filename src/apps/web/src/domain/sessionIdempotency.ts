interface SessionIntent {
  fingerprint: string
  idempotencyKey: string
}

type SessionIntentMap = Record<string, SessionIntent>
type SessionStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function validIntent(value: unknown): value is SessionIntent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return Object.keys(record).length === 2
    && typeof record.fingerprint === 'string'
    && record.fingerprint.length <= 4_096
    && typeof record.idempotencyKey === 'string'
    && /^auto-live-[A-Za-z0-9._:-]{8,180}$/.test(record.idempotencyKey)
}

function read(store: SessionStore | null, namespace: string): SessionIntentMap {
  if (!store) return {}
  try {
    const parsed: unknown = JSON.parse(store.getItem(namespace) ?? '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter(([scope, value]) => scope.length <= 180 && validIntent(value)).slice(0, 64))
  } catch { return {} }
}

function write(store: SessionStore | null, namespace: string, intents: SessionIntentMap): void {
  if (!store) return
  try {
    if (Object.keys(intents).length) store.setItem(namespace, JSON.stringify(intents))
    else store.removeItem(namespace)
  } catch { /* session storage can be disabled */ }
}

export interface SessionIdempotencyRegistry {
  key(scope: string, fingerprint: string): string
  clear(scope: string, fingerprint: string): void
  hasPending(): boolean
}

export function createSessionIdempotencyRegistry(namespace: string, store?: SessionStore | null): SessionIdempotencyRegistry {
  const sessionStore = store === undefined
    ? (typeof window === 'undefined' ? null : window.sessionStorage)
    : store
  let memory = read(sessionStore, namespace)
  const current = () => ({ ...memory, ...read(sessionStore, namespace) })
  return {
    key(scope, fingerprint) {
      const intents = current()
      const existing = intents[scope]
      if (existing?.fingerprint === fingerprint) return existing.idempotencyKey
      const idempotencyKey = `auto-live-${scope}-${crypto.randomUUID()}`
      memory = { ...intents, [scope]: { fingerprint, idempotencyKey } }
      write(sessionStore, namespace, memory)
      return idempotencyKey
    },
    clear(scope, fingerprint) {
      const intents = current()
      if (intents[scope]?.fingerprint !== fingerprint) return
      delete intents[scope]
      memory = intents
      write(sessionStore, namespace, intents)
    },
    hasPending() { return Object.keys(current()).length > 0 },
  }
}
