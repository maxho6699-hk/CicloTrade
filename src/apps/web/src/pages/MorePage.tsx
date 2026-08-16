import {
  AlertTriangle, ArrowRight, BarChart3, BellRing, BookOpenCheck, BrainCircuit, CalendarClock, ChartCandlestick,
  ChevronDown, CircleHelp, ClipboardCheck, Crown, FileText, FlaskConical, Gauge, Grid2X2, LifeBuoy, List,
  ListFilter, LockKeyhole, Megaphone, MessageSquareText, Pin, RadioTower, Scale, Search, ShieldCheck,
  Sparkles, Swords, Target, UserRound, WalletCards, Workflow, type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import {
  filterFeatureCatalog,
  formatMorePageCopy,
  FEATURE_CATALOG_VIEW_STORAGE_KEY,
  featureOpenRoute,
  isValidPinnedSelection,
  localizeFeature,
  localizeFeatureReason,
  MORE_PAGE_COPY,
  readFeatureCatalogView,
  toggleDraftPin,
  writeFeatureCatalogView,
  type FeatureCatalogItem,
  type FeatureCatalogPayload,
  type FeatureCatalogView,
  type MorePageCopy,
} from '../domain/featureCatalog'
import { useLocale } from '../i18n/useLocale'
import { useCicloTier } from '../api/use-ciclo-tier'
import '../styles/more.css'

const FEATURE_ICONS = {
  BellRing, BookOpenCheck, CalendarClock, ChartCandlestick, ClipboardCheck, Gauge, Grid2X2,
  LifeBuoy, ListFilter, RadioTower, ShieldCheck, Sparkles, Target, WalletCards,
}

const FEATURE_ROUTE_ICONS: Record<string, LucideIcon> = {
  '/membership': Crown, '/account': UserRound, '/ai': BrainCircuit, '/workflow': Workflow, '/lab': FlaskConical,
  '/reports': BarChart3, '/notifications': BellRing, '/promotion': Megaphone, '/help': CircleHelp,
  '/feedback': MessageSquareText, '/legal': Scale, '/earnings': CalendarClock,
}

type HubGroupKey = 'account' | 'ai' | 'research' | 'reports' | 'notifications' | 'support' | 'legal'
type HubGroup = { key: HubGroupKey; Icon: LucideIcon }
const HUB_GROUPS: HubGroup[] = [
  { key: 'account', Icon: Crown },
  { key: 'ai', Icon: BrainCircuit },
  { key: 'research', Icon: FlaskConical },
  { key: 'reports', Icon: BarChart3 },
  { key: 'notifications', Icon: Megaphone },
  { key: 'support', Icon: CircleHelp },
  { key: 'legal', Icon: Scale },
]

const HUB_FALLBACK_ROUTES: Record<HubGroupKey, string> = {
  account: '/account',
  ai: '/ai',
  research: '/discover',
  reports: '/reports',
  notifications: '/notifications',
  support: '/help',
  legal: '/legal',
}

function hubGroupFor(item: FeatureCatalogItem): HubGroupKey {
  const route = item.route
  if (route === '/legal' || route === '/admin' || route === '/more') return 'legal'
  if (route === '/help' || route === '/feedback') return 'support'
  if (route === '/notifications' || route === '/promotion') return 'notifications'
  if (route === '/reports' || item.category === 'review') return 'reports'
  if (route === '/ai' || route === '/workflow' || route === '/trade' || item.category === 'automation') return 'ai'
  if (route === '/account' || route === '/membership' || item.category === 'account') return 'account'
  return 'research'
}

export interface MorePageProps {
  catalog?: FeatureCatalogPayload | null
  loading?: boolean
  error?: string | null
  copy?: MorePageCopy
  onRetry?: () => void
  onSavePins?: (pinned: string[], recent: string[], expectedVersion: number) => FeatureCatalogPayload | Promise<FeatureCatalogPayload>
  onOpenFeature?: (item: FeatureCatalogItem) => void
  onRecordRecent?: (key: string, expectedVersion: number) => FeatureCatalogPayload | Promise<FeatureCatalogPayload>
}

function FeatureCard({ item, pinned, copy, view, onOpen, onTogglePin }: { item: FeatureCatalogItem; pinned: boolean; copy: MorePageCopy; view: FeatureCatalogView; onOpen: () => void; onTogglePin?: () => void }) {
  const { locale } = useLocale()
  const featureCopy = localizeFeature(item, locale)
  const localizedReason = localizeFeatureReason(item.reason, locale)
  const Icon = FEATURE_ROUTE_ICONS[item.route] ?? FEATURE_ICONS[item.icon]
  const disabled = featureOpenRoute(item) === null
  const pinLabel = pinned ? copy.unpin : copy.pin
  const cardContent = <>
    <span className="feature-card-icon"><Icon size={19} aria-hidden="true" /></span>
    <span className="feature-card-copy"><strong>{featureCopy.title}</strong><small>{featureCopy.description}</small></span>
    <ArrowRight size={16} aria-hidden="true" />
  </>
  return (
    <article className={`feature-card feature-card--${view} ${item.availability}`}>
      {disabled ? (
        <button className="feature-card-main" type="button" disabled aria-describedby={item.reason ? `feature-reason-${item.key}` : undefined}>{cardContent}</button>
      ) : (
        <Link className="feature-card-main" to={featureOpenRoute(item)!} onClick={onOpen} aria-describedby={item.reason ? `feature-reason-${item.key}` : undefined}>{cardContent}</Link>
      )}
      <footer>
        <span className={`feature-state ${item.availability}`}>{copy.availability[item.availability]}</span>
        {localizedReason && <small id={`feature-reason-${item.key}`}>{localizedReason}</small>}
        {item.pinAllowed && onTogglePin && <button type="button" className="feature-pin" aria-label={pinLabel} title={pinLabel} aria-pressed={pinned} onClick={onTogglePin}><Pin size={14} aria-hidden="true" /><span className="feature-pin-label">{pinned ? copy.unpin : copy.pin}</span></button>}
      </footer>
    </article>
  )
}

function FeatureSection({ title, items, pinned, copy, view, onOpen, onTogglePin }: { title: string; items: FeatureCatalogItem[]; pinned: Set<string>; copy: MorePageCopy; view: FeatureCatalogView; onOpen: (item: FeatureCatalogItem) => void; onTogglePin?: (key: string) => void }) {
  if (!items.length) return null
  return (
    <section className="more-section">
      <header><h2>{title}</h2><span>{items.length}</span></header>
      <div className={`feature-grid feature-grid--${view}`}>{items.map((item) => <FeatureCard key={item.key} item={item} pinned={pinned.has(item.key)} copy={copy} view={view} onOpen={() => onOpen(item)} onTogglePin={onTogglePin ? () => onTogglePin(item.key) : undefined} />)}</div>
    </section>
  )
}

function HubGroupCard({ group, items, expanded, pinned, copy, view, onToggle, onOpen, onTogglePin }: { group: HubGroup; items: FeatureCatalogItem[]; expanded: boolean; pinned: Set<string>; copy: MorePageCopy; view: FeatureCatalogView; onToggle: () => void; onOpen: (item: FeatureCatalogItem) => void; onTogglePin?: (key: string) => void }) {
  const available = items.filter((item) => item.availability === 'available').length
  const groupCopy = copy.hubGroups[group.key]
  return <section className={`more-hub-group ${expanded ? 'is-expanded' : ''}`}>
    <button className="more-hub-group-trigger" type="button" aria-expanded={expanded} onClick={onToggle}>
      <span className="more-hub-group-icon"><group.Icon aria-hidden="true" /></span>
      <span className="more-hub-group-copy"><strong>{groupCopy.title}</strong><small>{groupCopy.description}</small></span>
      <span className="more-hub-group-count"><b>{items.length}</b><small>{formatMorePageCopy(copy.hubAvailable, { count: available })}</small></span>
      <ChevronDown className="more-hub-group-chevron" aria-hidden="true" />
    </button>
    <div className="more-hub-group-expand"><div>{items.length ? <div className={`feature-grid feature-grid--${view}`}>{items.map((item) => <FeatureCard key={item.key} item={item} pinned={pinned.has(item.key)} copy={copy} view={view} onOpen={() => onOpen(item)} onTogglePin={onTogglePin ? () => onTogglePin(item.key) : undefined} />)}</div> : <div className="more-group-empty"><FileText aria-hidden="true" /><span>{copy.hubEmpty}</span></div>}</div></div>
  </section>
}

export function MorePage({ catalog, loading = false, error = null, copy: injectedCopy, onRetry, onSavePins, onOpenFeature, onRecordRecent }: MorePageProps) {
  const navigate = useNavigate()
  const cicloTier = useCicloTier()
  const { locale } = useLocale()
  const copy = injectedCopy ?? MORE_PAGE_COPY[locale]
  const [query, setQuery] = useState('')
  const [draftPins, setDraftPins] = useState<string[]>(catalog?.preferences.pinned ?? [])
  const [savedPins, setSavedPins] = useState<string[]>(catalog?.preferences.pinned ?? [])
  const [pinFeedback, setPinFeedback] = useState('')
  const [recentFeedback, setRecentFeedback] = useState('')
  const [savingPins, setSavingPins] = useState(false)
  const [catalogSnapshot, setCatalogSnapshot] = useState<FeatureCatalogPayload | null>(catalog ?? null)
  const [expandedGroup, setExpandedGroup] = useState<HubGroupKey | null>('account')
  const [view, setView] = useState<FeatureCatalogView>(() => {
    const isPhone = typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches
    let stored: string | null = null
    try { stored = typeof window === 'undefined' ? null : window.localStorage.getItem(FEATURE_CATALOG_VIEW_STORAGE_KEY) } catch { /* storage can be unavailable */ }
    return readFeatureCatalogView(stored, isPhone)
  })

  useEffect(() => {
    const pinned = catalog?.preferences.pinned ?? []
    setCatalogSnapshot(catalog ?? null)
    setDraftPins(pinned)
    setSavedPins(pinned)
    setPinFeedback('')
    setRecentFeedback('')
  }, [catalog])

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpandedGroup(null) }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [])

  const items = useMemo(() => filterFeatureCatalog(catalogSnapshot?.items ?? [], query, locale).filter((item) => !item.primaryNav), [catalogSnapshot?.items, locale, query])
  const pinnedKeys = useMemo(() => new Set(draftPins), [draftPins])
  const byKey = useMemo(() => new Map((catalogSnapshot?.items ?? []).map((item) => [item.key, item])), [catalogSnapshot?.items])
  const selectKeys = (keys: string[]) => keys.flatMap((key) => byKey.get(key) ?? []).filter((item) => !item.primaryNav)
  const recommended = items.filter((item) => item.recommendationRank !== null).sort((left, right) => Number(left.recommendationRank) - Number(right.recommendationRank)).slice(0, 4)
  const pinnedItems = selectKeys(draftPins)
  const pinnedItemKeys = new Set(pinnedItems.map((item) => item.key))
  const recentItems = selectKeys(catalogSnapshot?.preferences.recent ?? []).filter((item) => !pinnedItemKeys.has(item.key)).slice(0, 4)
  const recentItemKeys = new Set(recentItems.map((item) => item.key))
  const recommendedItems = recommended.filter((item) => !pinnedItemKeys.has(item.key) && !recentItemKeys.has(item.key))
  const availableCount = items.filter((item) => item.availability === 'available').length
  const categoryCount = HUB_GROUPS.length
  const groupedItems = useMemo(() => HUB_GROUPS.reduce<Record<HubGroupKey, FeatureCatalogItem[]>>((result, group) => {
    result[group.key] = items.filter((item) => hubGroupFor(item) === group.key)
    return result
  }, { account: [], ai: [], research: [], reports: [], notifications: [], support: [], legal: [] }), [items])
  const deliberationItem = (catalogSnapshot?.items ?? []).find((item) => featureOpenRoute(item) === '/deliberation')
  const deliberationTitle = deliberationItem ? localizeFeature(deliberationItem, locale).title : copy.categories.research
  const pinsDirty = draftPins.length !== savedPins.length || draftPins.some((key, index) => key !== savedPins[index])
  const pinsValid = isValidPinnedSelection(draftPins)

  const open = (item: FeatureCatalogItem) => {
    const route = featureOpenRoute(item)
    if (!route) return
    if (item.availability === 'available') {
      if (onRecordRecent && catalogSnapshot) {
        // Do not prevent the Link, wait for the response, or force same-tab
        // navigation. Recent tracking is advisory and may finish after the
        // destination has mounted.
        void Promise.resolve()
          .then(() => onRecordRecent(item.key, catalogSnapshot.preferences.version))
          .then((updated) => { setCatalogSnapshot(updated); setRecentFeedback('') })
          .catch(() => setRecentFeedback(copy.recentSaveError))
      }
      if (onOpenFeature) {
        try { onOpenFeature(item) } catch { /* custom analytics must not block Link navigation */ }
      }
    } else navigate(route)
  }

  const togglePin = (key: string) => {
    if (!draftPins.includes(key) && draftPins.length === 5) {
      setPinFeedback(copy.pinLimit)
      return
    }
    setDraftPins(toggleDraftPin(draftPins, key))
    setPinFeedback('')
  }

  const savePins = async () => {
    if (!catalogSnapshot || !onSavePins || !pinsDirty || !pinsValid || savingPins) return
    setSavingPins(true)
    setPinFeedback('')
    try {
      const updated = await onSavePins(
        [...draftPins], [...catalogSnapshot.preferences.recent], catalogSnapshot.preferences.version,
      )
      setCatalogSnapshot(updated)
      setDraftPins(updated.preferences.pinned)
      setSavedPins(updated.preferences.pinned)
      setPinFeedback(copy.pinSaved)
    } catch {
      setPinFeedback(copy.pinSaveError)
    } finally {
      setSavingPins(false)
    }
  }

  const setFeatureView = (next: FeatureCatalogView) => {
    setView(next)
    writeFeatureCatalogView(typeof window === 'undefined' ? null : window.localStorage, next)
  }
  const sectionProps = { pinned: pinnedKeys, copy, view, onOpen: (item: FeatureCatalogItem) => void open(item), onTogglePin: onSavePins ? togglePin : undefined }

  return (
    <div className="page more-page">
      <PageHeader kicker={copy.kicker} title={copy.title} description={copy.description} />
      <section className="more-hub-hero" aria-label={copy.hubEyebrow}>
        <div className="more-hub-copy">
          <header><span><Sparkles size={14} /> CICLO SERVICE HUB</span><strong>{copy.hubEyebrow}</strong><small><i />{copy.deliberationStatus}</small></header>
          <div className="more-hub-primary"><h2>{copy.hubTitle}</h2><p>{copy.hubDescription}</p></div>
          <div className="more-hub-metrics"><span><small>{copy.availableMetric}</small><strong>{availableCount}</strong></span><span><small>{copy.categoryMetric}</small><strong>{categoryCount}</strong></span><span><small>{copy.pinnedMetric}</small><strong>{draftPins.length}/5</strong></span><span><small>{copy.tierMetric}</small><strong>{cicloTier.toUpperCase()}</strong></span></div>
          <div className="more-tools">
            <label className="more-search"><Search size={18} aria-hidden="true" /><span className="sr-only">{copy.searchLabel}</span><input name="feature-search" autoComplete="off" spellCheck={false} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.searchPlaceholder} /></label>
            <div className="feature-view-toggle" role="group" aria-label={copy.viewLabel}>
              <button className={view === 'list' ? 'active' : ''} type="button" aria-pressed={view === 'list'} onClick={() => setFeatureView('list')}><List size={17} aria-hidden="true" /><span>{copy.listView}</span></button>
              <button className={view === 'icon' ? 'active' : ''} type="button" aria-pressed={view === 'icon'} onClick={() => setFeatureView('icon')}><Grid2X2 size={17} aria-hidden="true" /><span>{copy.iconView}</span></button>
            </div>
          </div>
          <ol className="more-service-flow" aria-label={copy.serviceFlowLabel}>{copy.serviceFlow.map((label) => <li key={label}><i /><span>{label}</span></li>)}</ol>
          <div className="more-hub-actions"><Link className="button primary" to="/deliberation"><Swords size={16} />{deliberationTitle}</Link></div>
          <footer><span><i />{copy.deliberationStatus}</span><small>{copy.safetyNote}</small></footer>
        </div>
        <div className="more-hub-core" aria-label={`${copy.hubEyebrow} Ciclo`}><span className="more-core-icon"><BrainCircuit aria-hidden="true" /></span><small>CICLO RESEARCH ASSISTANT</small><strong>{copy.hubEyebrow}</strong><p>{copy.safetyNote}</p></div>
      </section>
      <span className="sr-only" role="status" aria-live="polite">{recentFeedback}</span>
      {loading && <div className="more-sync-state" role="status"><Sparkles size={20} aria-hidden="true" /><span><strong>{copy.loadingTitle}</strong><small>{copy.loadingDescription}</small></span></div>}
      {!loading && error && catalogSnapshot && <div className="more-sync-state error" role="alert"><AlertTriangle size={20} aria-hidden="true" /><span><strong>{copy.errorTitle}</strong><small>{error}</small></span>{onRetry && <button className="button secondary" type="button" onClick={onRetry}>{copy.retry}</button>}</div>}
      {!loading && !catalogSnapshot && <section className="more-fallback" aria-labelledby="more-fallback-title">
        <div className={`more-sync-state ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{error ? <AlertTriangle size={20} aria-hidden="true" /> : <LockKeyhole size={20} aria-hidden="true" />}<span><strong id="more-fallback-title">{error ? copy.errorTitle : copy.disconnectedTitle}</strong><small>{error || copy.disconnectedDescription}</small></span>{onRetry && <button className="button secondary" type="button" onClick={onRetry}>{copy.retry}</button>}</div>
        <div className="more-fallback-grid">{HUB_GROUPS.map((group) => { const groupCopy = copy.hubGroups[group.key]; return <Link key={group.key} to={HUB_FALLBACK_ROUTES[group.key]}><span><group.Icon aria-hidden="true" /></span><div><strong>{groupCopy.title}</strong><small>{groupCopy.description}</small></div><ArrowRight aria-hidden="true" /></Link> })}</div>
      </section>}
      {!loading && catalogSnapshot && <>
        {onSavePins && <section className="pin-manager" aria-labelledby="pin-manager-title">
          <div><h2 id="pin-manager-title">{copy.pinManagerTitle}</h2><p>{copy.pinManagerDescription}</p></div>
          <span>{formatMorePageCopy(copy.pinCount, { count: draftPins.length })}</span>
          <button className="button secondary" type="button" disabled={!pinsDirty || !pinsValid || savingPins} onClick={() => void savePins()}>{savingPins ? copy.pinSaving : copy.pinSave}</button>
          <small className={!pinsValid || pinFeedback === copy.pinLimit || pinFeedback === copy.pinSaveError ? 'error' : undefined} role="status">{pinFeedback || (!pinsValid ? copy.pinInvalid : '')}</small>
        </section>}
        {!query && <div className="more-quick-grid"><FeatureSection title={copy.pinnedSection} items={pinnedItems} {...sectionProps} /><FeatureSection title={copy.recentSection} items={recentItems} {...sectionProps} /><FeatureSection title={copy.recommendedSection} items={recommendedItems} {...sectionProps} /></div>}
        {items.length ? <section className="more-aggregate-directory" aria-labelledby="more-aggregate-title"><header><div><span>PERSONAL SERVICE DIRECTORY</span><h2 id="more-aggregate-title">{copy.directoryTitle}</h2><p>{copy.directoryDescription}</p></div><strong>{formatMorePageCopy(copy.serviceCount, { count: items.length })}</strong></header><div className="more-hub-group-grid">{HUB_GROUPS.map((group) => <HubGroupCard key={group.key} group={group} items={groupedItems[group.key]} expanded={expandedGroup === group.key} pinned={pinnedKeys} copy={copy} view={view} onToggle={() => setExpandedGroup((current) => current === group.key ? null : group.key)} onOpen={(item) => void open(item)} onTogglePin={onSavePins ? togglePin : undefined} />)}</div></section> : <div className="more-state"><Search size={22} aria-hidden="true" /><strong>{copy.noResultsTitle}</strong><span>{copy.noResultsDescription}</span></div>}
      </>}
    </div>
  )
}
