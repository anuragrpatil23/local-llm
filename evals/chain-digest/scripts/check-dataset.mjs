import { readFile } from 'node:fs/promises'
const file = process.argv[2] || 'evals/chain-digest/private/dataset.json'
const data = JSON.parse(await readFile(file, 'utf8'))
if (!data.systemPrompt || !Array.isArray(data.cases) || data.cases.length < 8) throw new Error('Dataset needs a system prompt and at least 8 cases')
for (const item of data.cases) if (!item.id || !item.prompt?.includes('OUTPUT:') || !item.baseline?.title) throw new Error(`Invalid case ${item.id || '(missing id)'}`)
console.log(`Valid: ${data.cases.length} real-chain cases`)
