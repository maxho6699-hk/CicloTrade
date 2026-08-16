import { useWorkspace } from './workspace-context'
import { tierFromPlan, type CicloCoreTier } from '../components/paper/CicloCore'

/**
 * 读取当前用户的会员等级并映射为机器人 tier。
 * 任何页面渲染机器人时都调用此 hook，保证"不同等级用户只显示对应等级机器人"。
 */
export function useCicloTier(): CicloCoreTier {
  const workspace = useWorkspace()
  // 会员等级在 SessionUser.plan（如 '免费版'/'标准版'/'高级版'/'专业版'/'定制版'）
  const plan = workspace.user?.plan ?? null
  return tierFromPlan(plan)
}
