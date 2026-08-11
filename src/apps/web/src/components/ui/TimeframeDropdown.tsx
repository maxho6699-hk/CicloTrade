import { Check, ChevronDown } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

export interface TimeframeChoice {
  value: string
  label: string
  group: string
}

interface TimeframeDropdownProps {
  value: string
  options: readonly TimeframeChoice[]
  ariaLabel: string
  onChange: (value: string) => void
}

interface MenuPosition {
  left: number
  top: number
  maxHeight: number
  placement: 'top' | 'bottom'
}

const MENU_WIDTH = 180
const MENU_MAX_HEIGHT = 320
const VIEWPORT_GAP = 8

/** A small, native-menu-free listbox for dense chart toolbars. */
export function TimeframeDropdown({ value, options, ariaLabel, onChange }: TimeframeDropdownProps) {
  const [open, setOpen] = useState(false)
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const pendingFocusIndex = useRef<number | null>(null)
  const listId = useId()
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value))
  const selected = options[selectedIndex] ?? options[0]
  const groups = [...new Set(options.map((option) => option.group))]

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const measuredHeight = Math.min(menuRef.current?.scrollHeight ?? MENU_MAX_HEIGHT, MENU_MAX_HEIGHT)
    const roomBelow = window.innerHeight - rect.bottom - VIEWPORT_GAP - 4
    const roomAbove = rect.top - VIEWPORT_GAP - 4
    const placement = roomBelow < Math.min(measuredHeight, 180) && roomAbove > roomBelow ? 'top' : 'bottom'
    const maxHeight = Math.max(96, Math.min(measuredHeight, placement === 'top' ? roomAbove : roomBelow))
    const top = placement === 'top'
      ? Math.max(VIEWPORT_GAP, rect.top - maxHeight - 4)
      : Math.min(window.innerHeight - VIEWPORT_GAP - maxHeight, rect.bottom + 4)
    const left = Math.max(VIEWPORT_GAP, Math.min(rect.left, window.innerWidth - VIEWPORT_GAP - MENU_WIDTH))
    setMenuPosition({ left, top, maxHeight, placement })
  }, [])

  const focusOption = useCallback((index: number) => {
    const next = Math.min(options.length - 1, Math.max(0, index))
    const option = optionRefs.current[next]
    option?.focus({ preventScroll: true })
    option?.scrollIntoView({ block: 'nearest' })
  }, [options.length])

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false)
    setMenuPosition(null)
    pendingFocusIndex.current = null
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus({ preventScroll: true }))
  }, [])

  const openMenu = () => {
    pendingFocusIndex.current = selectedIndex
    setOpen(true)
  }

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
    const frame = requestAnimationFrame(() => {
      updatePosition()
      focusOption(pendingFocusIndex.current ?? selectedIndex)
      pendingFocusIndex.current = null
    })
    return () => cancelAnimationFrame(frame)
  }, [focusOption, open, selectedIndex, updatePosition])

  useEffect(() => {
    if (!open) return
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) closeMenu()
    }
    const reposition = () => updatePosition()
    window.addEventListener('pointerdown', closeOnOutside)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('pointerdown', closeOnOutside)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [closeMenu, open, updatePosition])

  const select = (next: TimeframeChoice) => {
    onChange(next.value)
    closeMenu(true)
  }

  const handleListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = optionRefs.current.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(true); return }
    if (event.key === 'Home') { event.preventDefault(); focusOption(0); return }
    if (event.key === 'End') { event.preventDefault(); focusOption(options.length - 1); return }
    if (event.key === 'ArrowDown') { event.preventDefault(); focusOption(index < 0 ? selectedIndex : index + 1); return }
    if (event.key === 'ArrowUp') { event.preventDefault(); focusOption(index < 0 ? selectedIndex : index - 1); return }
    if ((event.key === 'Enter' || event.key === ' ') && index >= 0) { event.preventDefault(); select(options[index]) }
  }

  let optionIndex = -1
  return <div className="timeframe-dropdown" ref={rootRef}>
    <button
      ref={triggerRef}
      className="timeframe-dropdown-trigger"
      type="button"
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? listId : undefined}
      onClick={() => open ? closeMenu(true) : openMenu()}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) { event.preventDefault(); closeMenu(true) }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
          event.preventDefault()
          openMenu()
        }
      }}
    ><span>{selected?.label ?? value}</span><ChevronDown size={13} aria-hidden="true" /></button>
    {open && createPortal(<div
      className="timeframe-dropdown-menu"
      data-placement={menuPosition?.placement}
      id={listId}
      ref={menuRef}
      role="listbox"
      aria-label={ariaLabel}
      onKeyDown={handleListKeyDown}
      style={{
        position: 'fixed',
        visibility: menuPosition ? 'visible' : 'hidden',
        top: menuPosition?.top ?? 0,
        left: menuPosition?.left ?? 0,
        maxHeight: menuPosition?.maxHeight ?? MENU_MAX_HEIGHT,
      }}
    >
      {groups.map((group) => <div className="timeframe-dropdown-group" role="group" aria-label={group} key={group}>
        <span>{group}</span>
        {options.filter((option) => option.group === group).map((option) => {
          optionIndex += 1
          const index = optionIndex
          return <button
            ref={(node) => { optionRefs.current[index] = node }}
            type="button"
            role="option"
            aria-selected={option.value === value}
            className={option.value === value ? 'is-selected' : ''}
            onClick={() => select(option)}
            key={option.value}
          ><span>{option.label}</span>{option.value === value && <Check size={13} aria-hidden="true" />}</button>
        })}
      </div>)}
    </div>, document.body)}
  </div>
}
