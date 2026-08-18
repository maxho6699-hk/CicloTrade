export type CicloCoreTier = 'free' | 'standard' | 'advanced' | 'professional' | 'custom'

export const TIER_LABELS: Record<CicloCoreTier, string> = {
  free: '基础机器人',
  standard: '进阶机器人',
  advanced: '高级机器人',
  professional: '专业机器人',
  custom: '定制机器人',
}

export const CICLO_ROBOT_ASSET_VERSION = 'institutional-v3'

export const CICLO_TIER_ASSETS = {
  free: `/assets/robot/robot-lv1.png?v=${CICLO_ROBOT_ASSET_VERSION}`,
  standard: `/assets/robot/robot-lv2.png?v=${CICLO_ROBOT_ASSET_VERSION}`,
  advanced: `/assets/robot/robot-lv3.png?v=${CICLO_ROBOT_ASSET_VERSION}`,
  professional: `/assets/robot/robot-lv4.png?v=${CICLO_ROBOT_ASSET_VERSION}`,
  custom: `/assets/robot/robot-lv4.png?v=${CICLO_ROBOT_ASSET_VERSION}`,
} as const satisfies Record<CicloCoreTier, string>

export function tierFromPlan(plan: string | null | undefined): CicloCoreTier {
  if (!plan) return 'free'
  if (plan.includes('专业')) return 'professional'
  if (plan.includes('定制')) return 'custom'
  if (plan.includes('高级')) return 'advanced'
  if (plan.includes('标准')) return 'standard'
  return 'free'
}
