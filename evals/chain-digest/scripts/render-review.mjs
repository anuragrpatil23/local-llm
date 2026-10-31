import { readFile, writeFile } from 'node:fs/promises'

function option(name) { const i = process.argv.indexOf(`--${name}`); return i >= 0 ? process.argv[i + 1] : null }
const runPath = option('run'); if (!runPath) throw new Error('Pass --run <run.json>')
const run = JSON.parse(await readFile(runPath, 'utf8'))
const dataset = JSON.parse(await readFile(run.dataset, 'utf8'))
const byId = new Map(dataset.cases.map(item => [item.id, item]))
const escape = value => String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const rotate = (items, n) => items.slice(n).concat(items.slice(0, n))
const sections = dataset.cases.map((item, index) => {
  const candidates = rotate(run.candidates.map(candidate => ({ label: candidate.label, row: candidate.cases.find(row => row.id === item.id) })), index % Math.max(run.candidates.length, 1))
  const cards = candidates.map((candidate, i) => {
    const value = candidate.row?.normalized
    const output = candidate.row?.error ? `ERROR: ${candidate.row.error}` : candidate.row?.raw
    return `<article class="candidate"><h3>Candidate ${String.fromCharCode(65 + i)}</h3><pre>${escape(output)}</pre><label>Score (1–5) <select name="${item.id}:${i}:score"><option></option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label><label><input type="checkbox" name="${item.id}:${i}:wouldUse"> Would use in timeline</label><input type="hidden" name="${item.id}:${i}:model" value="${escape(candidate.label)}"></article>`
  }).join('')
  return `<section><h2>Case ${index + 1}: ${escape(item.project)} · ${escape(item.source)}</h2><details><summary>Source chain prompt (use this for factuality)</summary><pre>${escape(item.prompt)}</pre></details><details><summary>Current stored digest (baseline; not a gold answer)</summary><pre>TITLE: ${escape(item.baseline.title)}\n\n${escape(item.baseline.summary)}</pre></details><div class="candidates">${cards}</div><label class="notes">Why? / factual errors / preference <textarea name="${item.id}:notes"></textarea></label></section>`
}).join('\n')
const html = `<!doctype html><meta charset="utf-8"><title>Chain digest blind review</title><style>body{font:15px system-ui;max-width:1300px;margin:24px auto;padding:0 20px;background:#fafafa;color:#17202a}section{border-top:2px solid #ccd;padding:25px 0}pre{white-space:pre-wrap;max-height:420px;overflow:auto;background:#fff;border:1px solid #d8dee9;padding:14px;border-radius:6px}.candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px}.candidate{background:#eef3fa;padding:14px;border-radius:8px}.candidate label,.notes{display:block;margin-top:12px}.notes textarea{display:block;width:100%;height:70px}button{position:sticky;bottom:15px;padding:12px 18px;background:#163b66;color:white;border:0;border-radius:6px;font-weight:600}</style><h1>Thinking Space chain-digest blind review</h1><p>Score factuality, coverage, specificity, and usefulness together. Candidate identities are intentionally hidden; the downloaded JSON maps them back to models.</p><form id="review">${sections}<button type="button" onclick="download()">Download review JSON</button></form><script>function download(){const d={run:${JSON.stringify(runPath)},reviewedAt:new Date().toISOString(),answers:Object.fromEntries(new FormData(document.querySelector('form')))};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='chain-digest-review.json';a.click()}</script>`
const output = runPath.replace(/\.json$/, '.review.html'); await writeFile(output, html)
console.log(`Saved ${output}`)
