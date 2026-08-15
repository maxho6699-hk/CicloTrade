import { useId } from 'react'

export type CicloCoreState = 'neutral' | 'processing' | 'locked' | 'offline'

interface CicloCoreProps {
  label: string
  size?: 'hero' | 'compact'
  state?: CicloCoreState
}

export function CicloCore({ label, size = 'hero', state = 'neutral' }: CicloCoreProps) {
  const gradientId = useId().replaceAll(':', '')
  const coreGradient = `core-${gradientId}`
  const orbitGradient = `orbit-${gradientId}`

  return <figure className={`ciclo-core ciclo-core-${size}`} data-state={state} role="img" aria-label={label}>
    <svg viewBox="0 0 320 300" focusable="false" aria-hidden="true">
      <defs>
        <linearGradient id={coreGradient} x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stopColor="var(--paper-ai-blue)" />
          <stop offset="0.52" stopColor="var(--paper-ai-violet)" />
          <stop offset="1" stopColor="var(--paper-ai-magenta)" />
        </linearGradient>
        <linearGradient id={orbitGradient} x1="0" x2="1">
          <stop offset="0" stopColor="var(--paper-ai-blue)" stopOpacity="0" />
          <stop offset="0.48" stopColor="var(--paper-ai-blue)" />
          <stop offset="1" stopColor="var(--paper-ai-magenta)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <g className="ciclo-core-orbits">
        <ellipse cx="160" cy="244" rx="116" ry="24" />
        <ellipse cx="160" cy="244" rx="82" ry="15" />
        <path d="M42 244c40-28 196-28 236 0" />
      </g>
      <g className="ciclo-core-body">
        <path className="ciclo-core-shell" d="M104 162 132 143h61l26 25 15 64-29 27h-88l-31-29Z" />
        <path className="ciclo-core-inset" d="m122 178 25-15h35l23 18 8 48-22 17h-61l-22-20Z" />
        <path className="ciclo-core-neck" d="m135 151 6-25h38l7 25-13 16h-26Z" />
        <path className="ciclo-core-head" d="m91 76 47-34h61l37 26 8 53-34 36h-87l-39-29Z" />
        <path className="ciclo-core-visor" d="M119 102c8-28 29-43 58-43 17 0 31 5 42 15-25 1-41 12-50 34-8 19-5 36 9 49-42-1-68-21-59-55Z" />
        <path className="ciclo-core-visor-line" d="M133 105c8-19 24-30 47-30 10 0 20 2 28 7-18 4-30 14-36 29-5 12-4 23 2 34-28-3-49-17-41-40Z" />
        <path className="ciclo-core-side ciclo-core-side-left" d="m84 89-22 20 7 36 25 10 15-26-5-35Z" />
        <path className="ciclo-core-side ciclo-core-side-right" d="m236 86 23 18-4 38-25 14-16-24 4-38Z" />
        <path className="ciclo-core-arm ciclo-core-arm-left" d="m91 175-29 20-3 43 24 13 17-25-3-31Z" />
        <path className="ciclo-core-arm ciclo-core-arm-right" d="m226 176 29 18 5 42-23 16-18-24 2-34Z" />
      </g>
      <g className="ciclo-core-evidence">
        <path fill={`url(#${coreGradient})`} d="m160 179 27 19-10 33h-34l-10-33Z" />
        <path d="m160 187 17 13-7 22h-20l-7-22Z" />
      </g>
      <g className="ciclo-core-rails" stroke={`url(#${orbitGradient})`}>
        <ellipse cx="160" cy="206" rx="76" ry="29" transform="rotate(-12 160 206)" />
        <ellipse cx="160" cy="206" rx="92" ry="36" transform="rotate(15 160 206)" />
      </g>
      <circle className="ciclo-core-status" cx="217" cy="64" r="7" />
    </svg>
  </figure>
}
