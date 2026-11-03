import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ClaudeTotals, LocalCall } from '../types'

const PANE = 'local-gpu'
const USAGE_PREFIX = 'local-gpu-usage: '
const LOCAL_TOOL =
  /^mcp__.*local[-_]gpu.*__(local_chat|local_code|generate_image|gpu_status|list_models|set_mode)$/
const HISTORY_MAX = 200
// A calibration needs at least this much movement in the five-hour window,
// which reports one decimal and counts every session of the account.
const MIN_WINDOW_MOVE = 1
const MIN_CALIBRATION_USD = 0.05

const isOn = atom({ plugin: 'local-gpu', key: 'isOn' } as const, null)
const calls = atom({ plugin: 'local-gpu', key: 'calls' } as const, [], {
  shape: 'calls-1',
})
const claude = atom(
  { plugin: 'local-gpu', key: 'claude' } as const,
  { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, usd: 0, turns: 0 },
  { shape: 'claude-1' },
)
const baseline = atom({ plugin: 'local-gpu', key: 'baseline' } as const, null, {
  shape: 'baseline-1',
})
const headroom = atom(
  { plugin: 'local-gpu', key: 'headroom' } as const,
  { percentPerUsd: null, fiveHourNow: null },
  { shape: 'headroom-1' },
)
const summary = atom(
  { plugin: 'local-gpu', key: 'summary' } as const,
  { sessions: 0, avgSavedUsd: 0 },
  { shape: 'summary-1' },
)

type Usage = {
  tool: string
  model: string
  seconds: number
  prompt_tokens?: number
  completion_tokens?: number
  estimated?: boolean
}

type HistoryRow = {
  id: string
  day: string
  savedUsd: number
  localTokens: number
  claudeUsd: number
}

const tokensOf = (text: string) => Math.ceil(text.length / 4)
const dollars = (n: number) => `$${n.toFixed(n !== 0 && Math.abs(n) < 0.1 ? 3 : 2)}`
const thousands = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

const parseUsage = (text: string | undefined): Usage | null => {
  const line = (text ?? '')
    .split('\n')
    .find(row => row.startsWith(USAGE_PREFIX))

  if (line === undefined) {
    return null
  }

  try {
    return JSON.parse(line.slice(USAGE_PREFIX.length)) as Usage
  } catch {
    return null
  }
}

const bar = (share: number, width = 20) => {
  const full = Math.round(Math.min(1, Math.max(0, share)) * width)

  return '█'.repeat(full) + '░'.repeat(width - full)
}

async function enabled($: EngineInterface, defaultOn: boolean) {
  return (await read($, isOn)) ?? defaultOn
}

async function setOn($: EngineInterface, value: boolean) {
  await update($, isOn, () => value)
}

async function persist($: EngineInterface) {
  const list = await read($, calls)

  if (list.length === 0) {
    return
  }

  const totals = await read($, claude)
  const id = await $.session.id()
  const day = new Date(await $.clock.now()).toISOString().slice(0, 10)
  const row: HistoryRow = {
    id,
    day,
    savedUsd: list.reduce((sum, call) => sum + call.savedUsd, 0),
    localTokens: list.reduce((sum, call) => sum + call.completionTokens, 0),
    claudeUsd: totals.usd,
  }
  const stored = ((await $.store.get('history')) ?? []) as HistoryRow[]
  const history = [...stored.filter(one => one.id !== id), row].slice(-HISTORY_MAX)
  await $.store.set('history', history)
  await update($, summary, () => ({
    sessions: history.length,
    avgSavedUsd: history.reduce((sum, one) => sum + one.savedUsd, 0) / history.length,
  }))
}

async function calibrate($: EngineInterface, totals: ClaudeTotals) {
  const five = (await $.session.usage()).rateLimits.find(
    limit => limit.kind === 'five_hour',
  )

  if (five === undefined) {
    return
  }

  const start = await read($, baseline)

  if (start === null) {
    await update($, baseline, () => ({ percent: five.percentUsed, usd: totals.usd }))
    await update($, headroom, () => ({ percentPerUsd: null, fiveHourNow: five.percentUsed }))

    return
  }

  const moved = five.percentUsed - start.percent
  const spent = totals.usd - start.usd
  const isCalibrated = moved >= MIN_WINDOW_MOVE && spent >= MIN_CALIBRATION_USD
  await update($, headroom, () => ({
    percentPerUsd: isCalibrated ? moved / spent : null,
    fiveHourNow: five.percentUsed,
  }))
}

export const register: Register = (on, options) => {
  const inPrice = Number(options.inputPricePerMTok ?? 3)
  const outPrice = Number(options.outputPricePerMTok ?? 15)
  const defaultOn = options.defaultOn === true

  const claudeUsd = (u: {
    input_tokens: number
    output_tokens: number
    cache_read_input_tokens: number
    cache_creation_input_tokens: number
  }) =>
    (u.input_tokens * inPrice +
      u.output_tokens * outPrice +
      u.cache_read_input_tokens * inPrice * 0.1 +
      u.cache_creation_input_tokens * inPrice * 1.25) /
    1e6

  // What Claude would have spent writing the answer itself, less what delegating
  // cost it: the arguments it wrote and the result it read back.
  const savedUsd = (usage: Usage, argsTokens: number, resultTokens: number) =>
    ((usage.completion_tokens ?? 0) * outPrice -
      argsTokens * outPrice -
      resultTokens * inPrice) /
    1e6

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'local',
      description: 'Local GPU tools: /local on | off | stats | pane',
    })
    const summaryNow = await read($, summary)

    if (summaryNow.sessions === 0) {
      const stored = (await $.store.get('history')) as HistoryRow[] | undefined

      if (stored !== undefined && stored.length > 0) {
        await update($, summary, () => ({
          sessions: stored.length,
          avgSavedUsd: stored.reduce((sum, one) => sum + one.savedUsd, 0) / stored.length,
        }))
      }
    }

    if (await enabled($, defaultOn)) {
      void $.ui.open({ id: PANE, title: 'Local GPU' })
    }

    return next(e)
  })

  on('command.run', { command: 'local' }, async ($, e) => {
    const word = e.args.trim().toLowerCase()

    if (word === 'on' || word === 'off') {
      await setOn($, word === 'on')

      if (word === 'on') {
        await $.ui.open({ id: PANE, title: 'Local GPU' })
      }

      return {
        text:
          word === 'on'
            ? 'Local GPU tools are on for this session.'
            : 'Local GPU tools are off for this session.',
      }
    }

    if (word === 'stats') {
      const list = await read($, calls)
      const totals = await read($, summary)
      const saved = list.reduce((sum, call) => sum + call.savedUsd, 0)

      return {
        text:
          `This session: ${list.length} local calls, about ${dollars(saved)} saved (estimate). ` +
          (totals.sessions > 0
            ? `Across ${totals.sessions} sessions: about ${dollars(totals.avgSavedUsd)} saved on average.`
            : 'No earlier sessions recorded.'),
      }
    }

    if (word === 'pane') {
      await $.ui.open({ id: PANE, title: 'Local GPU' })

      return { text: 'Local GPU pane opened.' }
    }

    return {
      text: `Local GPU tools are ${(await enabled($, defaultOn)) ? 'on' : 'off'}. Use /local on, /local off, /local stats or /local pane.`,
    }
  })

  on('tool.call', async ($, e, next) => {
    if (!LOCAL_TOOL.test(e.tool)) {
      return next(e)
    }

    if (!(await enabled($, defaultOn))) {
      return {
        deny: 'local-gpu: the local GPU tools are off for this session. Ask the user to run /local on.',
      }
    }

    const ran = await next(e)

    if (ran.deny !== undefined || ran.isError === true) {
      return ran
    }

    const usage = parseUsage(ran.text)

    if (usage === null) {
      return ran
    }

    const { tool: _tool, tool_use_id: _id, ...args } = e
    const isText = usage.tool === 'local_chat' || usage.tool === 'local_code'
    const call: LocalCall = {
      id: e.tool_use_id,
      tool: usage.tool,
      model: usage.model,
      seconds: usage.seconds,
      promptTokens: usage.prompt_tokens ?? 0,
      completionTokens: usage.completion_tokens ?? 0,
      isEstimated: usage.estimated === true,
      savedUsd: isText
        ? savedUsd(
            usage,
            tokensOf(JSON.stringify(args)),
            tokensOf((ran.text ?? '').replace(/local-gpu-usage: .*$/m, '')),
          )
        : 0,
    }
    await update($, calls, list => [...list, call].slice(-200))
    await persist($)

    return ran
  }).catch(($, e, next) =>
    next.called
      ? next(e)
      : { deny: `${$.plugin.name}: could not check whether local GPU tools are on.` },
  )

  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)

    if (!(await enabled($, defaultOn))) {
      return composed
    }

    return {
      sections: [
        ...composed.sections,
        {
          id: 'local-gpu:guidance',
          scope: 'session',
          text:
            'Local GPU tools (the local-gpu MCP server) are on for this session. ' +
            'Delegate self-contained, low-risk work (boilerplate, summaries, drafts, bulk text) to ' +
            'local_code or local_chat, passing every file or snippet the task needs because the local ' +
            'models cannot read the repository, and review their output before using it. Use ' +
            'generate_image for images. Keep work that needs repo-wide reasoning or careful ' +
            'correctness for yourself. A call may take several seconds while the GPU swaps models.',
        },
      ],
    }
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined && e.usage !== undefined) {
      const usd = claudeUsd(e.usage)
      const totals = await update($, claude, c => ({
        input: c.input + e.usage!.input_tokens,
        output: c.output + e.usage!.output_tokens,
        cacheRead: c.cacheRead + e.usage!.cache_read_input_tokens,
        cacheWrite: c.cacheWrite + e.usage!.cache_creation_input_tokens,
        usd: c.usd + usd,
        turns: c.turns + 1,
      }))
      await calibrate($, totals)
      await persist($)
    }

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const isEnabled = await enabled($, defaultOn)
    const list = await read($, calls)
    const totals = await read($, claude)
    const limit = await read($, headroom)
    const history = await read($, summary)

    const localOut = list.reduce((sum, call) => sum + call.completionTokens, 0)
    const share = totals.output + localOut > 0 ? localOut / (totals.output + localOut) : 0
    const saved = list.reduce((sum, call) => sum + call.savedUsd, 0)
    const gpuSeconds = list.reduce((sum, call) => sum + call.seconds, 0)
    const byTool = new Map<string, number>()

    for (const call of list) {
      byTool.set(call.tool, (byTool.get(call.tool) ?? 0) + 1)
    }

    const hasEstimate = list.some(call => call.isEstimated)

    return (
      <Box flexDirection="column">
        <Box>
          <Text bold>Local GPU: {isEnabled ? 'ON' : 'off'} </Text>
          <Button
            key="toggle"
            label={isEnabled ? 'Turn off' : 'Turn on'}
            onPress={() => setOn($, !isEnabled)}
          />
        </Box>
        <Text dimColor>Generated tokens this session</Text>
        <Text>
          Claude {bar(1 - share)} {thousands(totals.output)} ({dollars(totals.usd)} API-equiv)
        </Text>
        <Text>
          Local  {bar(share)} {thousands(localOut)}
          {hasEstimate ? ' (some estimated)' : ''}
        </Text>
        <Text>
          Saved about {dollars(saved)} (estimate)
          {limit.percentPerUsd !== null
            ? `, roughly ${(saved * limit.percentPerUsd).toFixed(1)}% of your 5h window`
            : ', window headroom still calibrating'}
        </Text>
        {limit.fiveHourNow !== null && (
          <Text dimColor>5h window now at {limit.fiveHourNow.toFixed(1)}%</Text>
        )}
        <Text dimColor>
          {list.length === 0
            ? 'No local calls yet.'
            : `${[...byTool].map(([tool, n]) => `${tool} x${n}`).join(', ')}; ${gpuSeconds.toFixed(0)}s GPU time`}
        </Text>
        <Text dimColor>
          {history.sessions > 0
            ? `Average saved per session: ${dollars(history.avgSavedUsd)} over ${history.sessions} sessions`
            : 'Averages appear after your first session with local calls.'}
        </Text>
      </Box>
    )
  })
}
