// Test-only native reference sources. Never include in a production image.
const roleReferenceSolutions = {
  'backend-webhook-receipts': { go: `package main

func Solve(input map[string]any) map[string]any {
    balance := int(input["balance"].(float64))
    seen := map[string]bool{}
    receipts := []any{}
    for index, raw := range input["events"].([]any) {
        event, objectOK := raw.(map[string]any)
        id, idOK := event["id"].(string)
        kind, kindOK := event["kind"].(string)
        amount, amountOK := event["amount"].(float64)
        signed, _ := event["signed"].(bool)
        status := "invalid"
        var receiptID any = nil
        if objectOK && idOK && id != "" && kindOK && (kind == "credit" || kind == "debit") && amountOK && amount > 0 && amount == float64(int(amount)) && signed {
            receiptID = id
            if seen[id] { status = "duplicate" } else {
                seen[id] = true
                if kind == "debit" && int(amount) > balance { status = "insufficient" } else {
                    status = "applied"
                    if kind == "credit" { balance += int(amount) } else { balance -= int(amount) }
                }
            }
        }
        receipts = append(receipts, map[string]any{"index": index, "id": receiptID, "status": status})
    }
    return map[string]any{"balance": balance, "receipts": receipts}
}` },
  'backend-retry-desk': { go: `package main

func Solve(input map[string]any) map[string]any {
    number := func(key string) int { return int(input[key].(float64)) }
    decisions := []any{}
    for _, raw := range input["results"].([]any) {
        item := raw.(map[string]any)
        status := 0
        if item["status"] != nil { status = int(item["status"].(float64)) }
        action, reason := "failed", "permanent"
        var nextAt any = nil
        transient := false
        if item["error"] != nil { transient = item["error"] == "timeout" || item["error"] == "reset" } else if status >= 200 && status < 300 {
            action, reason = "done", "success"
        } else { transient = status == 408 || status == 429 || status == 500 || status == 502 || status == 503 || status == 504 }
        if transient {
            method := item["method"].(string)
            safe := method == "GET" || method == "PUT" || method == "DELETE" || (method == "POST" && item["idempotencyKey"].(bool))
            attempt := int(item["attempt"].(float64))
            delay := number("baseDelay") * attempt
            if delay > number("maxDelay") { delay = number("maxDelay") }
            if item["retryAfter"] != nil { delay = int(item["retryAfter"].(float64)) }
            if !safe { action, reason = "manual", "unsafe" } else if attempt >= number("maxAttempts") { reason = "attempts" } else if delay > number("maxDelay") { reason = "wait_limit" } else if number("now") + delay > number("budgetUntil") { reason = "budget" } else {
                action, reason, nextAt = "retry", "transient", number("now") + delay
            }
        }
        decisions = append(decisions, map[string]any{"id": item["id"], "action": action, "reason": reason, "nextAt": nextAt})
    }
    return map[string]any{"decisions": decisions}
}` },
  'backend-quota-reservations': { go: `package main

import "sort"

func Solve(input map[string]any) map[string]any {
    remaining := map[string]int{}
    for key, value := range input["capacities"].(map[string]any) { remaining[key] = int(value.(float64)) }
    seen, used := map[string]bool{}, map[string]bool{}
    active := map[string]map[string]int{}
    receipts := []any{}
    for _, raw := range input["operations"].([]any) {
        op := raw.(map[string]any)
        id, reservationID := op["id"].(string), op["reservationId"].(string)
        status := "duplicate"
        if !seen[id] {
            seen[id] = true
            if op["kind"] == "release" {
                amounts, exists := active[reservationID]
                status = "missing"
                if exists {
                    for key, value := range amounts { remaining[key] += value }
                    delete(active, reservationID)
                    status = "released"
                }
            } else if used[reservationID] { status = "conflict" } else {
                amounts := map[string]int{}
                valid, enough := true, true
                for key, value := range op["amounts"].(map[string]any) {
                    available, exists := remaining[key]
                    amount := int(value.(float64))
                    if !exists { valid = false }
                    if amount > available { enough = false }
                    amounts[key] = amount
                }
                if !valid { status = "invalid" } else if !enough { status = "insufficient" } else {
                    for key, value := range amounts { remaining[key] -= value }
                    active[reservationID], used[reservationID], status = amounts, true, "reserved"
                }
            }
        }
        receipts = append(receipts, map[string]any{"id": id, "status": status})
    }
    activeIDs := []string{}
    for id := range active { activeIDs = append(activeIDs, id) }
    sort.Strings(activeIDs)
    return map[string]any{"remaining": remaining, "receipts": receipts, "activeIds": activeIDs}
}` },
  'ai-dataset-quarantine': { python: `def solve(data: dict) -> dict:
    groups, reasons, normalized = {}, {}, {}
    for index, row in enumerate(data["rows"]):
        text = " ".join(row["text"].split()).lower() if isinstance(row["text"], str) else ""
        reason = "empty_text" if not text else "label" if row["label"] not in ("safe", "unsafe") else "split" if row["split"] not in ("train", "test") else None
        if reason:
            reasons[index] = reason
        else:
            normalized[index] = text
            groups.setdefault(text, []).append(index)
    for indices in groups.values():
        rows = [data["rows"][index] for index in indices]
        reason = "label_conflict" if len({row["label"] for row in rows}) > 1 else "test_leakage" if len({row["split"] for row in rows}) > 1 else None
        for index in indices if reason else indices[1:]:
            reasons[index] = reason or "duplicate"
    clean, rejected = [], []
    for index, row in enumerate(data["rows"]):
        if index in reasons:
            rejected.append({"id": row["id"], "reason": reasons[index]})
        else:
            clean.append({"id": row["id"], "text": normalized[index], "label": row["label"], "split": row["split"]})
    return {"clean": clean, "rejected": rejected}
` },
  'ai-grounded-eval': { python: `def solve(data: dict) -> dict:
    reports = []
    counts = {key: 0 for key in ("grounded", "unsafe", "needs_review", "correct_abstain", "unnecessary_abstain", "empty")}
    for answer in data["answers"]:
        supported, unsupported = [], []
        missing_required = False
        for claim in answer["claims"]:
            valid = False
            for citation in answer["citations"]:
                source = data["sources"].get(citation["sourceId"])
                quote = citation["quote"].strip()
                if citation["claimId"] == claim["id"] and source and source["trusted"] and quote and quote in source["text"]:
                    valid = True
                    break
            (supported if valid else unsupported).append(claim["id"])
            missing_required |= claim["required"] and not valid
        if answer["abstained"]:
            status = "unnecessary_abstain" if answer["expectedAnswerable"] else "correct_abstain"
        elif not answer["claims"]:
            status = "empty"
        else:
            status = "unsafe" if missing_required else "needs_review" if unsupported else "grounded"
        counts[status] += 1
        reports.append({"id": answer["id"], "status": status, "supportedIds": supported, "unsupportedIds": unsupported})
    return {"reports": reports, "counts": counts}
` },
  'ai-rag-context-audit': { python: `def solve(data: dict) -> dict:
    seen, reasons, winners, trimmed = set(), {}, {}, {}
    for index, record in enumerate(data["records"]):
        if record["id"] in seen:
            reasons[index] = "duplicate"
            continue
        seen.add(record["id"])
        text = record["text"].strip()
        reason = "collection" if record["collection"] not in data["allowedCollections"] else "instruction" if record["injection"] else "future" if record["retrievedAt"] > data["now"] else "stale" if data["now"] - record["retrievedAt"] > data["maxAge"] else "empty" if not text else None
        if reason:
            reasons[index] = reason
            continue
        trimmed[index] = text
        prior = winners.get(record["sourceId"])
        if prior is None or record["revision"] > data["records"][prior]["revision"]:
            if prior is not None:
                reasons[prior] = "superseded"
            winners[record["sourceId"]] = index
        else:
            reasons[index] = "superseded"
    context, rejected = [], []
    for index, record in enumerate(data["records"]):
        if index in reasons:
            rejected.append({"id": record["id"], "reason": reasons[index]})
        else:
            context.append({"sourceId": record["sourceId"], "recordId": record["id"], "text": trimmed[index], "revision": record["revision"]})
    return {"context": context, "rejected": rejected}
` },
}

module.exports = { roleReferenceSolutions }
