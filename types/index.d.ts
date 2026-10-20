/** One session as `claude-sessions.py list --json` prints it. */
export type SessionRow = {
  id: string
  title: string
  project: string
  started: string
  modified: string
  prompts: number
  replies: number
  cost: number
  size: number
  running: boolean
  path: string
}

/** What the pane shows: the list, one session, or a delete to confirm. */
export type SessionsView =
  | { kind: 'list' }
  | { kind: 'detail'; id: string; summary: string[]; ai: string }
  | { kind: 'delete'; ids: string[] }

export type SessionsSort = 'modified' | 'created' | 'size' | 'title' | 'messages'

declare module 'claude-code' {
  interface PluginState {
    'session-manager': {
      rows: SessionRow[]
      isGlobal: boolean
      filter: string
      sort: SessionsSort
      marked: string[]
      view: SessionsView
      message: string
      focused: string
    }
  }
}
