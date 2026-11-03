export type LocalCall = {
  id: string
  tool: string
  model: string
  seconds: number
  promptTokens: number
  completionTokens: number
  isEstimated: boolean
  savedUsd: number
}

export type ClaudeTotals = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  usd: number
  turns: number
}

/** The five-hour window's reading, and what Claude had spent when it was taken. */
export type Baseline = { percent: number; usd: number }

export type Headroom = { percentPerUsd: number | null; fiveHourNow: number | null }

export type Summary = { sessions: number; avgSavedUsd: number }

declare module 'claude-code' {
  interface PluginState {
    'local-gpu': {
      isOn: boolean | null
      calls: Shaped<LocalCall[]>
      claude: Shaped<ClaudeTotals>
      baseline: Shaped<Baseline | null>
      headroom: Shaped<Headroom>
      summary: Shaped<Summary>
    }
  }
}
