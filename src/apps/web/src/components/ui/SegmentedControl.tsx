import { useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
  disabled?: boolean
}

interface SegmentedControlProps<T extends string> {
  ariaLabel: string
  value: T
  options: SegmentedOption<T>[]
  onChange: (value: T) => void
  className?: string
}

export function SegmentedControl<T extends string>({
  ariaLabel,
  value,
  options,
  onChange,
  className = '',
}: SegmentedControlProps<T>) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([])
  const enabledIndexes = options.reduce<number[]>((indexes, option, index) => {
    if (!option.disabled) indexes.push(index)
    return indexes
  }, [])
  const selectedIndex = options.findIndex((option) => option.value === value && !option.disabled)
  const tabStopIndex = selectedIndex >= 0 ? selectedIndex : (enabledIndexes[0] ?? -1)

  const selectOption = (index: number) => {
    const option = options[index]
    if (!option || option.disabled) return
    buttonRefs.current[index]?.focus()
    onChange(option.value)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!enabledIndexes.length) return

    let targetIndex: number | undefined
    if (event.key === 'Home') targetIndex = enabledIndexes[0]
    if (event.key === 'End') targetIndex = enabledIndexes.at(-1)

    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const direction = event.key === 'ArrowRight' ? 1 : -1
      const currentPosition = enabledIndexes.indexOf(index)
      const startPosition = currentPosition >= 0 ? currentPosition : 0
      const nextPosition = (startPosition + direction + enabledIndexes.length) % enabledIndexes.length
      targetIndex = enabledIndexes[nextPosition]
    }

    if (targetIndex === undefined) return
    event.preventDefault()
    selectOption(targetIndex)
  }

  return (
    <div className={`ui-segmented ${className}`.trim()} role="radiogroup" aria-label={ariaLabel} aria-orientation="horizontal">
      {options.map((option, index) => (
        <button
          className={value === option.value ? 'is-active' : ''}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          aria-disabled={option.disabled || undefined}
          data-disabled={option.disabled || undefined}
          data-state={value === option.value ? 'active' : 'inactive'}
          disabled={option.disabled}
          key={option.value}
          ref={(element) => { buttonRefs.current[index] = element }}
          tabIndex={index === tabStopIndex ? 0 : -1}
          onClick={() => selectOption(index)}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          {option.icon}
          <span>{option.label}</span>
        </button>
      ))}
    </div>
  )
}
