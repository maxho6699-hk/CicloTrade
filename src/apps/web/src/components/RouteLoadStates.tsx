import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { LoaderCircle } from 'lucide-react'

type RouteModule = { default: ComponentType }

export function RouteLoadingState() {
  return <div className="route-loading" role="status"><LoaderCircle aria-hidden="true" /><strong>正在载入工作区</strong><span>保留导航与账户状态</span></div>
}

function RouteChunkFailurePage() {
  return <div className="route-failure" role="alert"><strong>工作区资源载入失败</strong><span>网络或缓存中的页面资源可能已更新，请重新载入后再试。</span><button type="button" onClick={() => window.location.reload()}>重新载入</button></div>
}

export function lazyRoute(loader: () => Promise<RouteModule>): LazyExoticComponent<ComponentType> {
  return lazy(() => loader().catch(() => ({ default: RouteChunkFailurePage })))
}
