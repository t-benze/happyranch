import Foundation
import CryptoKit
import Security

/// Closed failures only: neither decoder diagnostics nor input bytes escape.
public enum RemoteInviteError: String, Error, Sendable, LocalizedError {
    case oversized, prefix, encoding, schema, unsupported, url, certificate, fingerprint, san, time, entropy, reapprovalRequired

    public var category: String { self == .time ? "expired_invite" : "malformed_invite" }
    public var errorDescription: String? {
        self == .reapprovalRequired ? "New invite and approval required" :
        self == .time ? "Invite expired or not yet valid — create new invite on Linux" :
            "Invite could not be validated — create new invite on Linux"
    }
}

/// Contains no secrets. Equality binds saved access to one exact trust profile.
public struct SavedHomeTrust: Codable, Equatable, Sendable {
    public let version: Int
    public let dialIP: String
    public let homeID: String
    public let controlPlaneID: String
    public let connectorID: String
    public let controlURL: String
    public let homeEndpoint: String
    public let caDER: Data
    public let caSHA256: String

    public init(version: Int = 2, homeID: String, controlPlaneID: String, connectorID: String,
                controlURL: String, dialIP: String, homeEndpoint: String, caDER: Data, caSHA256: String) {
        self.version = version; self.homeID = homeID; self.controlPlaneID = controlPlaneID
        self.connectorID = connectorID; self.dialIP = dialIP
        self.controlURL = controlURL == RemoteInvite.controlURL + "/" ? RemoteInvite.controlURL : controlURL
        self.homeEndpoint = homeEndpoint; self.caDER = caDER; self.caSHA256 = caSHA256
    }

    private enum CodingKeys: String, CodingKey {
        case version, dialIP, homeID, controlPlaneID, connectorID, controlURL, homeEndpoint, caDER, caSHA256
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard try c.decodeIfPresent(Int.self, forKey: .version) == 2 else {
            throw SavedAccessFailure.reapprovalRequired
        }
        self.init(homeID: try c.decode(String.self, forKey: .homeID),
            controlPlaneID: try c.decode(String.self, forKey: .controlPlaneID),
            connectorID: try c.decode(String.self, forKey: .connectorID),
            controlURL: try c.decode(String.self, forKey: .controlURL),
            dialIP: try c.decode(String.self, forKey: .dialIP),
            homeEndpoint: try c.decode(String.self, forKey: .homeEndpoint),
            caDER: try c.decode(Data.self, forKey: .caDER), caSHA256: try c.decode(String.self, forKey: .caSHA256))
        try validateVersionAndAddress()
    }

    func validateVersionAndAddress() throws {
        guard version == 2 else { throw SavedAccessFailure.reapprovalRequired }
        _ = try RemoteInvite.homeControlHost(controlURL)
        _ = try RemoteInvite.homeDialIP(dialIP)
    }
}

/// Memory-only envelope. Never serialize this type or pass it to diagnostics.
/// Explicit display/debug/mirror representations prevent synthesized secret output.
public struct RemoteInvite: Sendable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let trust: SavedHomeTrust
    public let issuedAt: Date
    public let expiresAt: Date
    private let inviteIDBytes: Data
    private let joinKeyBytes: Data
    private let pairSecretBytes: Data

    public var description: String { "RemoteInvite(<redacted>)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["contents": "<redacted>"]) }

    /// Explicit transport handoff; the caller must not log or persist these bytes.
    public func withSecrets<T>(_ body: (Data, Data, Data) throws -> T) rethrows -> T {
        try body(inviteIDBytes, joinKeyBytes, pairSecretBytes)
    }

    /// Normative v2 wire literals are pinned in spec section 6. Zero clock skew:
    /// issued <= now < expires, and a maximum five-minute issuance window.
    public static func parse(_ input: String, now: Date = Date()) throws -> Self {
        guard input.utf8.count <= 5500 else { throw RemoteInviteError.oversized }
        if input.hasPrefix("hrinvite1:") { throw RemoteInviteError.reapprovalRequired }
        guard input.hasPrefix("hrinvite2:") else { throw RemoteInviteError.prefix }
        let bytes = try Base64URL.decode(String(input.dropFirst(10)))
        guard bytes.count <= 4096 else { throw RemoteInviteError.oversized }
        var reader = InviteJSON(bytes: Array(bytes))
        let fields = try reader.document()
        guard Set(fields.keys) == Set(["v", "mode", "control_url", "control_plane_id", "control_tls",
            "home_endpoint", "home_id", "connector_id", "invite_id", "join_key", "pair_secret", "issued_at", "expires_at"]) else {
            throw RemoteInviteError.schema
        }
        guard case .number(let version) = fields["v"] else { throw RemoteInviteError.schema }
        guard version == 2 else { throw RemoteInviteError.unsupported }
        let mode = try fields.text("mode")
        guard case .object(let tls) = fields["control_tls"] else { throw RemoteInviteError.schema }
        let kind = try tls.text("kind")
        switch kind {
        case "system":
            guard Set(tls.keys) == ["kind"] else { throw RemoteInviteError.schema }
            // R3 has no UI/intake path. Even a well-shaped system-trust envelope refuses.
            throw RemoteInviteError.unsupported
        case "local_ca":
            guard mode == "home" else { throw RemoteInviteError.unsupported }
            guard Set(tls.keys) == Set(["kind", "ca_der", "sha256", "dial_ip"]) else { throw RemoteInviteError.schema }
        default: throw RemoteInviteError.unsupported
        }
        let dialIP = try tls.text("dial_ip")
        _ = try homeDialIP(dialIP)
        let control = try fields.text("control_url")
        _ = try homeControlHost(control)
        let endpoint = try fields.text("home_endpoint")
        // The runtime endpoint is tailnet host:443, never the LAN control endpoint.
        guard let target = URLComponents(string: "https://" + endpoint),
              let host = target.host, !host.isEmpty, target.port == 443, target.user == nil, target.password == nil,
              target.query == nil, target.fragment == nil, target.path.isEmpty,
              target.string == "https://" + endpoint, !endpoint.contains("%"),
              endpoint.utf8.allSatisfy({ $0 > 32 && $0 < 127 }) else { throw RemoteInviteError.url }
        let ca: Data
        do { ca = try Base64URL.decode(tls.text("ca_der")) } catch { throw RemoteInviteError.certificate }
        let fingerprint = try tls.text("sha256")
        guard fingerprint.count == 64, fingerprint.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              SHA256.hash(data: ca).map({ String(format: "%02x", $0) }).joined() == fingerprint else {
            throw RemoteInviteError.fingerprint
        }
        try InviteCertificate.validate(ca, now: now)
        guard case .number(let issued) = fields["issued_at"], case .number(let expires) = fields["expires_at"],
              issued >= 0, expires > issued, expires - issued <= 300,
              Double(issued) <= now.timeIntervalSince1970, now.timeIntervalSince1970 < Double(expires) else {
            throw RemoteInviteError.time
        }
        let inviteID: Data
        let pairSecret: Data
        do {
            inviteID = try Base64URL.decode(fields.text("invite_id"))
            pairSecret = try Base64URL.decode(fields.text("pair_secret"))
        } catch { throw RemoteInviteError.entropy }
        guard inviteID.count == 16, pairSecret.count == 32 else { throw RemoteInviteError.entropy }
        let join = try fields.text("join_key")
        guard join.utf8.count == 48, join.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
            throw RemoteInviteError.entropy
        }
        func identity(_ name: String) throws -> String {
            let value = try fields.text(name)
            guard !value.isEmpty, value.utf8.count <= 128,
                  value.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }) else {
                throw RemoteInviteError.schema
            }
            return value
        }
        return Self(trust: SavedHomeTrust(homeID: try identity("home_id"), controlPlaneID: try identity("control_plane_id"),
            connectorID: try identity("connector_id"), controlURL: control, dialIP: dialIP, homeEndpoint: endpoint, caDER: ca, caSHA256: fingerprint),
            issuedAt: Date(timeIntervalSince1970: Double(issued)), expiresAt: Date(timeIntervalSince1970: Double(expires)),
            inviteIDBytes: inviteID, joinKeyBytes: Data(join.utf8), pairSecretBytes: pairSecret)
    }

    static let controlURL = "https://control.happyranch.invalid:28443"

    static func homeControlHost(_ text: String) throws -> String {
        guard text == controlURL || text == controlURL + "/" else { throw RemoteInviteError.url }
        return "control.happyranch.invalid"
    }

    static func homeDialIP(_ text: String) throws -> [UInt8] {
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        let ip = parts.compactMap { UInt8($0) }
        guard parts.count == 4, ip.count == 4, zip(parts, ip).allSatisfy({ String($0.1) == $0.0 }),
              ip[0] == 10 || (ip[0] == 172 && (16...31).contains(ip[1])) || (ip[0] == 192 && ip[1] == 168) else {
            throw RemoteInviteError.url
        }
        return ip
    }
}

enum Base64URL {
    static func encode(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    static func decode(_ text: String) throws -> Data {
        guard !text.isEmpty, text.utf8.allSatisfy({ (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 }),
              text.utf8.count % 4 != 1,
              let data = Data(base64Encoded: text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/") + String(repeating: "=", count: (4 - text.utf8.count % 4) % 4)),
              encode(data) == text else { throw RemoteInviteError.encoding }
        return data
    }
}

/// Small bounded JSON grammar: objects, strings and integer tokens only.
/// Decoding keys before insertion catches even escaped duplicate names.
indirect enum InviteValue { case text(String), number(Int64), object([String: InviteValue]) }
extension Dictionary where Key == String, Value == InviteValue {
    func text(_ key: String) throws -> String {
        guard case .text(let value) = self[key] else { throw RemoteInviteError.schema }
        return value
    }
}
struct InviteJSON {
    let bytes: [UInt8]
    var index = 0
    mutating func whitespace() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
    mutating func take(_ byte: UInt8) throws {
        whitespace()
        guard index < bytes.count, bytes[index] == byte else { throw RemoteInviteError.schema }
        index += 1
    }
    mutating func document() throws -> [String: InviteValue] {
        let result = try object(depth: 0)
        whitespace()
        guard index == bytes.count else { throw RemoteInviteError.schema }
        return result
    }
    mutating func string() throws -> String {
        whitespace()
        let start = index
        try take(34)
        var escaped = false
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if !escaped && byte == 34 {
                guard let value = try? JSONSerialization.jsonObject(with: Data(bytes[start..<index]), options: .fragmentsAllowed) as? String else { throw RemoteInviteError.schema }
                return value
            }
            if !escaped && byte == 92 { escaped = true } else { escaped = false }
        }
        throw RemoteInviteError.schema
    }
    mutating func object(depth: Int) throws -> [String: InviteValue] {
        guard depth <= 1 else { throw RemoteInviteError.schema }
        try take(123)
        var fields: [String: InviteValue] = [:]
        whitespace()
        if index < bytes.count && bytes[index] == 125 { index += 1; return fields }
        while true {
            let key = try string()
            guard fields[key] == nil else { throw RemoteInviteError.schema }
            try take(58)
            whitespace()
            guard index < bytes.count else { throw RemoteInviteError.schema }
            if bytes[index] == 34 { fields[key] = .text(try string()) }
            else if bytes[index] == 123 { fields[key] = .object(try object(depth: depth + 1)) }
            else {
                let start = index
                while index < bytes.count && (48...57).contains(bytes[index]) { index += 1 }
                let token = String(decoding: bytes[start..<index], as: UTF8.self)
                guard let value = Int64(token), token == String(value) else { throw RemoteInviteError.schema }
                fields[key] = .number(value)
            }
            whitespace()
            guard index < bytes.count else { throw RemoteInviteError.schema }
            if bytes[index] == 125 { index += 1; return fields }
            try take(44)
        }
    }
}

/// Security parses the certificate; this DER reader locates typed X.509
/// extensions, never a substring or localized certificate-property label.
private enum InviteCertificate {
    static func validate(_ data: Data, now: Date) throws {
        guard let certificate = SecCertificateCreateWithData(nil, data as CFData),
              SecCertificateCopyData(certificate) as Data == data else { throw RemoteInviteError.certificate }
        var root = DER(bytes: Array(data))
        let cert = try root.read(0x30)
        guard root.empty else { throw RemoteInviteError.certificate }
        var body = DER(bytes: cert)
        var tbs = DER(bytes: try body.read(0x30))
        if tbs.peek == 0xa0 { _ = try tbs.read(0xa0) }
        // serial, signature, issuer, validity, subject, subjectPublicKeyInfo
        _ = try tbs.read(0x02)
        _ = try tbs.read(0x30)
        _ = try tbs.read(0x30)
        var validity = DER(bytes: try tbs.read(0x30))
        let notBefore = try validity.date()
        let notAfter = try validity.date()
        guard validity.empty, notBefore <= now, now < notAfter else { throw RemoteInviteError.certificate }
        _ = try tbs.read(0x30)
        _ = try tbs.read(0x30)
        while tbs.peek == 0x81 || tbs.peek == 0x82 { _ = try tbs.read(tbs.peek!) }
        var wrapper = DER(bytes: try tbs.read(0xa3))
        guard tbs.empty else { throw RemoteInviteError.certificate }
        var extensions = DER(bytes: try wrapper.read(0x30))
        guard wrapper.empty else { throw RemoteInviteError.certificate }
        var seen: Set<Data> = []
        var isCA = false
        while !extensions.empty {
            var ext = DER(bytes: try extensions.read(0x30))
            let oid = try ext.read(0x06)
            guard seen.insert(Data(oid)).inserted else { throw RemoteInviteError.certificate }
            if ext.peek == 0x01 { _ = try ext.read(0x01) }
            let content = try ext.read(0x04)
            guard ext.empty else { throw RemoteInviteError.certificate }
            if oid == [0x55, 0x1d, 0x13] {
                var outer = DER(bytes: content)
                var constraints = DER(bytes: try outer.read(0x30))
                if constraints.peek == 0x01 { isCA = try constraints.read(0x01) == [0xff] }
                guard outer.empty else { throw RemoteInviteError.certificate }
            }
        }
        guard isCA else { throw RemoteInviteError.certificate }
    }
    private struct DER {
        let bytes: [UInt8]
        var index = 0
        mutating func date() throws -> Date {
            guard let tag = peek, tag == 0x17 || tag == 0x18 else { throw RemoteInviteError.certificate }
            let value = try read(tag)
            guard value.count == (tag == 0x17 ? 13 : 15), value.last == 90,
                  value.dropLast().allSatisfy({ (48...57).contains($0) }) else { throw RemoteInviteError.certificate }
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            formatter.dateFormat = tag == 0x17 ? "yyMMddHHmmss'Z'" : "yyyyMMddHHmmss'Z'"
            formatter.twoDigitStartDate = Date(timeIntervalSince1970: -631152000)
            formatter.isLenient = false
            guard let date = formatter.date(from: String(decoding: value, as: UTF8.self)) else { throw RemoteInviteError.certificate }
            return date
        }
        var empty: Bool { index == bytes.count }
        var peek: UInt8? { index < bytes.count ? bytes[index] : nil }
        mutating func read(_ tag: UInt8) throws -> [UInt8] {
            guard peek == tag, index + 1 < bytes.count else { throw RemoteInviteError.certificate }
            index += 1
            let first = Int(bytes[index]); index += 1
            var count = first
            if first >= 128 {
                let width = first & 127
                guard width > 0, width <= 4, index + width <= bytes.count, bytes[index] != 0 else { throw RemoteInviteError.certificate }
                count = 0
                for _ in 0..<width { count = count * 256 + Int(bytes[index]); index += 1 }
                guard count >= 128 else { throw RemoteInviteError.certificate }
            }
            guard count <= bytes.count - index else { throw RemoteInviteError.certificate }
            defer { index += count }
            return Array(bytes[index..<(index + count)])
        }
    }
}
