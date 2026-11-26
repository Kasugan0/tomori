import type { PaginatedResponse } from '@/shared/types'

const LIST_PAGE_SIZE = 100

/**
 * 后端列表端点 page_size 上限统一为 100（各路由 Query(le=100)）。
 * 需要全量数据的视图（调度看板队列、维修待办、metadata 选项源）
 * 必须循环翻页拉取，仅取首页会让旧数据不可见。
 */
export async function fetchAllPages<T>(
  fetchPage: (page: number, pageSize: number) => Promise<PaginatedResponse<T>>,
): Promise<T[]> {
  const first = await fetchPage(1, LIST_PAGE_SIZE)
  if (first.items.length >= first.total) {
    return first.items
  }

  const remainingPages = Math.ceil((first.total - first.items.length) / LIST_PAGE_SIZE)
  const rest = await Promise.all(
    Array.from({ length: remainingPages }, (_, i) => fetchPage(i + 2, LIST_PAGE_SIZE)),
  )
  return [...first.items, ...rest.flatMap((res) => res.items)]
}
