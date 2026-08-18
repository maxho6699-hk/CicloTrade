import { CICLO_TIER_ASSETS, TIER_LABELS, type CicloCoreTier } from './cicloCoreModel'

export { CICLO_TIER_ASSETS, TIER_LABELS, tierFromPlan, type CicloCoreTier } from './cicloCoreModel'

export type CicloCoreState = 'neutral' | 'processing' | 'locked' | 'offline'

interface CicloCoreProps {
  label: string
  size?: 'hero' | 'compact'
  state?: CicloCoreState
  tier?: CicloCoreTier
}

export function CicloCore({ label, size = 'hero', state = 'neutral', tier = 'free' }: CicloCoreProps) {
  // 等级属性
  const isPro = tier === 'professional' || tier === 'custom'
  const isAdvanced = isPro || tier === 'advanced'
  const hasParticles = isAdvanced
  const hasOrbits = tier !== 'free'
  const hasHalo = isAdvanced
  const hasEnergyLines = isPro
  const artwork = CICLO_TIER_ASSETS[tier]

  return <figure className={`ciclo-core ciclo-core-${size} ciclo-core-${tier}`} data-state={state} data-tier={tier} role="img" aria-label={`${label}（${TIER_LABELS[tier]}）`}>
    {hasEnergyLines && <span className="ciclo-core-energy-field" aria-hidden="true"><i /><i /><i /><i /></span>}
    {hasHalo && <span className="ciclo-core-image-halo" aria-hidden="true"><i /><i /></span>}
    {hasOrbits && <span className="ciclo-core-image-orbits" aria-hidden="true"><i /><i /></span>}
    <span className="ciclo-core-image-frame" aria-hidden="true">
      <img
        className="ciclo-core-hero-image"
        src={artwork}
        alt=""
        width="320"
        height="320"
        loading={size === 'hero' ? 'eager' : 'lazy'}
        decoding="async"
        style={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain', objectPosition: 'center' }}
      />
      <i className="ciclo-core-status-light" />
    </span>
    {hasParticles && <span className="ciclo-core-image-particles" aria-hidden="true"><i /><i /><i /><i /><i /><i /></span>}
  </figure>
}
