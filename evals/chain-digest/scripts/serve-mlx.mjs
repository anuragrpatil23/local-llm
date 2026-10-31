import { spawn } from 'node:child_process'

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index >= 0 ? process.argv[index + 1] : fallback
}

const model = option('model')
const port = option('port', '1234')
if (!model) throw new Error('Usage: npm run eval:serve:mlx -- --model <MLX model directory or HF id> [--port 1234]')

const server = spawn('mlx_lm.server', ['--model', model, '--port', port], { stdio: 'inherit' })
server.on('exit', code => process.exitCode = code ?? 1)
