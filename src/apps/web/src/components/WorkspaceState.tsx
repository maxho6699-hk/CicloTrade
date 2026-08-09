import { CircleAlert, CloudOff, Database, FlaskConical, LoaderCircle } from 'lucide-react'
import { useWorkspace } from '../api/workspace-context'

export function WorkspaceState({ empty = false, emptyText = '当前账户还没有可展示的记录。' }: { empty?: boolean; emptyText?: string }) {
  const workspace = useWorkspace()
  if (workspace.mode === 'loading') return <div className="workspace-state loading" role="status"><LoaderCircle /><span><strong>正在读取工作区</strong><small>账户、会员与量化记录正在同步。</small></span></div>
  if (workspace.mode === 'offline') return <div className="workspace-state offline" role="alert"><CloudOff /><span><strong>数据服务离线</strong><small>{workspace.error ?? '保留界面演示数据，真实写入已关闭。'}</small></span></div>
  if (workspace.mode === 'demo') return <div className="workspace-state demo" role="status"><FlaskConical /><span><strong>当前为演示数据</strong><small>登录后切换到你的历史记录和账户状态。</small></span></div>
  if (empty) return <div className="workspace-state empty" role="status"><CircleAlert /><span><strong>暂无真实记录</strong><small>{emptyText}</small></span></div>
  return <div className="workspace-state real" role="status"><Database /><span><strong>已连接真实账户记录</strong><small>历史记录与快照可能不是实时行情，页面会分别标注来源。</small></span></div>
}
