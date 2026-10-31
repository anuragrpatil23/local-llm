import { readFile, writeFile } from 'node:fs/promises'
function option(name) { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : null }
const input = option('run'); if (!input) throw new Error('Pass --run <run.json>')
const run = JSON.parse(await readFile(input, 'utf8'))
const results = run.candidates.map(candidate => {
  const cases = candidate.cases.map(row => {
    const value = row.normalized
    const bullets = (value?.summary?.match(/^\s*\d+\.\s+/gm) || []).length
    const checks = { succeeded: !row.error, titlePrefix: !!value?.format?.hasTitlePrefix, blankLine: !!value?.format?.hasBlankLine, titleLength: !!value?.title && value.title.length <= 240, summaryLength: (value?.summary?.length || 0) <= 1800, numberedBullets: bullets >= 1 && bullets <= 7, noPreamble: !/^(?:here(?:'s| is)|sure[,!]?|output:)/i.test(row.raw || '') }
    return { id: row.id, latencyMs: row.latencyMs, bullets, checks, passCount: Object.values(checks).filter(Boolean).length, totalChecks: Object.keys(checks).length }
  })
  return { label: candidate.label, model: candidate.model, cases, passRate: cases.reduce((sum, row) => sum + row.passCount / row.totalChecks, 0) / Math.max(cases.length, 1), medianLatencyMs: [...cases].sort((a, b) => a.latencyMs - b.latencyMs)[Math.floor(cases.length / 2)]?.latencyMs || 0 }
})
const output = input.replace(/\.json$/, '.score.json'); await writeFile(output, JSON.stringify({ run: input, results }, null, 2) + '\n')
for (const result of results) console.log(`${result.label}: ${(result.passRate * 100).toFixed(1)}% contract pass, median ${result.medianLatencyMs}ms`)
console.log(`Saved ${output}`)
