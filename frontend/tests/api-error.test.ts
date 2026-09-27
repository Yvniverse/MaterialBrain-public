import { describe, expect, it } from 'vitest'
import { apiErrorMessage } from '../src/utils/apiError'

function error(status?: number, message = '') {
  return status
    ? { response: { status, data: { message } } }
    : { request: {}, message: 'Network Error' }
}

describe('API error presentation', () => {
  it.each([
    [401, '登录状态已过期，请重新登录'],
    [403, '登录状态或安全校验无效，请刷新页面后重试'],
    [422, '输入内容未通过校验，请检查后重试'],
    [500, '服务器处理失败，请稍后重试'],
    [502, '物料服务暂时不可用，请稍后重试'],
    [503, '物料服务暂时不可用，请稍后重试'],
    [504, '物料服务暂时不可用，请稍后重试'],
  ])('maps HTTP %i without disguising the failure', (status, expected) => {
    expect(apiErrorMessage(error(status))).toBe(expected)
  })

  it('keeps a fresh login 401 distinct from a gateway failure', () => {
    expect(apiErrorMessage(error(401), 'login')).toBe('账号或密码错误，或账号暂时锁定')
    expect(apiErrorMessage(error(502), 'login')).toBe('物料服务暂时不可用，请稍后重试')
  })

  it('identifies a missing HTTP response as network/service unavailable', () => {
    expect(apiErrorMessage(error())).toBe('物料服务暂时不可用，请检查网络后重试')
  })
})
