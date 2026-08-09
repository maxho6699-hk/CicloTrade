export function PageHeader({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return (
    <header className="page-header">
      <span>{kicker}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  )
}
