import type { AxiosError } from 'axios'
import type { ApiError } from '../types'

export type ApiErrorContext = 'default' | 'login'

export function apiErrorMessage(error: unknown, context: ApiErrorContext = 'default'): string {
  const response = (error as AxiosError<ApiError>)?.response
  const status = response?.status
  if (!response) return '物料服务暂时不可用，请检查网络后重试'
  if (status === 401) {
    return context === 'login'
      ? '账号或密码错误，或账号暂时锁定'
      : '登录状态已过期，请重新登录'
  }
  if (status === 403) return '登录状态或安全校验无效，请刷新页面后重试'
  if (status === 422) return '输入内容未通过校验，请检查后重试'
  if (status === 429) return '请求过于频繁，请稍候再试'
  if (status === 500) return '服务器处理失败，请稍后重试'
  if (status === 502 || status === 503 || status === 504) {
    return '物料服务暂时不可用，请稍后重试'
  }
  return response.data?.message || '服务请求失败'
}
