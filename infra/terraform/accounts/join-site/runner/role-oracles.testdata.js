// Test-only fixture oracles. These do not execute or stand in for Go/Python.
const roleOracles = {
  'backend-webhook-receipts': function solve(input) {
    let balance = input.balance
    const seen = new Set(), receipts = []
    for (const [index, event] of input.events.entries()) {
      if (!event || typeof event !== 'object' || Array.isArray(event) || typeof event.id !== 'string' || !event.id || !['credit', 'debit'].includes(event.kind) || !Number.isInteger(event.amount) || event.amount <= 0 || event.signed !== true) {
        receipts.push({ index, id: null, status: 'invalid' }); continue
      }
      let status
      if (seen.has(event.id)) status = 'duplicate'
      else {
        seen.add(event.id)
        if (event.kind === 'debit' && event.amount > balance) status = 'insufficient'
        else { status = 'applied'; balance += event.kind === 'credit' ? event.amount : -event.amount }
      }
      receipts.push({ index, id: event.id, status })
    }
    return { balance, receipts }
  },
  'backend-retry-desk': function solve(input) {
    return { decisions: input.results.map(item => {
      let action = 'failed', reason = 'permanent', nextAt = null
      const transient = item.error !== null ? ['timeout', 'reset'].includes(item.error) : [408, 429, 500, 502, 503, 504].includes(item.status)
      if (item.error === null && item.status >= 200 && item.status < 300) { action = 'done'; reason = 'success' }
      else if (transient) {
        const delay = item.retryAfter === null ? Math.min(input.maxDelay, input.baseDelay * item.attempt) : item.retryAfter
        if (item.method === 'POST' && !item.idempotencyKey) { action = 'manual'; reason = 'unsafe' }
        else if (item.attempt >= input.maxAttempts) reason = 'attempts'
        else if (delay > input.maxDelay) reason = 'wait_limit'
        else if (input.now + delay > input.budgetUntil) reason = 'budget'
        else { action = 'retry'; reason = 'transient'; nextAt = input.now + delay }
      }
      return { id: item.id, action, reason, nextAt }
    }) }
  },
  'backend-quota-reservations': function solve(input) {
    const remaining = { ...input.capacities }, seen = new Set(), used = new Set(), active = new Map(), receipts = []
    for (const op of input.operations) {
      let status = 'duplicate'
      if (!seen.has(op.id)) {
        seen.add(op.id)
        if (op.kind === 'release') {
          status = 'missing'
          if (active.has(op.reservationId)) {
            for (const [key, amount] of Object.entries(active.get(op.reservationId))) remaining[key] += amount
            active.delete(op.reservationId); status = 'released'
          }
        } else if (used.has(op.reservationId)) status = 'conflict'
        else if (Object.keys(op.amounts).some(key => !Object.hasOwn(remaining, key))) status = 'invalid'
        else if (Object.entries(op.amounts).some(([key, amount]) => amount > remaining[key])) status = 'insufficient'
        else {
          for (const [key, amount] of Object.entries(op.amounts)) remaining[key] -= amount
          active.set(op.reservationId, op.amounts); used.add(op.reservationId); status = 'reserved'
        }
      }
      receipts.push({ id: op.id, status })
    }
    return { remaining, receipts, activeIds: [...active.keys()].sort() }
  },
  'ai-dataset-quarantine': function solve(input) {
    const groups = new Map(), reasons = new Map(), normalized = new Map()
    input.rows.forEach((row, index) => {
      const text = typeof row.text === 'string' ? row.text.trim().replace(/\s+/g, ' ').toLowerCase() : ''
      const reason = !text ? 'empty_text' : !['safe', 'unsafe'].includes(row.label) ? 'label' : !['train', 'test'].includes(row.split) ? 'split' : null
      if (reason) reasons.set(index, reason)
      else { normalized.set(index, text); if (!groups.has(text)) groups.set(text, []); groups.get(text).push(index) }
    })
    for (const indices of groups.values()) {
      const rows = indices.map(index => input.rows[index])
      const reason = new Set(rows.map(row => row.label)).size > 1 ? 'label_conflict' : new Set(rows.map(row => row.split)).size > 1 ? 'test_leakage' : null
      for (const index of reason ? indices : indices.slice(1)) reasons.set(index, reason || 'duplicate')
    }
    const clean = [], rejected = []
    input.rows.forEach((row, index) => {
      if (reasons.has(index)) rejected.push({ id: row.id, reason: reasons.get(index) })
      else clean.push({ id: row.id, text: normalized.get(index), label: row.label, split: row.split })
    })
    return { clean, rejected }
  },
  'ai-grounded-eval': function solve(input) {
    const counts = { grounded: 0, unsafe: 0, needs_review: 0, correct_abstain: 0, unnecessary_abstain: 0, empty: 0 }
    const reports = input.answers.map(answer => {
      const supportedIds = [], unsupportedIds = []
      let missingRequired = false
      for (const claim of answer.claims) {
        const valid = answer.citations.some(citation => {
          const source = Object.hasOwn(input.sources, citation.sourceId) ? input.sources[citation.sourceId] : null
          const quote = citation.quote.trim()
          return citation.claimId === claim.id && source && source.trusted && quote && source.text.includes(quote)
        })
        ;(valid ? supportedIds : unsupportedIds).push(claim.id)
        if (!valid && claim.required) missingRequired = true
      }
      const status = answer.abstained ? answer.expectedAnswerable ? 'unnecessary_abstain' : 'correct_abstain' : !answer.claims.length ? 'empty' : missingRequired ? 'unsafe' : unsupportedIds.length ? 'needs_review' : 'grounded'
      counts[status]++
      return { id: answer.id, status, supportedIds, unsupportedIds }
    })
    return { reports, counts }
  },
  'ai-rag-context-audit': function solve(input) {
    const seen = new Set(), reasons = new Map(), winners = new Map(), trimmed = new Map()
    input.records.forEach((record, index) => {
      if (seen.has(record.id)) { reasons.set(index, 'duplicate'); return }
      seen.add(record.id)
      const text = record.text.trim()
      const reason = !input.allowedCollections.includes(record.collection) ? 'collection' : record.injection ? 'instruction' : record.retrievedAt > input.now ? 'future' : input.now - record.retrievedAt > input.maxAge ? 'stale' : !text ? 'empty' : null
      if (reason) { reasons.set(index, reason); return }
      trimmed.set(index, text)
      const prior = winners.get(record.sourceId)
      if (prior === undefined || record.revision > input.records[prior].revision) {
        if (prior !== undefined) reasons.set(prior, 'superseded')
        winners.set(record.sourceId, index)
      } else reasons.set(index, 'superseded')
    })
    const context = [], rejected = []
    input.records.forEach((record, index) => {
      if (reasons.has(index)) rejected.push({ id: record.id, reason: reasons.get(index) })
      else context.push({ sourceId: record.sourceId, recordId: record.id, text: trimmed.get(index), revision: record.revision })
    })
    return { context, rejected }
  },
}

module.exports = { roleOracles }
