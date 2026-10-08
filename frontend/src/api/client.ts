import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiError } from '../types'
import { apiErrorMessage } from '../utils/apiError'

function cookie(name: string): string {
  const value = document.cookie.split('; ').find((item) => item.startsWith(`${name}=`))
  return value ? decodeURIComponent(value.split('=').slice(1).join('=')) : ''
}

function randomUuid(): string {
  const bytes = new Uint8Array(16)
  const browserCrypto = globalThis.crypto
  if (browserCrypto && typeof browserCrypto.getRandomValues === 'function') {
    browserCrypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export const api = axios.create({ baseURL: '/api/v1', withCredentials: true, timeout: 15000 })
api.interceptors.request.use((config) => {
  const csrf = cookie('materialbrain_csrf')
  if (csrf) config.headers['X-CSRF-Token'] = csrf
  config.headers['X-Request-ID'] = randomUuid()
  return config
})
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiError>) => {
    const path = String(error.config?.url || '')
    const handledByLogin = path.endsWith('/auth/login')
    const silentAuthProbe = path.endsWith('/auth/me') && error.response?.status === 401
    if (!handledByLogin && !silentAuthProbe) {
      ElMessage.error(apiErrorMessage(error))
    }
    return Promise.reject(error)
  },
)

export const uuidKey = () => randomUuid()
