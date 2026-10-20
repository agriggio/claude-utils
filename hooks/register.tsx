// Copyright (c) 2026 Alberto Griggio
// SPDX-License-Identifier: MIT
//
// A Claude Code mod: a pane to browse and tidy up sessions. All the work on
// the transcripts is done by claude-sessions.py, which this module runs.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { SessionRow, SessionsSort, SessionsView } from '../types'

const PANE = 'session-manager'
const TITLE = 'Sessions'

const rows = atom({ plugin: 'session-manager', key: 'rows' } as const, [])
const isGlobal = atom({ plugin: 'session-manager', key: 'isGlobal' } as const, false)
const filter = atom({ plugin: 'session-manager', key: 'filter' } as const, '')
const sort = atom({ plugin: 'session-manager', key: 'sort' } as const, 'modified')
const marked = atom({ plugin: 'session-manager', key: 'marked' } as const, [])
const view = atom({ plugin: 'session-manager', key: 'view' } as const, { kind: 'list' })
const message = atom({ plugin: 'session-manager', key: 'message' } as const, '')

const SORTS: { value: SessionsSort; label: string }[] = [
  { value: 'modified', label: 'last modified' },
  { value: 'created', label: 'created' },
  { value: 'size', label: 'size' },
  { value: 'title', label: 'title' },
  { value: 'messages', label: 'messages' },
]

type $ = EngineInterface

let python = 'python3'

export const register: Register = (on, options) => {
  python = String(options.python ?? 'python3')

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'sessions',
      description: 'Browse, preview, rename and delete Claude Code sessions in a pane',
    })
    return next(e)
  })

  on('command.run', { command: 'sessions' }, async $ => {
    await openPane($)
    return { text: 'Sessions pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const ui = $.ui.resolve(e)
    const { Box, Text, Button } = ui
    const Input = 'Input' in ui ? ui.Input : undefined
    const Select = 'Select' in ui ? ui.Select : undefined
    const v = await read($, view)
    const msg = await read($, message)
    const current = await $.session.id()
    const list = await read($, rows)
    const byId = new Map(list.map(s => [s.id, s]))
    const note = msg ? <Text color="yellow">{msg}</Text> : null

    if (v.kind === 'delete') {
      const victims = v.ids.map(id => byId.get(id)).filter(s => s !== undefined)
      return (
        <Box flexDirection="column">
          <Text bold>Delete {String(victims.length)} session(s)? This is permanent.</Text>
          {victims.map(s => (
            <Text>
              {s.id.slice(0, 8)} {s.title}
            </Text>
          ))}
          <Box flexDirection="row" gap={1}>
            <Button key="confirm" variant="primary" onPress={() => void remove($, v.ids)}>
              Delete
            </Button>
            <Button key="cancel" onPress={() => void goto($, { kind: 'list' })}>
              Cancel
            </Button>
          </Box>
          {note}
        </Box>
      )
    }

    if (v.kind === 'detail') {
      const s = byId.get(v.id)
      const isMarked = (await read($, marked)).includes(v.id)
      return (
        <Box flexDirection="column">
          <Box flexDirection="row" gap={1}>
            <Button key="back" hotkey="b" onPress={() => void goto($, { kind: 'list' })}>
              Back
            </Button>
            <Button key="mark" hotkey="m" onPress={() => void toggle($, v.id)}>
              {isMarked ? 'Unmark' : 'Mark'}
            </Button>
            <Button key="ai" hotkey="a" onPress={() => void summarize($, v.id)}>
              AI summary
            </Button>
            {v.id !== current && (
              <Button key="delete" hotkey="d" onPress={() => void goto($, { kind: 'delete', ids: [v.id] })}>
                Delete
              </Button>
            )}
          </Box>
          {Input && (
            <Input
              key="rename"
              label="Rename: "
              value={s?.title ?? ''}
              submitLabel="rename"
              onSubmit={title => void rename($, v.id, title)}
            />
          )}
          {note}
          {v.summary.map(line => (
            <Text>{line || ' '}</Text>
          ))}
          {v.ai && <Text bold>AI summary</Text>}
          {v.ai && <Text>{v.ai}</Text>}
        </Box>
      )
    }

    const query = await read($, filter)
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
    const order = await read($, sort)
    const marks = await read($, marked)
    const global = await read($, isGlobal)
    const shown = sorted(
      list.filter(s => {
        const hay = `${s.title} ${s.project} ${s.id}`.toLowerCase()
        return terms.every(t => hay.includes(t))
      }),
      order,
    )
    const width = e.props.bodyColumns ?? 60

    return (
      <Box flexDirection="column">
        {Input && (
          <Input
            key="filter"
            label="Filter: "
            placeholder="title, project or id"
            value={query}
            onInput={text => void update($, filter, () => text)}
            onSubmit={text => void update($, filter, () => text)}
          />
        )}
        <Box flexDirection="row" gap={1}>
          {Select && (
            <Select
              key="sort"
              label="Sort: "
              options={SORTS}
              value={order}
              onSelect={value => void update($, sort, () => value as SessionsSort)}
            />
          )}
          <Button key="scope" hotkey="t" onPress={() => void toggleScope($)}>
            {global ? 'All projects' : 'This project'}
          </Button>
          <Button key="reload" hotkey="r" onPress={() => void reload($)}>
            Reload
          </Button>
          {marks.length > 0 && (
            <Button key="delete-marked" onPress={() => void goto($, { kind: 'delete', ids: marks })}>
              Delete {String(marks.length)} marked
            </Button>
          )}
        </Box>
        {note}
        <Text dimColor>
          {String(shown.length)} of {String(list.length)} session(s); ● running, * marked
        </Text>
        {shown.map(s => (
          <Button key={s.id} plain onPress={() => void show($, s.id)}>
            {s.running ? '● ' : '  '}
            {marks.includes(s.id) ? '* ' : '  '}
            {clip(s.title, Math.max(10, width - 30))}
            <Text dimColor>
              {' '}
              {s.modified} {String(s.prompts)} msg{s.id === current ? ' (this session)' : ''}
              {global ? ` ${s.project}` : ''}
            </Text>
          </Button>
        ))}
      </Box>
    )
  })
}

// Runs claude-sessions.py in the session's directory, so that its default
// scope is the session's project; -g makes it all projects.
async function script($: $, args: string[], timeoutMs?: number) {
  const scope = (await read($, isGlobal)) ? ['-g'] : []
  const argv = [python, `${$.plugin.root}/claude-sessions.py`, ...scope, ...args]
  try {
    const r = await $.process.run(argv, { timeoutMs })
    const err = r.stderr.split('\n').filter(l => l && !l.startsWith('note:'))
    return { ok: r.exitCode === 0, stdout: r.stdout, error: err.join(' ') }
  } catch (x) {
    return { ok: false, stdout: '', error: `cannot run ${python}: ${String(x)}` }
  }
}

async function reload($: $) {
  const r = await script($, ['list', '--json'])
  if (!r.ok) return say($, r.error || 'listing sessions failed')
  const list: SessionRow[] = JSON.parse(r.stdout)
  await update($, rows, () => list)
  const ids = new Set(list.map(s => s.id))
  await update($, marked, m => m.filter(id => ids.has(id)))
}

async function say($: $, text: string) {
  await update($, message, () => text)
}

async function goto($: $, next: SessionsView) {
  await update($, view, () => next)
}

async function openPane($: $) {
  await say($, '')
  await goto($, { kind: 'list' })
  await $.ui.open({ id: PANE, title: TITLE })
  await reload($)
}

async function toggleScope($: $) {
  await update($, isGlobal, g => !g)
  await update($, marked, () => [])
  await reload($)
}

async function toggle($: $, id: string) {
  await update($, marked, m => (m.includes(id) ? m.filter(x => x !== id) : [...m, id]))
}

async function show($: $, id: string) {
  await say($, '')
  const r = await script($, ['preview', '--json', id])
  if (!r.ok) return say($, r.error || 'preview failed')
  const { summary } = JSON.parse(r.stdout)
  await goto($, { kind: 'detail', id, summary, ai: '' })
}

// The script's --ai shells out to `claude -p`; here the session's own client
// answers the same prompt.
async function summarize($: $, id: string) {
  const r = await script($, ['preview', '--json', id])
  if (!r.ok) return say($, r.error || 'preview failed')
  const { summary, ai_prompt } = JSON.parse(r.stdout)
  await goto($, { kind: 'detail', id, summary, ai: '…' })
  const done = await $.model.complete({ model: 'haiku', prompt: ai_prompt, maxTokens: 400 })
  const ai = done.isAnswered ? done.text.trim() : `(AI summary failed: ${done.reason})`
  await update($, view, v => (v.kind === 'detail' && v.id === id ? { ...v, ai } : v))
}

async function rename($: $, id: string, title: string) {
  if (!title.trim()) return
  const r = await script($, ['rename', id, '--', title])
  await say($, r.ok ? `Renamed to “${title.trim()}”.` : r.error || 'rename failed')
  await reload($)
}

async function remove($: $, ids: string[]) {
  const r = await script($, ['delete', '-y', ...ids])
  await say($, r.ok ? `Deleted ${ids.length} session(s).` : r.error || 'delete failed')
  await goto($, { kind: 'list' })
  await reload($)
}

function sorted(list: SessionRow[], key: SessionsSort) {
  const by: Record<SessionsSort, (a: SessionRow, b: SessionRow) => number> = {
    modified: (a, b) => b.modified.localeCompare(a.modified),
    created: (a, b) => b.started.localeCompare(a.started),
    size: (a, b) => b.size - a.size,
    title: (a, b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()),
    messages: (a, b) => b.prompts - a.prompts,
  }
  return [...list].sort(by[key])
}

function clip(text: string, n: number) {
  return text.length > n ? text.slice(0, n - 1) + '…' : text
}
