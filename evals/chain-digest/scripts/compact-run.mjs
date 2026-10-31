import { readFile, writeFile } from 'node:fs/promises'

function option(name) { const index = process.argv.indexOf(`--${name}`); return index >= 0 ? process.argv[index + 1] : null }
const input = option('run')
if (!input) throw new Error('Pass --run <run.json>')
const run = JSON.parse(await readFile(input, 'utf8'))
const merged = new Map()
for (const candidate of run.candidates) {
  const key = `${candidate.label}\u0000${candidate.model}\u0000${candidate.maxTokens}`
  const existing = merged.get(key)
  if (existing) existing.cases.push(...candidate.cases)
  else merged.set(key, { ...candidate, cases: [...candidate.cases] })
}
const compact = { ...run, candidates: [...merged.values()] }
const output = input.replace(/\.json$/, '.compact.json')
await writeFile(output, JSON.stringify(compact, null, 2) + '\n')
console.log(`Merged ${run.candidates.length} batch candidates into ${compact.candidates.length}: ${output}`)
