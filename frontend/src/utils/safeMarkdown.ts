const htmlEscapes: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => htmlEscapes[character])
}

function safeHref(value: string): { href: string; external: boolean } | null {
  const trimmed = value.trim()
  if (trimmed.startsWith('/') && !trimmed.startsWith('//')) {
    try {
      const parsed = new URL(trimmed, 'https://materialbrain.invalid')
      if (parsed.origin === 'https://materialbrain.invalid') {
        return {
          href: `${parsed.pathname}${parsed.search}${parsed.hash}`,
          external: false,
        }
      }
    } catch {
      return null
    }
    return null
  }
  if (/^#[a-z0-9_-]+$/i.test(trimmed)) {
    return { href: trimmed, external: false }
  }
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
      return { href: parsed.toString(), external: true }
    }
  } catch {
    return null
  }
  return null
}

const inlineToken =
  /(`[^`\n]+`|\[([^\]\n]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_))/g

function renderInline(value: string): string {
  let output = ''
  let offset = 0
  for (const match of value.matchAll(inlineToken)) {
    const index = match.index ?? 0
    output += escapeHtml(value.slice(offset, index))
    const token = match[0]
    if (token.startsWith('`')) {
      output += `<code>${escapeHtml(token.slice(1, -1))}</code>`
    } else if (match[2] !== undefined && match[3] !== undefined) {
      const safe = safeHref(match[3])
      if (safe) {
        const target = safe.external ? ' target="_blank" rel="noopener noreferrer"' : ''
        output += `<a href="${escapeHtml(safe.href)}"${target}>${escapeHtml(match[2])}</a>`
      } else {
        output += escapeHtml(token)
      }
    } else if (match[4] !== undefined) {
      output += `<strong>${escapeHtml(match[4])}</strong>`
    } else if (match[5] !== undefined) {
      output += `<strong>${escapeHtml(match[5])}</strong>`
    } else if (match[6] !== undefined) {
      output += `<em>${escapeHtml(match[6])}</em>`
    } else if (match[7] !== undefined) {
      output += `<em>${escapeHtml(match[7])}</em>`
    }
    offset = index + token.length
  }
  output += escapeHtml(value.slice(offset))
  return output
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isTableSeparator(line: string): boolean {
  const values = cells(line)
  return values.length > 0 && values.every((value) => /^:?-{3,}:?$/.test(value))
}

function isBlockStart(lines: string[], index: number): boolean {
  const line = lines[index] || ''
  return (
    !line.trim() ||
    /^\s{0,3}#{1,6}\s/.test(line) ||
    /^\s*```/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*[-*+]\s+/.test(line) ||
    /^\s*\d+[.)]\s+/.test(line) ||
    (line.includes('|') && index + 1 < lines.length && isTableSeparator(lines[index + 1]))
  )
}

/** Render a small Markdown subset using generated tags and escaped model text only. */
export function renderSafeMarkdown(value: unknown): string {
  const lines = String(value ?? '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
  const blocks: string[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }

    if (/^\s*```/.test(line)) {
      index += 1
      const code: string[] = []
      while (index < lines.length && !/^\s*```/.test(lines[index])) {
        code.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      blocks.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
      continue
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (heading) {
      const level = heading[1].length
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      index += 1
      continue
    }

    if (line.includes('|') && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const headers = cells(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(cells(lines[index]))
        index += 1
      }
      const head = headers.map((cell) => `<th>${renderInline(cell)}</th>`).join('')
      const body = rows
        .map(
          (row) =>
            `<tr>${headers
              .map((_, cellIndex) => `<td>${renderInline(row[cellIndex] || '')}</td>`)
              .join('')}</tr>`,
        )
        .join('')
      blocks.push(`<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`)
      continue
    }

    if (/^\s*>/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ''))
        index += 1
      }
      blocks.push(
        `<blockquote>${quote.map((item) => `<p>${renderInline(item)}</p>`).join('')}</blockquote>`,
      )
      continue
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/)
    if (unordered || ordered) {
      const listType = unordered ? 'ul' : 'ol'
      const items: string[] = []
      while (index < lines.length) {
        const item = lines[index].match(
          listType === 'ul' ? /^\s*[-*+]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/,
        )
        if (!item) break
        items.push(`<li>${renderInline(item[1])}</li>`)
        index += 1
      }
      blocks.push(`<${listType}>${items.join('')}</${listType}>`)
      continue
    }

    const paragraph: string[] = []
    while (index < lines.length && !isBlockStart(lines, index)) {
      paragraph.push(lines[index])
      index += 1
    }
    if (paragraph.length === 0) {
      paragraph.push(lines[index])
      index += 1
    }
    blocks.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`)
  }

  return blocks.join('')
}
