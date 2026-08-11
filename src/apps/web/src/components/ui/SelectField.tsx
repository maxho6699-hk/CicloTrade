import { ChevronDown, LoaderCircle } from 'lucide-react'
import { useId } from 'react'
import type { ChangeEvent, KeyboardEvent, PointerEvent, SelectHTMLAttributes } from 'react'

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  hint?: string
  error?: string
  invalid?: boolean
  loading?: boolean
  readOnly?: boolean
}

export function SelectField({
  label,
  hint,
  error,
  invalid = false,
  loading = false,
  readOnly = false,
  className = '',
  children,
  id,
  disabled,
  onChange,
  onKeyDown,
  onPointerDown,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
  ...props
}: SelectFieldProps) {
  const generatedId = useId()
  const fieldId = id ?? `select-${generatedId}`
  const hintId = `${fieldId}-hint`
  const errorId = `${fieldId}-error`
  const describedBy = [ariaDescribedBy, hint ? hintId : undefined, error ? errorId : undefined].filter(Boolean).join(' ') || undefined
  const hasAriaInvalid = ariaInvalid !== undefined && ariaInvalid !== false && ariaInvalid !== 'false'
  const isInvalid = invalid || Boolean(error) || hasAriaInvalid
  const isDisabled = Boolean(disabled || loading)

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    if (readOnly) return
    onChange?.(event)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLSelectElement>) => {
    if (readOnly && event.key !== 'Tab') event.preventDefault()
    onKeyDown?.(event)
  }

  const handlePointerDown = (event: PointerEvent<HTMLSelectElement>) => {
    if (readOnly) event.preventDefault()
    onPointerDown?.(event)
  }

  return (
    <label
      className={`select-field ${className}`.trim()}
      data-disabled={isDisabled || undefined}
      data-invalid={isInvalid || undefined}
      data-loading={loading || undefined}
      data-readonly={readOnly || undefined}
      htmlFor={fieldId}
    >
      <span className="select-field-label">{label}</span>
      <span className="select-field-control">
        <select
          {...props}
          id={fieldId}
          disabled={isDisabled}
          aria-busy={loading || undefined}
          aria-describedby={describedBy}
          aria-invalid={isInvalid ? true : ariaInvalid}
          aria-readonly={readOnly || undefined}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPointerDown={handlePointerDown}
        >
          {children}
        </select>
        {loading
          ? <LoaderCircle className="select-field-spinner" size={15} aria-hidden="true" />
          : <ChevronDown size={15} aria-hidden="true" />}
      </span>
      {hint && <small className="select-field-hint" id={hintId}>{hint}</small>}
      {error && <small className="select-field-error" id={errorId} role="alert">{error}</small>}
    </label>
  )
}
