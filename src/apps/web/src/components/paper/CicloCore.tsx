export type CicloCoreState = 'neutral' | 'processing' | 'locked' | 'offline'

/** 会员等级 → 机器人形态（产品负责人 2026-08-16 明确） */
export type CicloCoreTier = 'free' | 'standard' | 'advanced' | 'professional' | 'custom'

export const TIER_LABELS: Record<CicloCoreTier, string> = {
  free: '基础机器人',
  standard: '进阶机器人',
  advanced: '高级机器人',
  professional: '专业机器人',
  custom: '定制机器人',
}

/** 从会员 plan 字符串映射到等级 */
export function tierFromPlan(plan: string | null | undefined): CicloCoreTier {
  if (!plan) return 'free'
  if (plan.includes('专业')) return 'professional'
  if (plan.includes('定制')) return 'custom'
  if (plan.includes('高级')) return 'advanced'
  if (plan.includes('标准')) return 'standard'
  return 'free'
}

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

  return <figure className={`ciclo-core ciclo-core-${size} ciclo-core-${tier}`} data-state={state} data-tier={tier} role="img" aria-label={`${label}（${TIER_LABELS[tier]}）`}>
    {hasEnergyLines && <span className="ciclo-core-energy-field" aria-hidden="true"><i /><i /><i /><i /></span>}
    {hasHalo && <span className="ciclo-core-image-halo" aria-hidden="true"><i /><i /></span>}
    {hasOrbits && <span className="ciclo-core-image-orbits" aria-hidden="true"><i /><i /></span>}
    <span className="ciclo-core-image-frame" aria-hidden="true">
      <img
        className="ciclo-core-hero-image"
        src="/assets/robot/robot-hero.png"
        alt=""
        width="320"
        height="300"
        loading={size === 'hero' ? 'eager' : 'lazy'}
        decoding="async"
        style={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain' }}
      />
      <i className="ciclo-core-status-light" />
    </span>
    {hasParticles && <span className="ciclo-core-image-particles" aria-hidden="true"><i /><i /><i /><i /><i /><i /></span>}
  </figure>
}
