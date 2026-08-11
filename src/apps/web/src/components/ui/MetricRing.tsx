import type { CSSProperties } from "react";

type MetricRingTone = "accent" | "positive" | "warning" | "negative";

export function MetricRing({
  label,
  value,
  displayValue,
  caption,
  tone = "accent",
}: {
  label: string;
  value: number;
  displayValue: string;
  caption?: string;
  tone?: MetricRingTone;
}) {
  const normalizedValue = Math.max(0, Math.min(100, value));
  const style = {
    "--metric-ring-value": normalizedValue,
  } as CSSProperties;

  return (
    <figure
      className={`metric-ring metric-ring-${tone}`}
      style={style}
      role="img"
      aria-label={`${label}：${displayValue}`}
    >
      <div className="metric-ring-dial" aria-hidden="true">
        <span>
          <strong>{displayValue}</strong>
          <small>{label}</small>
        </span>
      </div>
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
}
