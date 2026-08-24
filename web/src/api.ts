// 统一的 API 访问层：除返回 JSON 外还捕获可观测性响应头
// （X-Request-Id 总是存在；X-Trace-Id 仅在后端启用 OTel 时出现）。

export type ApiMeta = { requestId: string | null; otelTraceId: string | null }

export class ApiError extends Error {
  requestId: string | null
  constructor(message: string, requestId: string | null) {
    super(message)
    this.requestId = requestId
  }
}

const extractDetail = (body: unknown): string => {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string') return message
      return JSON.stringify(detail)
    }
  }
  return '请求失败'
}

export async function apiWithMeta<T>(path: string, init?: RequestInit): Promise<{ body: T; meta: ApiMeta }> {
  const response = await fetch(path, init)
  const meta: ApiMeta = {
    requestId: response.headers.get('x-request-id'),
    otelTraceId: response.headers.get('x-trace-id'),
  }
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) throw new ApiError(extractDetail(body), meta.requestId)
  return { body: body as T, meta }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const { body } = await apiWithMeta<T>(path, init)
  return body
}

export const postJson = (payload: unknown, signal?: AbortSignal): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
  signal,
})
