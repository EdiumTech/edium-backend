// Trusted, test-only source fixtures for the real Kotlin/Swift execution path.
// Never ship these reference answers in a production runner image.
const mobileReferenceSolutions = {
  'mobile-outbox-cat': {
    kotlin: `fun solve(input: Map<String, Any?>): Map<String, Any?> {
    @Suppress("UNCHECKED_CAST")
    val operations = input["operations"] as List<Map<String, Any?>>
    val seen = mutableSetOf<String>()
    val latest = mutableMapOf<String, Map<String, Any?>>()
    val duplicates = mutableListOf<String>()
    for (op in operations) {
        val id = op["id"] as String
        if (!seen.add(id)) { duplicates.add(id); continue }
        val entity = op["entityId"] as String
        val prior = latest[entity]
        if (prior == null || (op["revision"] as Number).toDouble() >= (prior["revision"] as Number).toDouble()) {
            latest[entity] = op
        }
    }
    val items = mutableListOf<Map<String, Any?>>()
    val deleted = mutableListOf<String>()
    for (id in latest.keys.sorted()) {
        val op = latest.getValue(id)
        if (op["kind"] == "delete") deleted.add(id)
        else items.add(mapOf("id" to id, "value" to op["value"], "revision" to op["revision"]))
    }
    return mapOf("items" to items, "deletedIds" to deleted, "duplicateIds" to duplicates)
}`,
  },
  'mobile-permission-panda': {
    swift: `func solve(_ input: [String: Any]) -> [String: Any] {
    let grants = input["grants"] as! [String: String]
    let requests = input["requests"] as! [[String: Any]]
    let foreground = input["foreground"] as! Bool
    var seen = Set<String>()
    var result: [String: [String]] = ["readyIds": [], "settingsIds": [], "deferredIds": [], "rationaleIds": [], "promptIds": [], "ignoredIds": []]
    for request in requests {
        let id = request["id"] as! String
        let permission = request["permission"] as! String
        if !seen.insert(permission).inserted { result["ignoredIds"]!.append(id); continue }
        let state = grants[permission] ?? "unknown"
        let key: String
        if state == "granted" { key = "readyIds" }
        else if state == "blocked" { key = "settingsIds" }
        else if !foreground { key = "deferredIds" }
        else if state == "denied" && !(request["explained"] as! Bool) { key = "rationaleIds" }
        else { key = "promptIds" }
        result[key]!.append(id)
    }
    return result
}`,
  },
  'mobile-download-lunch': {
    kotlin: `fun solve(input: Map<String, Any?>): Map<String, Any?> {
    @Suppress("UNCHECKED_CAST")
    val jobs = input["jobs"] as List<Map<String, Any?>>
    val ordered = jobs.withIndex().sortedWith(Comparator { a, b ->
        val priority = (b.value["priority"] as Number).toDouble().compareTo((a.value["priority"] as Number).toDouble())
        if (priority != 0) priority else a.index.compareTo(b.index)
    })
    var remaining = (input["storageBytes"] as Number).toDouble()
    val selected = mutableListOf<String>()
    val skipped = mutableListOf<Map<String, Any?>>()
    val onWifi = input["onWifi"] as Boolean
    val lowBattery = (input["batteryPercent"] as Number).toDouble() < (input["minBatteryPercent"] as Number).toDouble()
    for (entry in ordered) {
        val job = entry.value
        val id = job["id"] as String
        val bytes = (job["bytes"] as Number).toDouble()
        val reason = when {
            !onWifi && !(job["allowCellular"] as Boolean) -> "wifi"
            lowBattery && !(job["essential"] as Boolean) -> "battery"
            bytes > remaining -> "storage"
            else -> null
        }
        if (reason != null) skipped.add(mapOf("id" to id, "reason" to reason))
        else { selected.add(id); remaining -= bytes }
    }
    return mapOf("selectedIds" to selected, "skipped" to skipped, "remainingBytes" to remaining)
}`,
    swift: `func solve(_ input: [String: Any]) -> [String: Any] {
    let jobs = input["jobs"] as! [[String: Any]]
    let ordered = jobs.enumerated().sorted { a, b in
        let aPriority = a.element["priority"] as! Int
        let bPriority = b.element["priority"] as! Int
        return aPriority == bPriority ? a.offset < b.offset : aPriority > bPriority
    }
    var remaining = input["storageBytes"] as! Int
    var selected = [String]()
    var skipped = [[String: Any]]()
    let onWifi = input["onWifi"] as! Bool
    let lowBattery = (input["batteryPercent"] as! Int) < (input["minBatteryPercent"] as! Int)
    for entry in ordered {
        let job = entry.element
        let id = job["id"] as! String
        let bytes = job["bytes"] as! Int
        let reason: String?
        if !onWifi && !(job["allowCellular"] as! Bool) { reason = "wifi" }
        else if lowBattery && !(job["essential"] as! Bool) { reason = "battery" }
        else if bytes > remaining { reason = "storage" }
        else { reason = nil }
        if let reason = reason { skipped.append(["id": id, "reason": reason]) }
        else { selected.append(id); remaining -= bytes }
    }
    return ["selectedIds": selected, "skipped": skipped, "remainingBytes": remaining]
}`,
  },
}

module.exports = { mobileReferenceSolutions }
