import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { normalizeOutput } from './contract.mjs'

function option(name, fallback) { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : fallback }
const datasetPath = option('dataset', 'evals/chain-digest/private/dataset.json')
const baseUrl = option('base-url', 'http://127.0.0.1:1234/v1').replace(/\/$/, '')
const model = option('model')
const requestModel = option('request-model', model)
const label = option('label', model)
const append = option('append')
const limit = Number(option('limit', '0'))
const start = Number(option('start', '0'))
const maxTokens = Number(option('max-tokens', '1024'))
const explicitOutput = option('output')
const disableReasoning = option('disable-reasoning') === 'true'
if (!model) throw new Error('Pass --model <server model id>')
function finalAssistantContent(raw) {
  // Muse Glimmer's current upstream MLX-VLM branch emits its internal
  // `assistant to=self` segment inline. Preserve it for diagnostics, but use
  // the final `assistant to=user` message as the actual response candidate.
  const markers = [...String(raw || '').matchAll(/<\|eom\|><\|start\|>assistant to=user<\|message\|>/g)]
  if (!markers.length) return String(raw || '')
  return String(raw || '').slice(markers.at(-1).index + markers.at(-1)[0].length)
}
const dataset = JSON.parse(await readFile(datasetPath, 'utf8'))
const run = append ? JSON.parse(await readFile(append, 'utf8')) : { schemaVersion: 1, useCase: dataset.useCase, dataset: datasetPath, createdAt: new Date().toISOString(), candidates: [] }
let candidate = run.candidates.find(candidate => candidate.label === label && candidate.model === model && candidate.maxTokens === maxTokens)
if (!candidate) {
  candidate = { label, model, baseUrl, temperature: 0.2, maxTokens, cases: [] }
  run.candidates.push(candidate)
}
const selectedCases = dataset.cases.slice(start, limit > 0 ? start + limit : undefined)
for (const item of selectedCases) {
  const started = performance.now()
  try {
    const response = await fetch(`${baseUrl}/chat/completions`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model: requestModel, temperature: 0.2, max_tokens: maxTokens, messages: [{ role: 'system', content: dataset.systemPrompt }, { role: 'user', content: item.prompt }], ...(disableReasoning ? { chat_template_kwargs: { enable_thinking: false }, enable_thinking: false } : {}) }) })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.error?.message || `HTTP ${response.status}`)
    const rawResponse = payload.choices?.[0]?.message?.content || ''
    const raw = finalAssistantContent(rawResponse)
    candidate.cases.push({ id: item.id, latencyMs: Math.round(performance.now() - started), raw, ...(raw === rawResponse ? {} : { rawResponse }), normalized: normalizeOutput(raw), usage: payload.usage || null })
    console.log(`${label}: ${item.id}`)
  } catch (error) { candidate.cases.push({ id: item.id, latencyMs: Math.round(performance.now() - started), error: String(error.message || error) }); console.error(`${label}: ${item.id}: ${error.message || error}`) }
}
const output = append || explicitOutput || `evals/chain-digest/runs/${new Date().toISOString().replace(/[:.]/g, '-')}.json`
await mkdir(path.dirname(output), { recursive: true }); await writeFile(output, JSON.stringify(run, null, 2) + '\n')
console.log(`Saved ${output}`)
