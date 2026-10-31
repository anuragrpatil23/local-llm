import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

function option(name, fallback) { const index = process.argv.indexOf(`--${name}`); return index >= 0 ? process.argv[index + 1] : fallback }
const modelPath = option('model-path')
const serverModel = option('server-model')
const label = option('label', serverModel)
const dataset = option('dataset', 'evals/chain-digest/private/dataset.json')
const batchSize = Number(option('batch-size', '10'))
const maxTokens = option('max-tokens', '1024')
const port = option('port', '1235')
const engine = option('engine', 'mlx-lm')
const disableReasoning = option('disable-reasoning', /qwen(?:3|[-_. ]?3)/i.test(serverModel || '') ? 'true' : 'false')
if (!modelPath || !serverModel || !label) throw new Error('Pass --model-path, --server-model, and --label')
const count = JSON.parse(await readFile(dataset, 'utf8')).cases.length
const output = `evals/chain-digest/runs/${new Date().toISOString().replace(/[:.]/g, '-')}.json`
const here = path.dirname(fileURLToPath(import.meta.url))
for (let start = 0; start < count; start += batchSize) {
  const args = [path.join(here, 'run-with-mlx.mjs'), '--model-path', modelPath, '--server-model', serverModel, '--label', label, '--dataset', dataset, '--start', String(start), '--limit', String(batchSize), '--max-tokens', maxTokens, '--port', port]
  args.push('--engine', engine)
  if (start === 0) args.push('--output', output)
  else args.push('--append', output)
  if (disableReasoning === 'true') args.push('--disable-reasoning', 'true')
  console.log(`Batch ${start / batchSize + 1}: cases ${start + 1}-${Math.min(start + batchSize, count)}`)
  const child = spawn(process.execPath, args, { stdio: 'inherit' })
  const code = await new Promise(resolve => child.once('exit', exitCode => resolve(exitCode ?? 1)))
  if (code) process.exit(Number(code))
}
console.log(`Saved ${output}`)
