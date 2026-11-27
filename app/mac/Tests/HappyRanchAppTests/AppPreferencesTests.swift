import XCTest
import Foundation
import Darwin
import CryptoKit
import HappyRanchSupervisor
@testable import HappyRanchApp

final class AppPreferencesTests: XCTestCase {
    private func directory() throws -> URL {
        // Foundation can strip /private and reintroduce the /var symlink.
        // The production stores deliberately refuse symlinked parent paths.
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    @MainActor
    private func scheme(_ root: URL, locale: AppLocalePreference) throws -> AppScheme {
        let assets = root.appendingPathComponent("assets", isDirectory: true)
        try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
        let html = Data("hello".utf8)
        try html.write(to: assets.appendingPathComponent("index.html"))
        let digest = SHA256.hash(data: html).map { String(format: "%02x", $0) }.joined()
        try JSONSerialization.data(withJSONObject: ["version": 1, "entry": "index.html", "resources": [
            ["path": "index.html", "bytes": html.count, "sha256": digest]]])
            .write(to: assets.appendingPathComponent("resource-manifest.json"))
        let policy = root.appendingPathComponent("policy.json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        return try AppScheme(root: assets, transport: nil, remote: false, policyURL: policy, localePreference: locale)
    }

    @MainActor
    func testLocaleWriteReplyPublicationAndReentrantChoicesStayOrdered() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        try AppPreferenceFile(directory: root, name: "locale").write("en")
        let locale = AppLocalePreference(directory: root)
        let scheme = try scheme(root, locale: locale)
        defer { scheme.close() }
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        XCTAssertNil(scheme.selectTransport(id: "local", transport: SwitchTransport(), remote: false))
        let generation = scheme.generation
        var trace: [String] = []
        scheme.observe = { event, _ in
            if event == "native_state_changed" { trace.append("event:\(locale.snapshot.current)") }
        }
        scheme.setLocale(#"{"schema":2,"locale":"zh-CN"}"#) { raw, error in
            XCTAssertNil(error)
            let object = (raw as? String).flatMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
            XCTAssertEqual((object?["locale"] as? [String: Any])?["persisted"] as? Bool, true)
            XCTAssertEqual(AppLocalePreference(directory: root).snapshot.current, "zh-CN", "durable file exists before reply")
            let status = try? JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any]
            XCTAssertEqual((status?["target"] as? [String: Any])?["name"] as? String, "此电脑")
            XCTAssertFalse(locale.nativeChinese, "web copy changes while native copy retains launch locale")
            trace.append("reply:zh-CN")
            scheme.setLocale(#"{"schema":2,"locale":"en"}"#) { _, error in
                XCTAssertNil(error)
                XCTAssertEqual(AppLocalePreference(directory: root).snapshot.current, "en")
                trace.append("reply:en")
            }
        }
        XCTAssertEqual(trace, ["reply:zh-CN", "event:zh-CN", "reply:en", "event:en"])
        XCTAssertEqual(locale.snapshot, .init(current: "en", persisted: true))
        XCTAssertEqual(locale.launchLocale, "en")
        XCTAssertEqual(scheme.generation, generation, "language cannot replace target or Main")
        XCTAssertEqual(try String(contentsOf: root.appendingPathComponent("locale"), encoding: .utf8), "en\n")
        XCTAssertFalse(try FileManager.default.contentsOfDirectory(atPath: root.path).contains { $0.hasPrefix(".locale-") })
    }

    @MainActor
    func testQueuedLocaleWaitsForDeliveredPublicationAndRejectsRetiredDocument() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let preference = AppLocalePreference(directory: root)
        var complete: AppLocalePreference.Completion?
        var current = true, replies = 0, events = 0
        preference.call(#"{"schema":2,"locale":"zh-CN"}"#, current: { true }, reply: { _, _ in replies += 1 },
                        publish: { done in events += 1; complete = done })
        preference.call(#"{"schema":2,"locale":"en"}"#, current: { current }, reply: { value, error in
            XCTAssertNil(value); XCTAssertTrue(error?.contains("documentChanged") == true); replies += 1
        }, publish: { done in events += 1; done() })
        XCTAssertEqual(replies, 1, "later reply waits for delivered publication")
        XCTAssertEqual(AppLocalePreference(directory: root).snapshot.current, "zh-CN", "queued write must wait too")
        current = false
        try XCTUnwrap(complete)()
        XCTAssertEqual(replies, 2); XCTAssertEqual(events, 1)
        XCTAssertEqual(AppLocalePreference(directory: root).snapshot.current, "zh-CN", "retired queued choice has no disk effect")
    }

    @MainActor
    func testLocaleAtomicReplacementAndNextLaunchNativeLanguage() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = AppPreferenceFile(directory: root.appendingPathComponent("nested/config"), name: "locale")
        try file.write("en")
        let old = try FileManager.default.attributesOfItem(atPath: file.directory.appendingPathComponent("locale").path)[.systemFileNumber] as? NSNumber
        let preference = AppLocalePreference(directory: file.directory)
        preference.call(#"{"schema":2,"locale":"zh-CN"}"#, current: { true }, reply: { _, error in XCTAssertNil(error) }, publish: { complete in complete() })
        let attributes = try FileManager.default.attributesOfItem(atPath: file.directory.appendingPathComponent("locale").path)
        XCTAssertNotEqual(old, attributes[.systemFileNumber] as? NSNumber, "same-directory replacement, not in-place overwrite")
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertFalse(preference.nativeChinese, "current native menus stay at launch preference")
        XCTAssertTrue(AppLocalePreference(directory: file.directory).nativeChinese)
    }

    @MainActor
    func testRoleSwitchMenuLabelsUseImmutableLaunchLocale() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = AppPreferenceFile(directory: root, name: "locale")
        let cases: [(String, String, String)] = [
            ("en", "Connect to another computer…", "Run HappyRanch on this Mac…"),
            ("zh-CN", "连接到另一台电脑…", "在此 Mac 上运行 HappyRanch…")
        ]
        for (launchLocale, homeLabel, clientLabel) in cases {
            try file.write(launchLocale)
            let preference = AppLocalePreference(directory: root)
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .home, locale: preference), homeLabel)
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .client, locale: preference), clientLabel)

            let nextLocale = launchLocale == "en" ? "zh-CN" : "en"
            preference.call("{\"schema\":2,\"locale\":\"\(nextLocale)\"}", current: { true }, reply: { _, error in
                XCTAssertNil(error)
            }, publish: { complete in complete() })
            XCTAssertEqual(preference.snapshot, .init(current: nextLocale, persisted: true))
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .home, locale: preference), homeLabel,
                           "live language changes leave the running process's native menu fixed")
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .client, locale: preference), clientLabel)

            let nextLaunch = AppLocalePreference(directory: root)
            let (_, nextHomeLabel, nextClientLabel) = try XCTUnwrap(cases.first { $0.0 == nextLocale })
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .home, locale: nextLaunch), nextHomeLabel)
            XCTAssertEqual(AppDelegate.switchRoleMenuLabel(for: .client, locale: nextLaunch), nextClientLabel)
        }
    }

    @MainActor
    func testLocaleWriteFailureKeepsSessionChoiceAndPublishesFalse() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root.appendingPathComponent("locale"), withIntermediateDirectories: false)
        let preference = AppLocalePreference(directory: root)
        let scheme = try scheme(root, locale: preference)
        defer { scheme.close() }
        var replies = 0, events = 0
        scheme.observe = { event, _ in if event == "native_state_changed" { events += 1 } }
        scheme.setLocale(#"{"schema":2,"locale":"zh-CN"}"#) { raw, error in
            XCTAssertNil(error); replies += 1
            let object = (raw as? String).flatMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
            XCTAssertEqual((object?["locale"] as? [String: Any])?["persisted"] as? Bool, false)
        }
        XCTAssertEqual(replies, 1); XCTAssertEqual(events, 1)
        XCTAssertEqual(preference.snapshot, .init(current: "zh-CN", persisted: false))
        let state = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual((state["locale"] as? [String: Any])?["current"] as? String, "zh-CN")
        XCTAssertEqual((state["locale"] as? [String: Any])?["persisted"] as? Bool, false)
        let fallback = Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh-CN" : "en"
        XCTAssertEqual(AppLocalePreference(directory: root).snapshot.current, fallback)
    }

    @MainActor
    func testInvalidLocaleRequestsPreserveDiskAndSession() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        try AppPreferenceFile(directory: root, name: "locale").write("en")
        let preference = AppLocalePreference(directory: root)
        var publications = 0
        for raw in ["{}", #"{"schema":2,"locale":"fr"}"#, #"{"schema":true,"locale":"en"}"#,
                    #"{"schema":2,"locale":"zh-CN","extra":0}"#, #"{"schema":2,"locale":"en","locale":"zh-CN"}"#, "{"] {
            preference.call(raw, current: { true }, reply: { value, error in
                XCTAssertNil(value); XCTAssertTrue(error?.contains("invalidRequest") == true)
            }, publish: { complete in publications += 1; complete() })
        }
        XCTAssertEqual(publications, 0)
        XCTAssertEqual(preference.snapshot, .init(current: "en", persisted: true))
        XCTAssertEqual(try String(contentsOf: root.appendingPathComponent("locale"), encoding: .utf8), "en\n")
    }

    @MainActor
    func testUnsafeAndOversizedRecordsRefuseWithoutFollowingLinks() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        let outside = root.appendingPathComponent("outside")
        try Data("zh-CN\n".utf8).write(to: outside)
        let path = root.appendingPathComponent("locale")
        try FileManager.default.createSymbolicLink(at: path, withDestinationURL: outside)
        XCTAssertFalse(AppLocalePreference(directory: root).snapshot.persisted)
        try FileManager.default.removeItem(at: path)
        try Data(repeating: 65, count: 129).write(to: path)
        XCTAssertFalse(AppLocalePreference(directory: root).snapshot.persisted)
        let link = root.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: root)
        XCTAssertEqual(AppPreferenceFile(directory: link, name: "locale").read(), .invalid)
        XCTAssertThrowsError(try AppPreferenceFile(directory: link, name: "locale").write("en"))
        XCTAssertEqual(try String(contentsOf: outside, encoding: .utf8), "zh-CN\n")
    }

    @MainActor
    func testStartupRoundTripClearAndInvalidNeverBecomeMissing() throws {
        let root = try directory()
        defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertEqual(AppStartupSelection(directory: root).choice, .local)
        let store = AppStartupSelection(directory: root), a = UUID(), b = UUID()
        for choice in [AppStartupSelection.Choice.local, .saved(a), .unsaved] {
            store.commit(choice)
            XCTAssertTrue(store.persisted)
            XCTAssertEqual(AppStartupSelection(directory: root).choice, choice)
        }
        store.commit(.saved(a)); store.clear(b)
        XCTAssertEqual(AppStartupSelection(directory: root).choice, .saved(a))
        store.clear(a)
        XCTAssertEqual(AppStartupSelection(directory: root).choice, .local)
        store.commit(.unsaved, profile: a); store.clear(b)
        XCTAssertEqual(store.choice, .unsaved)
        store.clear(a)
        XCTAssertEqual(AppStartupSelection(directory: root).choice, .local)
        try Data("bad\n".utf8).write(to: root.appendingPathComponent("startup-selection"))
        let invalid = AppStartupSelection(directory: root)
        XCTAssertEqual(invalid.choice, .unavailable); XCTAssertFalse(invalid.persisted)
        let failedParent = root.appendingPathComponent("locale")
        try Data("file".utf8).write(to: failedParent)
        let failed = AppStartupSelection(directory: failedParent)
        XCTAssertEqual(failed.choice, .unavailable)
        failed.commit(.saved(a))
        XCTAssertEqual(failed.choice, .saved(a)); XCTAssertFalse(failed.persisted)
    }
}
