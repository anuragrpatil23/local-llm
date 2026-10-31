import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

const DEFAULT_CONTRACT_SOURCE = '/Volumes/Macintosh-HD-Personal/PersonalGit/Thinking-Space/frontend/src/services/lego_blocks/units/intelligence/contracts/chainDigestContractBlock.ts'

/** Read the actual production prompt instead of maintaining a second copy. */
export async function loadProductionSystemPrompt(source = process.env.THINKING_SPACE_CHAIN_DIGEST_CONTRACT || DEFAULT_CONTRACT_SOURCE) {
  const code = await readFile(source, 'utf8')
  const match = /const SYSTEM_PROMPT = (\[[\s\S]*?\]\.join\('\\n'\))/.exec(code)
  if (!match) throw new Error(`Could not find SYSTEM_PROMPT in ${source}`)
  // The expression is a local, audited array of string literals in the source
  // contract. Evaluating it preserves all escaping and punctuation exactly.
  const prompt = Function(`'use strict'; return (${match[1]})`)()
  if (typeof prompt !== 'string' || !prompt.startsWith('You describe what an AI-assisted session')) throw new Error(`Unexpected chain-digest prompt in ${source}`)
  return { prompt, source, sha256: createHash('sha256').update(prompt).digest('hex') }
}

export function formatPrompt(turns, toolCalls = 0) {
  const lines = [`session shape: ${turns.length} substantive turn${turns.length === 1 ? '' : 's'} · ${toolCalls} tool call${toolCalls === 1 ? '' : 's'}`, '']
  for (const turn of turns) {
    lines.push(`${turn.role === 'user' ? 'USER' : 'ASST'}: ${turn.text}`, '')
  }
  lines.push('---', 'OUTPUT:')
  return lines.join('\n')
}

export function normalizeOutput(raw) {
  const lines = String(raw || '').replace(/^```(?:markdown|md)?\s*/i, '').replace(/```\s*$/i, '').split('\n')
  const first = lines.findIndex(line => line.trim())
  if (first < 0) return { title: '', summary: '', format: { hasTitlePrefix: false, hasBlankLine: false } }
  const firstLine = lines[first].trim()
  const hasTitlePrefix = /^title\s*[:\-—]/i.test(firstLine)
  const title = firstLine.replace(/^title\s*[:\-—]\s*/i, '').trim().replace(/[.!?;]+$/, '')
  const summaryLines = lines.slice(first + 1)
  return {
    title,
    summary: summaryLines.join('\n').trim(),
    format: { hasTitlePrefix, hasBlankLine: lines[first + 1]?.trim() === '' },
  }
}
