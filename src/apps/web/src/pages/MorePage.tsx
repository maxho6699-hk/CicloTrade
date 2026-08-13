import {
  AlertTriangle, ArrowRight, BellRing, BookOpenCheck, CalendarClock, ChartCandlestick,
  ClipboardCheck, Gauge, Grid2X2, LifeBuoy, ListFilter, LockKeyhole, Pin, RadioTower,
  Search, ShieldCheck, Sparkles, Target, WalletCards,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import {
  filterFeatureCatalog,
  formatMorePageCopy,
  isValidPinnedSelection,
  localizeFeature,
  MORE_PAGE_COPY,
  toggleDraftPin,
  type FeatureCatalogItem,
  type FeatureCatalogPayload,
  type FeatureCategory,
  type MorePageCopy,
} from '../domain/featureCatalog'
import { useLocale } from '../i18n/useLocale'
import '../styles/more.css'

const FEATURE_ICONS = {
  BellRing, BookOpenCheck, CalendarClock, ChartCandlestick, ClipboardCheck, Gauge, Grid2X2,
  LifeBuoy, ListFilter, RadioTower, ShieldCheck, Sparkles, Target, WalletCards,
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

function FeatureCard({ item, pinned, copy, onOpen, onTogglePin }: { item: FeatureCatalogItem; pinned: boolean; copy: MorePageCopy; onOpen: () => void; onTogglePin?: () => void }) {
  const { locale } = useLocale()
  const featureCopy = localizeFeature(item, locale)
  const Icon = FEATURE_ICONS[item.icon]
  const disabled = item.availability === 'planned' || item.availability === 'unavailable' || item.availability === 'degraded'
  return (
    <article className={`feature-card ${item.availability}`}>
      <button className="feature-card-main" type="button" disabled={disabled} onClick={onOpen} aria-describedby={item.reason ? `feature-reason-${item.key}` : undefined}>
        <span className="feature-card-icon"><Icon size={19} aria-hidden="true" /></span>
        <span className="feature-card-copy"><strong>{featureCopy.title}</strong><small>{featureCopy.description}</small></span>
        <ArrowRight size={16} aria-hidden="true" />
      </button>
      <footer>
        <span className={`feature-state ${item.availability}`}>{copy.availability[item.availability]}</span>
        {item.reason && <small id={`feature-reason-${item.key}`}>{item.reason}</small>}
        {item.pinAllowed && onTogglePin && <button type="button" className="feature-pin" aria-pressed={pinned} onClick={onTogglePin}><Pin size={14} aria-hidden="true" />{pinned ? copy.unpin : copy.pin}</button>}
      </footer>
    </article>
  )
}

function FeatureSection({ title, items, pinned, copy, onOpen, onTogglePin }: { title: string; items: FeatureCatalogItem[]; pinned: Set<string>; copy: MorePageCopy; onOpen: (item: FeatureCatalogItem) => void; onTogglePin?: (key: string) => void }) {
  if (!items.length) return null
  return (
    <section className="more-section">
      <header><h2>{title}</h2><span>{items.length}</span></header>
      <div className="feature-grid">{items.map((item) => <FeatureCard key={item.key} item={item} pinned={pinned.has(item.key)} copy={copy} onOpen={() => onOpen(item)} onTogglePin={onTogglePin ? () => onTogglePin(item.key) : undefined} />)}</div>
    </section>
  )
}

export function MorePage({ catalog, loading = false, error = null, copy: injectedCopy, onRetry, onSavePins, onOpenFeature, onRecordRecent }: MorePageProps) {
  const navigate = useNavigate()
  const { locale } = useLocale()
  const copy = injectedCopy ?? MORE_PAGE_COPY[locale]
  const [query, setQuery] = useState('')
  const [draftPins, setDraftPins] = useState<string[]>(catalog?.preferences.pinned ?? [])
  const [savedPins, setSavedPins] = useState<string[]>(catalog?.preferences.pinned ?? [])
  const [pinFeedback, setPinFeedback] = useState('')
  const [recentFeedback, setRecentFeedback] = useState('')
  const [savingPins, setSavingPins] = useState(false)
  const [catalogSnapshot, setCatalogSnapshot] = useState<FeatureCatalogPayload | null>(catalog ?? null)

  useEffect(() => {
    const pinned = catalog?.preferences.pinned ?? []
    setCatalogSnapshot(catalog ?? null)
    setDraftPins(pinned)
    setSavedPins(pinned)
    setPinFeedback('')
    setRecentFeedback('')
  }, [catalog])

  const items = useMemo(() => filterFeatureCatalog(catalogSnapshot?.items ?? [], query, locale), [catalogSnapshot?.items, locale, query])
  const pinnedKeys = useMemo(() => new Set(draftPins), [draftPins])
  const byKey = useMemo(() => new Map((catalogSnapshot?.items ?? []).map((item) => [item.key, item])), [catalogSnapshot?.items])
  const selectKeys = (keys: string[]) => keys.flatMap((key) => byKey.get(key) ?? [])
  const recommended = items.filter((item) => item.recommendationRank !== null).sort((left, right) => Number(left.recommendationRank) - Number(right.recommendationRank)).slice(0, 4)
  const pinsDirty = draftPins.length !== savedPins.length || draftPins.some((key, index) => key !== savedPins[index])
  const pinsValid = isValidPinnedSelection(draftPins)

  const open = async (item: FeatureCatalogItem) => {
    if (item.availability === 'available') {
      if (onRecordRecent && catalogSnapshot) {
        try {
          const updated = await onRecordRecent(item.key, catalogSnapshot.preferences.version)
          setCatalogSnapshot(updated)
          setRecentFeedback('')
        } catch {
          setRecentFeedback(copy.recentSaveError)
        }
      }
      if (onOpenFeature) onOpenFeature(item)
      else navigate(item.route)
    } else if (item.availability === 'locked') navigate('/membership')
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

  const sectionProps = { pinned: pinnedKeys, copy, onOpen: (item: FeatureCatalogItem) => void open(item), onTogglePin: onSavePins ? togglePin : undefined }

  return (
    <div className="page more-page">
      <PageHeader kicker={copy.kicker} title={copy.title} description={copy.description} />
      <span className="sr-only" role="status" aria-live="polite">{recentFeedback}</span>
      <label className="more-search"><Search size={18} aria-hidden="true" /><span className="sr-only">{copy.searchLabel}</span><input name="feature-search" autoComplete="off" spellCheck={false} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.searchPlaceholder} /></label>
      {loading && <div className="more-state" role="status"><Sparkles size={22} aria-hidden="true" /><strong>{copy.loadingTitle}</strong><span>{copy.loadingDescription}</span></div>}
      {!loading && error && <div className="more-state error" role="alert"><AlertTriangle size={22} aria-hidden="true" /><strong>{copy.errorTitle}</strong><span>{error}</span>{onRetry && <button className="button secondary" type="button" onClick={onRetry}>{copy.retry}</button>}</div>}
      {!loading && !error && !catalogSnapshot && <div className="more-state"><LockKeyhole size={22} aria-hidden="true" /><strong>{copy.disconnectedTitle}</strong><span>{copy.disconnectedDescription}</span></div>}
      {!loading && !error && catalogSnapshot && <>
        {onSavePins && <section className="pin-manager" aria-labelledby="pin-manager-title">
          <div><h2 id="pin-manager-title">{copy.pinManagerTitle}</h2><p>{copy.pinManagerDescription}</p></div>
          <span>{formatMorePageCopy(copy.pinCount, { count: draftPins.length })}</span>
          <button className="button secondary" type="button" disabled={!pinsDirty || !pinsValid || savingPins} onClick={() => void savePins()}>{savingPins ? copy.pinSaving : copy.pinSave}</button>
          <small className={!pinsValid || pinFeedback === copy.pinLimit || pinFeedback === copy.pinSaveError ? 'error' : undefined} role="status">{pinFeedback || (!pinsValid ? copy.pinInvalid : '')}</small>
        </section>}
        {!query && <FeatureSection title={copy.pinnedSection} items={selectKeys(draftPins)} {...sectionProps} />}
        {!query && <FeatureSection title={copy.recentSection} items={selectKeys(catalogSnapshot.preferences.recent)} {...sectionProps} />}
        {!query && <FeatureSection title={copy.recommendedSection} items={recommended} {...sectionProps} />}
        {items.length ? (Object.keys(copy.categories) as FeatureCategory[]).map((category) => <FeatureSection key={category} title={copy.categories[category]} items={items.filter((item) => item.category === category)} {...sectionProps} />) : <div className="more-state"><Search size={22} aria-hidden="true" /><strong>{copy.noResultsTitle}</strong><span>{copy.noResultsDescription}</span></div>}
      </>}
    </div>
  )
}
