import { Check, ChevronDown, LoaderCircle } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface SelectOption { value: string; label: string; disabled?: boolean }
export interface SelectFieldProps {
  label: string; options: SelectOption[]; value: string; onValueChange: (value: string) => void
  hint?: string; error?: string; invalid?: boolean; loading?: boolean; readOnly?: boolean
  className?: string; id?: string; disabled?: boolean; ariaLabel?: string
}

export interface ListboxPlacement { top: number; left: number; width: number; maxHeight: number; placement: 'top' | 'bottom' }

const VIEWPORT_GUTTER = 8
const LISTBOX_MAX_HEIGHT = 260
const OPTION_HEIGHT = 44

function firstEnabledOptionIndex(options: SelectOption[]) {
  return options.findIndex((option) => !option.disabled)
}

function nextEnabledOptionIndex(options: SelectOption[], start: number, direction: 1 | -1) {
  if (!options.length || firstEnabledOptionIndex(options) === -1) return -1
  for (let offset = 1; offset <= options.length; offset += 1) {
    const index = (start + direction * offset + options.length) % options.length
    if (!options[index]?.disabled) return index
  }
  return -1
}

function resolveListboxPlacement(rect: DOMRect, optionCount: number, viewportWidth = window.innerWidth, viewportHeight = window.innerHeight): ListboxPlacement {
  const desiredHeight = Math.min(LISTBOX_MAX_HEIGHT, Math.max(OPTION_HEIGHT, optionCount * OPTION_HEIGHT + 8))
  const below = viewportHeight - rect.bottom - VIEWPORT_GUTTER
  const above = rect.top - VIEWPORT_GUTTER
  const placement = below >= Math.min(desiredHeight, above) || above < OPTION_HEIGHT ? 'bottom' : 'top'
  const maxHeight = Math.max(OPTION_HEIGHT, Math.min(desiredHeight, placement === 'bottom' ? below : above))
  const width = Math.max(0, Math.min(rect.width, viewportWidth - VIEWPORT_GUTTER * 2))
  const left = Math.max(VIEWPORT_GUTTER, Math.min(rect.left, viewportWidth - width - VIEWPORT_GUTTER))
  const top = placement === 'bottom' ? rect.bottom + 4 : Math.max(VIEWPORT_GUTTER, rect.top - maxHeight - 4)
  return { top, left, width, maxHeight, placement }
}

export function SelectField({ label, options, value, onValueChange, hint, error, invalid = false, loading = false, readOnly = false, className = '', id, disabled = false, ariaLabel }: SelectFieldProps) {
  const generatedId = useId()
  const fieldId = id ?? `select-${generatedId}`
  const labelId = `${fieldId}-label`
  const listboxId = `${fieldId}-listbox`
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [placement, setPlacement] = useState<ListboxPlacement | null>(null)
  const rootRef = useRef<HTMLSpanElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listboxRef = useRef<HTMLDivElement>(null)
  const isDisabled = disabled || loading
  const selectedIndex = options.findIndex((option) => option.value === value)
  const selected = options[selectedIndex] ?? options[0]
  const optionId = useCallback((index: number) => `${listboxId}-option-${index}`, [listboxId])

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false)
    setActiveIndex(-1)
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus())
  }, [])
  const updatePlacement = useCallback(() => {
    if (triggerRef.current) setPlacement(resolveListboxPlacement(triggerRef.current.getBoundingClientRect(), options.length))
  }, [options.length])
  const openMenu = useCallback((active = selectedIndex) => {
    if (isDisabled || readOnly || firstEnabledOptionIndex(options) === -1) return
    setActiveIndex(active >= 0 && !options[active]?.disabled ? active : firstEnabledOptionIndex(options))
    setOpen(true)
  }, [isDisabled, options, readOnly, selectedIndex])
  const choose = useCallback((index: number) => {
    const option = options[index]
    if (!option || option.disabled || readOnly) return
    onValueChange(option.value)
    closeMenu(true)
  }, [closeMenu, onValueChange, options, readOnly])

  useLayoutEffect(() => {
    if (!open) return
    updatePlacement()
    const reposition = () => updatePlacement()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    const observer = new ResizeObserver(reposition)
    if (triggerRef.current) observer.observe(triggerRef.current)
    return () => { window.removeEventListener('resize', reposition); window.removeEventListener('scroll', reposition, true); observer.disconnect() }
  }, [open, updatePlacement])
  useEffect(() => {
    if (open && activeIndex >= 0) document.getElementById(optionId(activeIndex))?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open, optionId])
  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (!rootRef.current?.contains(target) && !listboxRef.current?.contains(target)) closeMenu()
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer)
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer)
  }, [closeMenu])
  useEffect(() => { if (isDisabled || readOnly) closeMenu() }, [closeMenu, isDisabled, readOnly])

  const moveActive = (direction: 1 | -1) => {
    if (!open) return openMenu(selectedIndex)
    const next = nextEnabledOptionIndex(options, activeIndex >= 0 ? activeIndex : selectedIndex, direction)
    if (next >= 0) setActiveIndex(next)
  }
  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (isDisabled || readOnly) return
    if (event.key === 'ArrowDown') { event.preventDefault(); moveActive(1); return }
    if (event.key === 'ArrowUp') { event.preventDefault(); moveActive(-1); return }
    if (event.key === 'Home') { event.preventDefault(); openMenu(firstEnabledOptionIndex(options)); return }
    if (event.key === 'End') { event.preventDefault(); const offset = [...options].reverse().findIndex((option) => !option.disabled); openMenu(offset < 0 ? -1 : options.length - 1 - offset); return }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (open && activeIndex >= 0) choose(activeIndex); else openMenu(selectedIndex); return }
    if (event.key === 'Escape' && open) { event.preventDefault(); closeMenu(true); return }
    if (event.key === 'Tab' && open) closeMenu()
  }
  const menu = open && placement && typeof document !== 'undefined' ? createPortal(
    <div ref={listboxRef} id={listboxId} className="select-field-listbox" role="listbox" aria-labelledby={ariaLabel ? undefined : labelId} aria-label={ariaLabel} data-placement={placement.placement} style={{ top: placement.top, left: placement.left, width: placement.width, maxHeight: placement.maxHeight }}>
      {options.map((option, index) => <button id={optionId(index)} role="option" type="button" tabIndex={-1} aria-selected={option.value === value} aria-disabled={option.disabled || undefined} data-active={index === activeIndex || undefined} disabled={option.disabled} key={option.value} onMouseMove={() => !option.disabled && setActiveIndex(index)} onClick={() => choose(index)}><span>{option.label}</span>{option.value === value && <Check size={15} aria-label="已选择" />}</button>)}
    </div>, document.body,
  ) : null

  return <label className={`select-field ${className}`.trim()} data-disabled={isDisabled || undefined} data-invalid={invalid || Boolean(error) || undefined} data-loading={loading || undefined} data-readonly={readOnly || undefined} htmlFor={fieldId}>
    <span id={labelId} className="select-field-label">{label}</span>
    <span className="select-field-control" ref={rootRef}>
      <button ref={triggerRef} id={fieldId} type="button" role="combobox" aria-label={ariaLabel} aria-labelledby={ariaLabel ? undefined : labelId} aria-controls={open ? listboxId : undefined} aria-activedescendant={open && activeIndex >= 0 ? optionId(activeIndex) : undefined} aria-expanded={open} aria-haspopup="listbox" aria-invalid={invalid || Boolean(error) || undefined} aria-busy={loading || undefined} aria-readonly={readOnly || undefined} disabled={isDisabled} onClick={() => open ? closeMenu() : openMenu(selectedIndex)} onKeyDown={handleKeyDown}>
        <span>{selected?.label ?? '请选择'}</span>{loading ? <LoaderCircle className="select-field-spinner" size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
      </button>
    </span>
    {menu}
    {hint && <small className="select-field-hint">{hint}</small>}{error && <small className="select-field-error" role="alert">{error}</small>}
  </label>
}
