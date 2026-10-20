import type { On } from 'claude-code'
import { expect, test } from 'claude-code/testing'

const ROWS = [
  {
    id: 'aaaaaaaa-1111', title: 'fix the parser', project: '/work/app',
    started: '2026-10-01 10:00', modified: '2026-10-02 10:00',
    prompts: 4, replies: 9, cost: 0, size: 2000, running: false, path: '/x/a.jsonl',
  },
  {
    id: 'bbbbbbbb-2222', title: 'write docs', project: '/work/app',
    started: '2026-10-03 10:00', modified: '2026-10-04 10:00',
    prompts: 2, replies: 3, cost: 0, size: 1000, running: false, path: '/x/b.jsonl',
  },
]

const PANE = {
  plugin: 'session-manager',
  component: 'Pane',
  requestId: 'session-manager',
  props: {
    title: 'Sessions',
    isFocused: false,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const SESSIONS = {
  command: 'sessions',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 120 },
} as const

// Stands for the engine, and for claude-sessions.py behind $.process.run.
function engine(on: On, calls: string[][], current = 'current-session') {
  on('command.register', () => ({ value: { command: 'sessions' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: current }))
  on('process.run', (_, e) => {
    const args = e.argv.slice(2)
    calls.push(args)
    const out =
      args[0] === 'list'
        ? JSON.stringify(ROWS)
        : args[0] === 'preview'
          ? JSON.stringify({ summary: ['Title:    fix the parser'], ai_prompt: 'p' })
          : ''
    return {
      value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`lists, filters, previews and deletes on ${surface}`, async ($, on) => {
    const calls: string[][] = []
    engine(on, calls)
    await $.session.start({ cwd: '/work/app', surface, isInteractive: true })
    await $.command.run(SESSIONS)
    expect(calls[0]).toEqual(['list', '--json'])

    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ key: 'aaaaaaaa-1111' }))?.text).toContain('fix the parser')
    // newest first
    const buttons = (await ui.findAll({ type: 'Button' })).map(b => b.key)
    expect(buttons.indexOf('bbbbbbbb-2222')).toBeLessThan(buttons.indexOf('aaaaaaaa-1111'))

    await ui.input({ key: 'filter', text: 'docs', kind: 'change' })
    expect(await ui.find({ key: 'aaaaaaaa-1111' })).toBeUndefined()
    expect(await ui.find({ key: 'bbbbbbbb-2222' })).toBeDefined()
    await ui.input({ key: 'filter', text: '', kind: 'change' })

    await ui.press({ key: 'aaaaaaaa-1111' })
    expect(calls.at(-1)).toEqual(['preview', '--json', 'aaaaaaaa-1111'])
    expect(await ui.find({ type: 'Text', text: /Title: +fix the parser/ })).toBeDefined()

    await ui.input({ key: 'rename', text: '-new title' })
    expect(calls).toContainEqual(['rename', 'aaaaaaaa-1111', '--', '-new title'])

    await ui.press({ key: 'delete' })
    await ui.press({ key: 'confirm' })
    expect(calls).toContainEqual(['delete', '-y', 'aaaaaaaa-1111'])
    expect(await ui.find({ key: 'filter' })).toBeDefined()
    await ui.unmount()
  })
}

test('marks several sessions and deletes them together', async ($, on) => {
  const calls: string[][] = []
  engine(on, calls)
  await $.session.start({ cwd: '/work/app', surface: 'terminal', isInteractive: true })
  await $.command.run(SESSIONS)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  for (const id of ['aaaaaaaa-1111', 'bbbbbbbb-2222']) {
    await ui.press({ key: id })
    await ui.press({ key: 'mark' })
    await ui.press({ key: 'back' })
  }
  await ui.press({ key: 'delete-marked' })
  await ui.press({ key: 'confirm' })
  expect(calls).toContainEqual(['delete', '-y', 'aaaaaaaa-1111', 'bbbbbbbb-2222'])
  await ui.unmount()
})

test('list hotkeys need a session under the focus ring', async ($, on) => {
  const calls: string[][] = []
  engine(on, calls)
  await $.session.start({ cwd: '/work/app', surface: 'terminal', isInteractive: true })
  await $.command.run(SESSIONS)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  await ui.press({ key: 'mark-row' })
  expect(await ui.find({ type: 'Text', text: /Move to a session first/ })).toBeDefined()
  await ui.press({ key: 'delete-row' })
  expect(await ui.find({ key: 'confirm' })).toBeUndefined()
  expect(calls.some(c => c[0] === 'delete')).toBe(false)
  await ui.unmount()
})

test('the list offers j and k to step through the sessions', async ($, on) => {
  engine(on, [])
  await $.session.start({ cwd: '/work/app', surface: 'terminal', isInteractive: true })
  await $.command.run(SESSIONS)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'next-row' })).toBeDefined()
  expect(await ui.find({ key: 'prev-row' })).toBeDefined()
  await ui.unmount()
})

test('the current session cannot be deleted from the pane', async ($, on) => {
  const calls: string[][] = []
  engine(on, calls, 'aaaaaaaa-1111')
  await $.session.start({ cwd: '/work/app', surface: 'desktop', isInteractive: true })
  await $.command.run(SESSIONS)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ key: 'aaaaaaaa-1111' }))?.text).toContain('(this session)')
  await ui.press({ key: 'aaaaaaaa-1111' })
  expect(await ui.find({ key: 'delete' })).toBeUndefined()
  await ui.unmount()
})

test('mobile draws the list without inputs', async ($, on) => {
  engine(on, [])
  await $.session.start({ cwd: '/work/app', surface: 'mobile', isInteractive: true })
  await $.command.run(SESSIONS)
  const ui = await $.ui.mount({ ...PANE, surface: 'mobile' })
  expect(await ui.find({ key: 'bbbbbbbb-2222' })).toBeDefined()
  await ui.unmount()
})
