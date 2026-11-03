import { expect, test } from 'claude-code/testing'

const TOOL = 'mcp__plugin_local-gpu_gpu__local_code'
const USAGE =
  'local-gpu-usage: {"tool":"local_code","model":"qwen","mode":"code","seconds":4,' +
  '"prompt_tokens":300,"completion_tokens":1200,"estimated":false,"backend":"local"}'

const PANE = {
  plugin: 'local-gpu',
  component: 'Pane',
  requestId: 'local-gpu',
  props: {
    title: 'Local GPU',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

test('local tools are refused until /local on', async ($, on) => {
  on('command.register', () => ({ value: { command: 'local' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('tool.call', () => ({ result: {}, text: `def f(): ...\n${USAGE}` }))

  const off = await $.tool.call({ tool: TOOL, task: 'write f' })
  expect(off.deny).toContain('/local on')

  await $.command.run({ command: 'local', args: 'on' })
  const on1 = await $.tool.call({ tool: TOOL, task: 'write f' })
  expect(on1.deny).toBeUndefined()

  await $.command.run({ command: 'local', args: 'off' })
  expect((await $.tool.call({ tool: TOOL, task: 'again' })).deny).toContain('/local on')
})

test('other tools are untouched while local tools are off', async ($, on) => {
  on('tool.call', () => ({ result: {} }))

  expect((await $.tool.call({ tool: 'Bash', command: 'ls' })).deny).toBeUndefined()
})

test('a local call shows in the pane on every surface', async ($, on) => {
  on('command.register', () => ({ value: { command: 'local' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('tool.call', () => ({ result: {}, text: `def f(): ...\n${USAGE}` }))
  on('store.get', () => ({ value: undefined }))
  on('session.id', () => ({ value: 's1' }))
  on('clock.now', () => ({ value: 0 }))

  await $.command.run({ command: 'local', args: 'on' })
  await $.tool.call({ tool: TOOL, task: 'write f', context: 'x = 1' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })

    expect((await ui.find({ type: 'Text', text: /Local GPU: ON/ }))?.text).toContain('ON')
    expect((await ui.find({ type: 'Text', text: /local_code x1/ }))?.text).toContain('local_code x1')
    await ui.unmount()
  }
})
