import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index >= 0 ? process.argv[index + 1] : fallback
}
const modelPath = option('model-path')
const serverModel = option('server-model')
const label = option('label', serverModel)
const dataset = option('dataset', 'evals/chain-digest/private/dataset.json')
const append = option('append')
const limit = option('limit')
const start = option('start')
const maxTokens = option('max-tokens')
const output = option('output')
const disableReasoning = option('disable-reasoning')
const engine = option('engine', 'mlx-lm')
const port = option('port', '1235')
if (!modelPath || !serverModel || !label) throw new Error('Pass --model-path, --server-model, and --label')

const serverCommand = engine === 'mlx-vlm' ? 'python3' : 'mlx_lm.server'
const serverArgs = engine === 'mlx-vlm'
  ? ['-m', 'mlx_vlm.server', '--model', modelPath, '--port', port, '--log-progress-interval', '0']
  : ['--model', modelPath, '--port', port]
if (!['mlx-lm', 'mlx-vlm'].includes(engine)) throw new Error(`Unsupported --engine ${engine}`)
const server = spawn(serverCommand, serverArgs, { stdio: 'inherit' })
let exited = false
let serverExitCode = null
server.once('exit', code => { exited = true; serverExitCode = code ?? 1 })
async function ready() {
  const until = Date.now() + 90_000
  while (Date.now() < until) {
    if (exited) throw new Error(`${engine} server exited before becoming ready`)
    try {
      const response = await fetch(`http://127.0.0.1:${port}/v1/models`)
      const payload = await response.json()
      const ids = (payload.data || []).map(row => row.id)
      const expectedIds = engine === 'mlx-vlm' ? [serverModel, modelPath] : [serverModel]
      if (response.ok && expectedIds.some(id => ids.includes(id))) return
      if (response.ok) throw new Error(`Port ${port} answered but did not serve ${expectedIds.join(' or ')}`)
    } catch (error) {
      // ECONNREFUSED is expected during server start. A different server on the
      // selected port is not: never attach an evaluation to the wrong model.
      if (!/fetch failed/i.test(String(error))) throw error
    }
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for ${engine} server on port ${port}`)
}
try {
  await ready()
  const here = path.dirname(fileURLToPath(import.meta.url))
  const args = [path.join(here, 'run.mjs'), '--dataset', dataset, '--base-url', `http://127.0.0.1:${port}/v1`, '--model', serverModel, '--label', label]
  if (engine === 'mlx-vlm') args.push('--request-model', modelPath)
  if (append) args.push('--append', append)
  if (limit) args.push('--limit', limit)
  if (start) args.push('--start', start)
  if (maxTokens) args.push('--max-tokens', maxTokens)
  if (output) args.push('--output', output)
  if (disableReasoning) args.push('--disable-reasoning', disableReasoning)
  const run = spawn(process.execPath, args, { stdio: 'inherit' })
  const code = await new Promise(resolve => run.once('exit', exitCode => resolve(exitCode ?? 1)))
  if (code) process.exitCode = code
} finally {
  if (!exited) server.kill('SIGTERM')
  if (serverExitCode && !process.exitCode) process.exitCode = serverExitCode
}
