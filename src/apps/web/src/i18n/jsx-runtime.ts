import { Fragment, jsx as reactJsx, jsxs as reactJsxs } from 'react/jsx-runtime'
import type { ElementType, Key } from 'react'
import { localizeProps } from './runtime'

export { Fragment }

export function jsx(type: ElementType, props: unknown, key?: Key) {
  return reactJsx(type, localizeProps(props), key)
}

export function jsxs(type: ElementType, props: unknown, key?: Key) {
  return reactJsxs(type, localizeProps(props), key)
}
