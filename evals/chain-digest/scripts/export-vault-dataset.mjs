import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { formatPrompt, loadProductionSystemPrompt } from './contract.mjs'

const args = {}
for (let i = 2; i < process.argv.length; i += 1) {
  if (process.argv[i].startsWith('--')) args[process.argv[i].slice(2)] = process.argv[i + 1]
}
const vault = args.vault
const limit = Number(args.limit || 50)
const output = args.output || 'evals/chain-digest/private/dataset.json'
if (!vault) throw new Error('Usage: npm run eval:export -- --vault <Thinking Space vault> [--limit 50]')

function frontmatter(text) {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(text)
  const values = {}
  if (!m) return { values, body: text }
  for (const line of m[1].split('\n')) {
    const field = /^([^:]+):\s*["']?(.*?)["']?\s*$/.exec(line)
    if (field) values[field[1]] = field[2]
  }
  return { values, body: text.slice(m[0].length).trim() }
}

async function filesDeep(dir) {
  const out = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const item = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...await filesDeep(item))
    else if (entry.isFile() && entry.name.endsWith('.md')) out.push(item)
  }
  return out
}

function cleanTurn(role, text) {
  let value = text.trim()
  if (!value || /^<(?:local-command-caveat|command-name|command-message|command-args|local-command-stdout)/.test(value)) return null
  if (value.startsWith('[tool_result]')) return null
  // Tool command / JSON sections are not model-visible in the production contract.
  value = value.replace(/\*\*\[tool_use:[\s\S]*?```[\s\S]*?```\s*/g, '').replace(/\*\*\[tool_use:[^\n]*\]\*\*/g, '').trim()
  if (role === 'assistant' && value.length < 300) return null
  if (!value) return null
  const head = role === 'user' ? 300 : 250
  const tail = role === 'user' ? 200 : 200
  if (value.length > 600 && value.length > head + tail + 100) value = `${value.slice(0, head).trimEnd()}\n[…${value.length - head - tail}c omitted…]\n${value.slice(-tail).trimStart()}`
  return value
}

function turnsFromRaw(text) {
  const body = text.replace(/^---\n[\s\S]*?\n---\n?/, '')
  const re = /^## (User|Assistant)\s*\n([\s\S]*?)(?=\n---\n## (?:User|Assistant)\s*\n|$)/gm
  const turns = []
  let match
  while ((match = re.exec(body))) {
    const role = match[1] === 'User' ? 'user' : 'assistant'
    const cleaned = cleanTurn(role, match[2])
    if (cleaned) turns.push({ role, text: cleaned })
  }
  return { turns, toolCalls: (body.match(/\[tool_use:/g) || []).length }
}

const chainRoot = path.join(vault, 'ai-activity/chains')
const rawRoot = path.join(vault, 'ai-activity/raw-sessions')
const productionContract = await loadProductionSystemPrompt(args['contract-source'])
const chainFiles = await filesDeep(chainRoot)
const rawFiles = await filesDeep(rawRoot)
const rows = []
const seenSessions = new Set()
for (const file of chainFiles) {
  const { values, body } = frontmatter(await readFile(file, 'utf8'))
  const title = values.title?.trim()
  const sessionId = /([0-9a-f]{8})-[0-9a-f-]{27,}/i.exec(values.chainKey || '')?.[1]
  if (!title || !sessionId || seenSessions.has(sessionId) || Number(values.msgCount || 0) < 2) continue
  const matchingRawFiles = rawFiles.filter(candidate => path.basename(candidate).includes(sessionId))
  if (matchingRawFiles.length !== 1) continue
  const raw = await readFile(matchingRawFiles[0], 'utf8')
  const { turns, toolCalls } = turnsFromRaw(raw)
  if (turns.length === 0) continue
  seenSessions.add(sessionId)
  rows.push({
    id: `chain-${sessionId}`,
    project: values.projectId || 'unknown',
    source: values.source || 'unknown',
    prompt: formatPrompt(turns, toolCalls),
    baseline: { title, summary: body.replace(/^# .*\n?/, '').trim() },
    privateSource: { chainFile: file, rawFile: matchingRawFiles[0] },
  })
}

// Every represented project gets one case, then additional cases are selected
// proportionally to its usable-chain population. This preserves domain coverage
// without pretending a small project is as important as a heavily used one.
const byProject = new Map()
for (const row of rows.sort((a, b) => a.project.localeCompare(b.project) || a.id.localeCompare(b.id))) {
  if (!byProject.has(row.project)) byProject.set(row.project, [])
  byProject.get(row.project).push(row)
}
const populations = new Map([...byProject.entries()].map(([project, list]) => [project, list.length]))
const selected = []
for (const list of byProject.values()) if (selected.length < limit && list.length) selected.push(list.shift())
while (selected.length < limit) {
  const eligible = [...byProject.entries()].filter(([, list]) => list.length)
  if (!eligible.length) break
  eligible.sort(([projectA], [projectB]) => {
    const selectedA = selected.filter(row => row.project === projectA).length / populations.get(projectA)
    const selectedB = selected.filter(row => row.project === projectB).length / populations.get(projectB)
    return selectedA - selectedB || projectA.localeCompare(projectB)
  })
  selected.push(eligible[0][1].shift())
}
await mkdir(path.dirname(output), { recursive: true })
await writeFile(output, JSON.stringify({ schemaVersion: 1, useCase: 'thinking-space-chain-digest', exportedAt: new Date().toISOString(), productionContract, systemPrompt: productionContract.prompt, cases: selected }, null, 2) + '\n')
const projectCounts = Object.groupBy(selected, row => row.project)
console.log(`Exported ${selected.length}/${rows.length} usable chains across ${Object.keys(projectCounts).length} projects (proportional allocation) to ${output}`)
console.log(Object.entries(projectCounts).map(([project, cases]) => `${project}: ${cases.length}`).join(', '))
