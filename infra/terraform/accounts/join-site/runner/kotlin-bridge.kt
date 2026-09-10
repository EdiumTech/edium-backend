@file:OptIn(kotlin.js.ExperimentalJsExport::class)

private fun inputValue(value: dynamic): Any? {
    if (value == null) return null
    return when (jsTypeOf(value)) {
        "string" -> value as String
        "boolean" -> value as Boolean
        "number" -> value as Double
        "object" -> {
            if (js("Array.isArray(value)") as Boolean) {
                (0 until (value.length as Int)).map { inputValue(value[it]) }
            } else {
                val keys = js("Object.keys(value)") as Array<String>
                keys.associateWith { inputValue(value[it]) }
            }
        }
        else -> error("Вход должен содержать только JSON-значения.")
    }
}

private fun outputValue(value: Any?): dynamic = when (value) {
    null -> null
    is String, is Boolean -> value
    is Number -> value.toDouble()
    is Map<*, *> -> {
        val result = js("Object.create(null)")
        for ((key, item) in value) {
            require(key is String) { "Ключи результата должны быть строками." }
            result[key] = outputValue(item)
        }
        result
    }
    is Iterable<*> -> value.map { outputValue(it) }.toTypedArray()
    is Array<*> -> value.map { outputValue(it) }.toTypedArray()
    else -> error("Результат должен содержать только JSON-значения.")
}

@JsExport
fun runCase(inputJson: String): String {
    val input = inputValue(JSON.parse<dynamic>(inputJson))
    require(input is Map<*, *>) { "Вход должен быть JSON-объектом." }
    @Suppress("UNCHECKED_CAST")
    val result = solve(input as Map<String, Any?>)
    return JSON.stringify(outputValue(result))
}
