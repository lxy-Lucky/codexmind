import { http } from './client'
import type {
  FileContent, FileNode, IndexProgress,
  Repo, RepoListResponse, SymbolInfo,
} from '@/types'

export const repoApi = {
  register: (name: string, root_path: string, skip_dirs: string[] = []) =>
    http.post<Repo>('/api/repo', { name, root_path, skip_dirs }).then(r => r.data),

  list: () =>
    http.get<RepoListResponse>('/api/repo').then(r => r.data),

  get: (id: string) =>
    http.get<Repo>(`/api/repo/${id}`).then(r => r.data),

  remove: (id: string) =>
    http.delete(`/api/repo/${id}`),

  triggerIndex: (id: string) =>
    http.post<IndexProgress>(`/api/repo/${id}/index`).then(r => r.data),

  indexStatus: (id: string) =>
    http.get<IndexProgress>(`/api/repo/${id}/index/status`).then(r => r.data),

  indexLogs: (id: string, limit = 50) =>
    http.get<{ level: string; message: string; created_at: string }[]>(
      `/api/repo/${id}/index/logs`, { params: { limit } }
    ).then(r => r.data),

  fileTree: (id: string) =>
    http.get<FileNode[]>(`/api/repo/${id}/tree`).then(r => r.data),

  fileContent: (id: string, path: string) =>
    http.get<FileContent>(`/api/repo/${id}/file`, { params: { path } }).then(r => r.data),

  /** 给定文件路径 + 行号，返回光标所在的方法符号（没有则返回 null） */
  symbolAt: (id: string, filePath: string, line: number): Promise<SymbolInfo | null> =>
    http.get<SymbolInfo | null>(`/api/repo/${id}/symbol/at`, {
      params: { file_path: filePath, line },
    }).then(r => r.data).catch(() => null),
}
