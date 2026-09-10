import Foundation

@main
struct EdiumContestBridge {
    static func main() throws {
        guard let line = readLine(),
              let input = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any]
        else {
            throw NSError(domain: "EdiumContest", code: 1, userInfo: [NSLocalizedDescriptionKey: "Вход должен быть JSON-объектом."])
        }
        let result = solve(input)
        guard JSONSerialization.isValidJSONObject(result) else {
            throw NSError(domain: "EdiumContest", code: 2, userInfo: [NSLocalizedDescriptionKey: "Результат должен содержать только JSON-значения."])
        }
        let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        print(String(decoding: output, as: UTF8.self))
    }
}
