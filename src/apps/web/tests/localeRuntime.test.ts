import assert from 'node:assert/strict'
import test from 'node:test'
import { getFormatLocale, localeSearchText, localizeText, setRuntimeLocale } from '../src/i18n/runtime.ts'

test('uses Taiwan Traditional terminology only for approved UI phrases', () => {
  setRuntimeLocale('zh-Hant')

  assert.equal(localizeText('账户'), '帳戶')
  assert.equal(localizeText('会员订单'), '會員訂單')
  assert.equal(localizeText('最大亏损'), '最大虧損')
  assert.equal(getFormatLocale(), 'zh-Hant-TW')
})

test('does not rewrite dynamic names or identifiers', () => {
  setRuntimeLocale('zh-Hant')

  assert.equal(localizeText('账户策略 Alpha'), '账户策略 Alpha')
  assert.equal(localizeText('王账户'), '王账户')
  assert.equal(localizeText('BUY-AAPL-001'), 'BUY-AAPL-001')
})

test('returns Simplified source phrases in Simplified mode', () => {
  setRuntimeLocale('zh-Hans')

  assert.equal(localizeText('账户'), '账户')
  assert.equal(localizeText('会员订单'), '会员订单')
  assert.equal(getFormatLocale(), 'zh-Hans-CN')
})

test('builds a Simplified and Traditional search index without changing entities', () => {
  assert.match(localeSearchText('自选'), /自选/)
  assert.match(localeSearchText('自选'), /自選/)
  assert.equal(localeSearchText('PLTR'), 'PLTR')
})
