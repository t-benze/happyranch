import Foundation
import Darwin

/// Small nonsecret installation records, separate from daemon config and custody.
/// Descriptor-relative traversal refuses symlinks, including parent components.
struct AppPreferenceFile {
    enum Read: Equatable { case missing, value(String), invalid }
    let directory: URL
    let name: String
    static var directory: URL {
        URL(fileURLWithPath: NSHomeDirectory()).resolvingSymlinksInPath()
            .appendingPathComponent("Library/Application Support/HappyRanchApp", isDirectory: true)
    }

    private func openDirectory(create: Bool) throws -> Int32 {
        guard directory.path.hasPrefix("/"), !directory.pathComponents.contains(".."),
              ["locale", "startup-selection"].contains(name) else { throw ConnectionRefusal.invalid }
        var fd = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else { throw POSIXError(.EIO) }
        do {
            for part in directory.pathComponents.dropFirst() {
                if create, mkdirat(fd, part, 0o700) != 0, errno != EEXIST { throw POSIXError(.EIO) }
                let child = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
                guard child >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
                if create, fsync(fd) != 0 { Darwin.close(child); throw POSIXError(.EIO) }
                Darwin.close(fd); fd = child
            }
            return fd
        } catch { Darwin.close(fd); throw error }
    }

    func read() -> Read {
        let parent: Int32
        do { parent = try openDirectory(create: false) }
        catch let error as POSIXError where error.code == .ENOENT { return .missing }
        catch { return .invalid }
        defer { Darwin.close(parent) }
        let fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { return errno == ENOENT ? .missing : .invalid }
        defer { Darwin.close(fd) }
        var before = stat(), after = stat()
        guard fstat(fd, &before) == 0, before.st_mode & S_IFMT == S_IFREG,
              before.st_size <= 128, before.st_size > 0 else { return .invalid }
        var bytes = [UInt8](repeating: 0, count: 129)
        let count = Darwin.read(fd, &bytes, bytes.count)
        guard count == before.st_size, fstat(fd, &after) == 0,
              before.st_size == after.st_size, before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec,
              before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec,
              let text = String(bytes: bytes.prefix(count), encoding: .utf8) else { return .invalid }
        return .value(text.hasSuffix("\n") ? String(text.dropLast()) : text)
    }

    /// No buffered I/O: complete write, file fsync, atomic rename, directory fsync.
    /// A post-replacement failure is still unconfirmed durability.
    func write(_ value: String) throws {
        guard value.utf8.count < 128 else { throw ConnectionRefusal.invalid }
        let parent = try openDirectory(create: true)
        defer { Darwin.close(parent) }
        let temporary = ".\(name)-\(UUID().uuidString)"
        let fd = openat(parent, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw POSIXError(.EIO) }
        defer { Darwin.close(fd); unlinkat(parent, temporary, 0) }
        let data = Array((value + "\n").utf8)
        try data.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                let written = Darwin.write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
                if written < 0, errno == EINTR { continue }
                guard written > 0 else { throw POSIXError(.EIO) }
                offset += written
            }
        }
        guard fsync(fd) == 0, renameat(parent, temporary, parent, name) == 0,
              fsync(parent) == 0 else { throw POSIXError(.EIO) }
    }
}

@MainActor
final class AppLocalePreference {
    static let shared = AppLocalePreference(directory: AppPreferenceFile.directory)
    struct Snapshot: Equatable {
        let current: String
        let persisted: Bool
        var json: [String: Any] { ["current": current, "persisted": persisted] }
    }
    private let file: AppPreferenceFile
    private(set) var snapshot: Snapshot
    /// Immutable native chrome language for this process; setters affect web only.
    let launchLocale: String
    var nativeChinese: Bool { launchLocale == "zh-CN" }
    private var delivering = false
    typealias Completion = @MainActor @Sendable () -> Void
    private var pending: [(String, () -> Bool, (String?, String?) -> Void, (@escaping Completion) -> Void)] = []

    init(directory: URL) {
        file = AppPreferenceFile(directory: directory, name: "locale")
        if case .value(let value) = file.read(), ["en", "zh-CN"].contains(value) {
            snapshot = Snapshot(current: value, persisted: true)
        } else {
            snapshot = Snapshot(current: Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh-CN" : "en", persisted: false)
        }
        launchLocale = snapshot.current
    }

    /// Main admits synchronously. Reentrant deliveries queue behind publication;
    /// no detached writer can overtake a newer choice, reply or full-state event.
    func call(_ raw: String, current: @escaping () -> Bool,
              reply: @escaping (String?, String?) -> Void, publish: @escaping (@escaping Completion) -> Void) {
        guard let object = try? ConnectionJSON.parse(raw).object,
              Set(object.keys) == ["schema", "locale"], object["schema"] == .integer(2),
              let locale = object["locale"]?.string, ["en", "zh-CN"].contains(locale) else {
            reply(nil, MacLocalControls.error("invalidRequest", recovery: "none")); return
        }
        guard pending.count < 32 else { reply(nil, MacLocalControls.error("busy", recovery: "explicitRetry")); return }
        pending.append((locale, current, reply, publish))
        next()
    }

    private func next() {
        guard !delivering, !pending.isEmpty else { return }
        let (locale, current, reply, publish) = pending.removeFirst()
        guard current() else {
            reply(nil, MacLocalControls.error("documentChanged", recovery: "refresh")); next(); return
        }
        delivering = true
        snapshot = Snapshot(current: locale, persisted: false)
        do { try file.write(locale); snapshot = Snapshot(current: locale, persisted: true) } catch { /* session-only */ }
        let data = try? JSONSerialization.data(withJSONObject: ["schema": 2, "locale": snapshot.json])
        reply(String(decoding: data ?? Data(), as: UTF8.self), nil)
        publish { [weak self] in
            guard let self else { return }
            delivering = false
            next()
        }
    }
}

@MainActor
final class AppStartupSelection {
    static let shared = AppStartupSelection(directory: AppPreferenceFile.directory)
    enum Choice: Equatable {
        case local, saved(UUID), unsaved, unavailable
        var record: String {
            switch self {
            case .local: return "local"
            case .saved(let id): return "remote:\(id.uuidString.lowercased())"
            case .unsaved, .unavailable: return "remote:unsaved"
            }
        }
    }
    private let file: AppPreferenceFile
    private(set) var choice: Choice
    private(set) var persisted: Bool
    private var confirmed: Choice?
    private var sessionProfile: UUID?
    private var confirmedProfile: UUID?
    init(directory: URL) {
        file = AppPreferenceFile(directory: directory, name: "startup-selection")
        switch file.read() {
        case .missing: choice = .local; persisted = true
        case .value("local"): choice = .local; persisted = true
        case .value("remote:unsaved"): choice = .unsaved; persisted = true
        case .value(let value):
            if value.hasPrefix("remote:"), let id = UUID(uuidString: String(value.dropFirst(7))),
               value == "remote:\(id.uuidString.lowercased())" { choice = .saved(id); persisted = true }
            else { choice = .unavailable; persisted = false }
        case .invalid: choice = .unavailable; persisted = false
        }
        confirmed = persisted ? choice : nil
        if case .saved(let id) = choice { sessionProfile = id; confirmedProfile = id }
    }
    func commit(_ choice: Choice, profile: UUID? = nil) {
        self.choice = choice; persisted = false
        if case .saved(let id) = choice { sessionProfile = id }
        else { sessionProfile = choice == .unsaved ? profile : nil }
        do {
            try file.write(choice.record)
            persisted = true; confirmed = choice; confirmedProfile = sessionProfile
        } catch { /* retain actual session choice */ }
    }
    /// Also clear an older durable reference after a newer session-only choice.
    func clear(_ id: UUID) {
        if sessionProfile == id || confirmedProfile == id || choice == .saved(id) || confirmed == .saved(id) { commit(.local) }
    }
}
