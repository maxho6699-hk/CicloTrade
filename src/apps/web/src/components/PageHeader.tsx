import { ShieldCheck } from 'lucide-react'

export function PageHeader({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return (
    <header className="page-header">
      <div className="page-header-copy"><span>{kicker}</span><h1>{title}</h1><p>{description}</p></div>
      <div className="page-header-boundary"><span><ShieldCheck aria-hidden="true" /><i /></span><div><small>ACCOUNT SCOPE</small><strong>账户域已隔离</strong><p>研究与模拟受控 · AI 不自动下单</p></div></div>
    </header>
  )
}
