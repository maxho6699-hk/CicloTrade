import { Fragment, jsxDEV as reactJsxDEV } from 'react/jsx-dev-runtime'
import type { JSXSource } from 'react/jsx-dev-runtime'
import type { ElementType, Key } from 'react'
import { localizeProps } from './runtime'

export { Fragment }

export function jsxDEV(
  type: ElementType,
  props: unknown,
  key: Key | undefined,
  isStaticChildren: boolean,
  source: JSXSource | undefined,
  self: unknown,
) {
  return reactJsxDEV(type, localizeProps(props), key, isStaticChildren, source, self)
}
