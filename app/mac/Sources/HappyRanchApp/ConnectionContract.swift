import Foundation
import CryptoKit
import Darwin

enum ConnectionRefusal: Error { case invalid }

/// Parse before Foundation can discard duplicate keys. The wire accepts only
/// integer numbers, valid Unicode strings, and at most sixteen nested levels.
indirect enum ConnectionJSON: Equatable {
    case object([String: ConnectionJSON]), array([ConnectionJSON]), string(String)
    case integer(Int64), bool(Bool), null

    var object: [String: ConnectionJSON]? { if case .object(let v) = self { return v }; return nil }
    var string: String? { if case .string(let v) = self { return v }; return nil }
    var integer: Int64? { if case .integer(let v) = self { return v }; return nil }
    var array: [ConnectionJSON]? { if case .array(let v) = self { return v }; return nil }

    static func parse(_ raw: String, limit: Int = 4096) throws -> ConnectionJSON {
        guard raw.utf8.count <= limit else { throw ConnectionRefusal.invalid }
        var parser = Parser(bytes: Array(raw.utf8))
        let value = try parser.value(depth: 0)
        parser.space()
        guard parser.index == parser.bytes.count else { throw ConnectionRefusal.invalid }
        return value
    }

    private struct Parser {
        let bytes: [UInt8]
        var index = 0
        mutating func space() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
        mutating func take(_ byte: UInt8) -> Bool {
            space()
            guard index < bytes.count, bytes[index] == byte else { return false }
            index += 1; return true
        }
        mutating func text() throws -> String {
            space()
            let start = index
            guard take(34) else { throw ConnectionRefusal.invalid }
            while index < bytes.count {
                let byte = bytes[index]; index += 1
                if byte == 34 {
                    return try JSONDecoder().decode(String.self, from: Data(bytes[start..<index]))
                }
                if byte == 92 { index += 1 }
            }
            throw ConnectionRefusal.invalid
        }
        mutating func value(depth: Int) throws -> ConnectionJSON {
            guard depth <= 16 else { throw ConnectionRefusal.invalid }
            space()
            guard index < bytes.count else { throw ConnectionRefusal.invalid }
            if bytes[index] == 34 { return .string(try text()) }
            if take(123) {
                var result: [String: ConnectionJSON] = [:]
                if take(125) { return .object(result) }
                repeat {
                    let key = try text()
                    guard result[key] == nil, take(58) else { throw ConnectionRefusal.invalid }
                    result[key] = try value(depth: depth + 1)
                    if take(125) { return .object(result) }
                } while take(44)
                throw ConnectionRefusal.invalid
            }
            if take(91) {
                var result: [ConnectionJSON] = []
                if take(93) { return .array(result) }
                repeat {
                    result.append(try value(depth: depth + 1))
                    if take(93) { return .array(result) }
                } while take(44)
                throw ConnectionRefusal.invalid
            }
            for (literal, result) in [("true", ConnectionJSON.bool(true)), ("false", .bool(false)), ("null", .null)] {
                let token = Array(literal.utf8)
                if bytes[index...].starts(with: token) { index += token.count; return result }
            }
            let start = index
            if bytes[index] == 45 { index += 1 }
            guard index < bytes.count, (48...57).contains(bytes[index]) else { throw ConnectionRefusal.invalid }
            if bytes[index] == 48 { index += 1 }
            else { while index < bytes.count && (48...57).contains(bytes[index]) { index += 1 } }
            guard let number = Int64(String(decoding: bytes[start..<index], as: UTF8.self)) else { throw ConnectionRefusal.invalid }
            return .integer(number)
        }
    }
}

struct ConnectionRequest {
    static let commands: Set<String> = [
        "bridge.hello", "host.status", "host.enable", "host.turnOff", "host.createInvite",
        "host.approveDevice", "host.rejectDevice", "host.nextDevice", "host.revokeDevice",
        "client.status", "client.acceptInvite", "client.savedComputers", "client.connectSaved",
        "client.removeSaved", "client.disconnect", "client.cancelAttempt", "client.retry",
        "client.retrySave", "client.health"
    ]
    let id: String
    let command: String

    init(_ raw: String) throws {
        guard let value = try ConnectionJSON.parse(raw).object,
              Set(value.keys) == ["protocol", "version", "requestId", "command", "args"],
              value["protocol"] == .string("happyranch.connection"), value["version"] == .integer(1),
              let id = value["requestId"]?.string, Self.identifier(id),
              let command = value["command"]?.string, Self.commands.contains(command),
              let args = value["args"]?.object else { throw ConnectionRefusal.invalid }
        switch command {
        case "bridge.hello":
            guard args == ["minVersion": .integer(1), "maxVersion": .integer(1)] else { throw ConnectionRefusal.invalid }
        case "client.connectSaved", "client.removeSaved":
            guard Set(args.keys) == ["computerRef"], let ref = args["computerRef"]?.string,
                  Self.identifier(ref) else { throw ConnectionRefusal.invalid }
        default:
            guard args.isEmpty else { throw ConnectionRefusal.invalid }
        }
        self.id = id; self.command = command
    }

    static func identifier(_ value: String) -> Bool {
        (1...64).contains(value.utf8.count) && value.utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 95 || $0 == 45
        }
    }

    static func reply(id: String, result: [String: Any]? = nil, error: String = "unavailable") -> String {
        var value: [String: Any] = ["protocol": "happyranch.connection", "version": 1, "requestId": id, "ok": result != nil]
        if let result { value["result"] = result }
        else { value["error"] = ["code": error, "effect": "none", "recovery": "refresh"] }
        guard let bytes = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              bytes.count <= 32768 else {
            return reply(id: "r0", error: "invalidRequest")
        }
        return String(decoding: bytes, as: UTF8.self)
    }
}

/// A literal bundle inventory, re-read using descriptor-relative no-follow walks
/// and verified again at delivery. Neither caller URLs nor preferences pick root.
struct ConnectionAssets {
    static let origin = "happyranch-shell://connection"
    static let entry = origin + "/index.html"
    static let csp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    let root: URL
    let resources: [String: Record]
    struct Record { let bytes: Int; let digest: String }

    init(root: URL) throws {
        self.root = root
        let raw = try Self.read(root: root, path: "resource-manifest.json", limit: 1024 * 1024)
        guard let text = String(data: raw, encoding: .utf8),
              let manifest = try ConnectionJSON.parse(text, limit: 1024 * 1024).object,
              Set(manifest.keys) == ["version", "entry", "resources"],
              manifest["version"] == .integer(1), manifest["entry"] == .string("index.html"),
              let rows = manifest["resources"]?.array, (1...256).contains(rows.count) else { throw ConnectionRefusal.invalid }
        var records: [String: Record] = [:]
        for row in rows {
            guard let item = row.object, Set(item.keys) == ["path", "bytes", "sha256"],
                  let path = item["path"]?.string, Self.literal(path), path != "resource-manifest.json",
                  records[path] == nil, let size = item["bytes"]?.integer, (0...16777216).contains(size),
                  let digest = item["sha256"]?.string, digest.utf8.count == 64,
                  digest.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { throw ConnectionRefusal.invalid }
            records[path] = Record(bytes: Int(size), digest: digest)
        }
        guard records["index.html"] != nil, records.values.reduce(0, { $0 + $1.bytes }) <= 33554432 else { throw ConnectionRefusal.invalid }
        self.resources = records
        var actual: Set<String> = []
        var enumerationFailed = false
        guard let iterator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey],
            options: [], errorHandler: { _, _ in enumerationFailed = true; return false }) else { throw ConnectionRefusal.invalid }
        var allowedDirectories: Set<String> = []
        for path in records.keys {
            var parts = path.split(separator: "/").map(String.init)
            parts.removeLast()
            while !parts.isEmpty { allowedDirectories.insert(parts.joined(separator: "/")); parts.removeLast() }
        }
        for case let url as URL in iterator {
            let flags = try url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey])
            let relative = url.pathComponents.dropFirst(root.pathComponents.count).joined(separator: "/")
            guard flags.isSymbolicLink == false else { throw ConnectionRefusal.invalid }
            if flags.isDirectory == true {
                guard allowedDirectories.contains(relative) else { throw ConnectionRefusal.invalid }
            } else {
                guard flags.isRegularFile == true,
                      relative == "resource-manifest.json" || records[relative] != nil else { throw ConnectionRefusal.invalid }
                actual.insert(relative)
            }
        }
        guard !enumerationFailed, actual == Set(records.keys).union(["resource-manifest.json"]) else { throw ConnectionRefusal.invalid }
        for path in records.keys { _ = try asset(path) }
    }

    static func literal(_ path: String) -> Bool {
        !path.isEmpty && path.utf8.count <= 256 && path.utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || [95, 46, 47, 45].contains($0)
        } && path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }

    func resolve(_ url: URL) throws -> (Data, String) {
        let raw = url.absoluteString
        guard raw.hasPrefix(Self.origin + "/") else { throw ConnectionRefusal.invalid }
        let path = String(raw.dropFirst(Self.origin.count + 1))
        guard Self.literal(path), raw == Self.origin + "/" + path else { throw ConnectionRefusal.invalid }
        let mime: String
        switch (path as NSString).pathExtension {
        case "html": mime = "text/html; charset=utf-8"
        case "js": mime = "text/javascript; charset=utf-8"
        case "css": mime = "text/css; charset=utf-8"
        case "woff2": mime = "font/woff2"
        case "woff": mime = "font/woff"
        case "ttf": mime = "font/ttf"
        case "txt": mime = "text/plain; charset=utf-8"
        default: mime = "application/octet-stream"
        }
        return (try asset(path), mime)
    }

    func asset(_ path: String) throws -> Data {
        guard let record = resources[path] else { throw ConnectionRefusal.invalid }
        let bytes = try Self.read(root: root, path: path, limit: record.bytes)
        guard bytes.count == record.bytes,
              SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == record.digest else { throw ConnectionRefusal.invalid }
        return bytes
    }

    private static func read(root: URL, path: String, limit: Int) throws -> Data {
        guard root.isFileURL, root.path.hasPrefix("/"), literal(path) else { throw ConnectionRefusal.invalid }
        let parts = root.path.split(separator: "/").map(String.init) + path.split(separator: "/").map(String.init)
        var descriptor = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw ConnectionRefusal.invalid }
        defer { close(descriptor) }
        for (index, part) in parts.enumerated() {
            let flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | (index == parts.count - 1 ? 0 : O_DIRECTORY)
            let next = openat(descriptor, part, flags)
            guard next >= 0 else { throw ConnectionRefusal.invalid }
            close(descriptor); descriptor = next
        }
        var before = stat(), after = stat()
        guard fstat(descriptor, &before) == 0, (before.st_mode & S_IFMT) == S_IFREG,
              before.st_size >= 0, before.st_size <= limit else { throw ConnectionRefusal.invalid }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 16384)
        while data.count <= limit {
            let count = Darwin.read(descriptor, &buffer, min(buffer.count, limit + 1 - data.count))
            guard count >= 0 else { throw ConnectionRefusal.invalid }
            if count == 0 { break }
            data.append(contentsOf: buffer.prefix(count))
        }
        guard fstat(descriptor, &after) == 0, data.count == before.st_size, data.count <= limit,
              before.st_ino == after.st_ino, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec, before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec, before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec else { throw ConnectionRefusal.invalid }
        return data
    }
}
