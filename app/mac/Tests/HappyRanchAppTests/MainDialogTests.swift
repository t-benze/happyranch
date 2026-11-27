import AppKit
import CryptoKit
import Darwin
import WebKit
import XCTest
@testable import HappyRanchApp

final class MainDialogTests: XCTestCase {
    @MainActor
    private func eventually(_ condition: () async throws -> Bool) async rethrows -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(5))
        while clock.now < deadline {
            if try await condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return false
    }

    @MainActor
    func testDialogRoutingDropsUnsubscribedAndRetiredRequestsWithoutTargetChanges() async throws {
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let assets = root.appendingPathComponent("web", isDirectory: true)
        try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let html = Data("<!doctype html><html><body><main>Main</main></body></html>".utf8)
        try html.write(to: assets.appendingPathComponent("index.html"))
        let hash = SHA256.hash(data: html).map { String(format: "%02x", $0) }.joined()
        try JSONSerialization.data(withJSONObject: ["version": 1, "entry": "index.html", "resources": [
            ["path": "index.html", "bytes": html.count, "sha256": hash]]]).write(to: assets.appendingPathComponent("resource-manifest.json"))
        let policy = root.appendingPathComponent("policy.json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        let scheme = try AppScheme(root: assets, transport: nil, remote: false, policyURL: policy)
        defer { scheme.close() }
        let app = AppDelegate(); app.bindMainScheme(scheme)
        let initial = scheme.generation
        XCTAssertFalse(scheme.requestDialog("settings"), "Loading clicks must be dropped")
        var loads = 0
        let owner = WebView(url: URL(string: AppSchemeRoute.origin + "/")!, transport: nil, remote: false,
                            onLoaded: { _ in loads += 1 })
        let coordinator = owner.makeCoordinator(); coordinator.scheme = scheme
        let configuration = WKWebViewConfiguration(); scheme.configure(configuration)
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 500), configuration: configuration)
        defer { view.navigationDelegate = nil; view.stopLoading() }
        view.navigationDelegate = coordinator
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
        let loaded = await eventually { loads == 1 }; XCTAssertTrue(loaded, "\(coordinator.callbackURLs)")
        XCTAssertFalse(scheme.requestDialog("connection"), "Admission without subscription does not queue")
        _ = try await view.callAsyncJavaScript("window.dialogs = []; window.states = []; window.off = window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw))); window.happyranchConnection.on('app.stateChanged', raw => states.push(JSON.parse(raw)));",
            arguments: [:], in: nil, contentWorld: .page)
        let subscribed = await eventually { scheme.requestDialog("settings") }; XCTAssertTrue(subscribed)
        try await Task.sleep(for: .milliseconds(100))
        let received = try await view.callAsyncJavaScript("return JSON.stringify({dialogs, states});", arguments: [:], in: nil, contentWorld: .page) as? String
        let result = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(received).utf8)) as? [String: Any])
        let dialogs = try XCTUnwrap(result["dialogs"] as? [[String: Any]])
        XCTAssertEqual(dialogs.count, 1)
        let first = try XCTUnwrap(dialogs.first)
        XCTAssertEqual(Set(first.keys), ["schema", "dialog"])
        XCTAssertEqual(first["schema"] as? Int, 2); XCTAssertEqual(first["dialog"] as? String, "settings")
        XCTAssertEqual((result["states"] as? [Any])?.count, 0, "Dialog payload cannot enter state listeners")
        XCTAssertFalse(scheme.requestDialog("preferences"))
        XCTAssertEqual(scheme.generation, initial)
        // The same admitted Main can read the existing command projection.
        let snapshot = try await view.callAsyncJavaScript("return await window.happyranchConnection.call('client.status', '{}');",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let client = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(snapshot).utf8)) as? [String: Any])
        XCTAssertNotNil(client["stage"]); XCTAssertNil(client["protocol"], "Main unwraps the existing v1 result")
        let localRaw = try await view.callAsyncJavaScript("return await window.happyranchConnection.call('local.status', '{}');",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let local = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(localRaw).utf8)) as? [String: Any])
        XCTAssertEqual((local["service"] as? [String: Any])?["mode"] as? String, "appProcess")
        XCTAssertEqual((local["service"] as? [String: Any])?["keepRunning"] as? String, "unavailable")
        let refusal = try await view.callAsyncJavaScript("try { await window.happyranchConnection.call('local.restart', '{\"target\":\"remote\"}'); return ''; } catch (error) { return String(error); }",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let error = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(refusal).utf8)) as? [String: String])
        XCTAssertEqual(error, ["code": "invalidRequest", "effect": "none", "recovery": "none"])
        XCTAssertEqual(scheme.generation, initial)
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        view.reload()
        let reloaded = await eventually { loads == 2 }; XCTAssertTrue(reloaded)
        XCTAssertFalse(scheme.requestDialog("connection"), "Replacement Main has no inherited subscriber")
        _ = try await view.callAsyncJavaScript("window.dialogs = []; window.off = window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw)));",
            arguments: [:], in: nil, contentWorld: .page)
        try await Task.sleep(for: .milliseconds(100))
        let empty = try await view.callAsyncJavaScript("return dialogs.length;", arguments: [:], in: nil, contentWorld: .page) as? Int
        XCTAssertEqual(empty, 0, "Retired/loading clicks are never replayed on subscription")
        let next = await eventually { scheme.requestDialog("connection") }; XCTAssertTrue(next)
        _ = try await view.callAsyncJavaScript("off();", arguments: [:], in: nil, contentWorld: .page)
        let unsubscribed = await eventually { !scheme.requestDialog("settings") }; XCTAssertTrue(unsubscribed)
        // Readmission retains the actual loaded document, dialog and draft.
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        let localTransport = SwitchTransport()
        XCTAssertNil(scheme.selectTransport(id: "local", transport: localTransport, remote: false))
        let localGeneration = scheme.generation
        let context = try await view.callAsyncJavaScript("window.localDraft = 'retained'; window.off = window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw))); return window.__hrAppContext;",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let listening = await eventually { scheme.requestDialog("settings") }; XCTAssertTrue(listening)
        let revision = scheme.localServiceWillChange(starting: true)
        scheme.localServiceDidChange(transport: localTransport, promptURL: nil, health: nil, revision: revision)
        let refreshed = try await view.callAsyncJavaScript("return await window.happyranchConnection.call('app.status', '{}');",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(refreshed).utf8)) as? [String: Any])
        XCTAssertEqual(status["state"] as? String, "ready", "Explicit Recheck repeats only health admission")
        try await Task.sleep(for: .milliseconds(100))
        let preserved = try await view.callAsyncJavaScript("return window.__hrAppContext === context && window.localDraft === 'retained';",
            arguments: ["context": try XCTUnwrap(context)], in: nil, contentWorld: .page) as? Bool
        XCTAssertEqual(preserved, true)
        XCTAssertEqual(loads, 2, "Local readmission must not reload Main")
        XCTAssertTrue(scheme.requestDialog("connection"), "Main keeps its existing dialog listener")
        XCTAssertEqual(scheme.generation, localGeneration)
        XCTAssertNil(scheme.switchRefusal)
        scheme.documentDidStart()
        XCTAssertFalse(scheme.requestDialog("settings"))
        XCTAssertEqual(scheme.generation, localGeneration, "Document retirement never selects a target")
    }

    @MainActor
    func testMainConnectionReopensAcrossMenuRoleSwitchesInTheSameDocument() async throws {
        let preference = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer { UserDefaults.standard.set(preference, forKey: "connectionRolePreference") }
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let assets = root.appendingPathComponent("web", isDirectory: true)
        try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let html = Data("<!doctype html><html><body><main>Main</main></body></html>".utf8)
        try html.write(to: assets.appendingPathComponent("index.html"))
        let hash = SHA256.hash(data: html).map { String(format: "%02x", $0) }.joined()
        try JSONSerialization.data(withJSONObject: ["version": 1, "entry": "index.html", "resources": [
            ["path": "index.html", "bytes": html.count, "sha256": hash]]]).write(to: assets.appendingPathComponent("resource-manifest.json"))
        let policy = root.appendingPathComponent("policy.json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        let scheme = try AppScheme(root: assets, transport: nil, remote: false, policyURL: policy)
        defer { scheme.close() }
        let app = AppDelegate(); app.connectionRolePreference = .home; app.bindMainScheme(scheme)
        let initial = scheme.generation
        var loads = 0
        let owner = WebView(url: URL(string: AppSchemeRoute.origin + "/")!, transport: nil, remote: false,
                            onLoaded: { _ in loads += 1 })
        let coordinator = owner.makeCoordinator(); coordinator.scheme = scheme
        let configuration = WKWebViewConfiguration(); scheme.configure(configuration)
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 500), configuration: configuration)
        defer { view.navigationDelegate = nil; view.stopLoading() }
        view.navigationDelegate = coordinator
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
        let loaded = await eventually { loads == 1 }; XCTAssertTrue(loaded, "\(coordinator.callbackURLs)")
        _ = try await view.callAsyncJavaScript("window.dialogs = []; window.clientEvents = []; window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw))); window.happyranchConnection.on('client.stateChanged', raw => clientEvents.push(JSON.parse(raw))); window.originalContext = window.__hrAppContext;",
            arguments: [:], in: nil, contentWorld: .page)
        let subscribed = await eventually { scheme.requestDialog("connection") }; XCTAssertTrue(subscribed)
        let home = try await view.callAsyncJavaScript("return await window.happyranchConnection.call('bridge.hello', '{\"minVersion\":1,\"maxVersion\":1}');",
            arguments: [:], in: nil, contentWorld: .page) as? String
        XCTAssertFalse(try XCTUnwrap(home).contains("client.acceptInvite"))
        // Model a protected owner in retained Main. Adapter renewal must not
        // manufacture clean owner evidence or a target generation change.
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"prompt","counts":{"dirty":1,"failed":0,"pending":0,"unknown":0}}"#)
        let eventsBeforeClient = try await view.callAsyncJavaScript("return clientEvents.length;",
            arguments: [:], in: nil, contentWorld: .page) as? Int
        let menu = HappyRanchApp()
        menu.executeRoleSwitch(from: .home, in: app)
        XCTAssertEqual(app.connectionRole, .client)
        app.showConnection() // Reopen via the shipping entry, without reload.
        let client = try await view.callAsyncJavaScript("const hello = JSON.parse(await window.happyranchConnection.call('bridge.hello', '{\"minVersion\":1,\"maxVersion\":1}')); const saved = JSON.parse(await window.happyranchConnection.call('client.savedComputers', '{}')); const status = JSON.parse(await window.happyranchConnection.call('client.status', '{}')); let error; try { await window.happyranchConnection.call('client.cancelAttempt', '{}'); } catch (e) { error = JSON.parse(e.message); } return JSON.stringify({hello, saved, status, error});",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let result = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(client).utf8)) as? [String: Any])
        let hello = try XCTUnwrap(result["hello"] as? [String: Any])
        let capabilities = try XCTUnwrap(hello["capabilities"] as? [[String: String]])
        XCTAssertTrue(capabilities.contains { $0["command"] == "client.acceptInvite" })
        XCTAssertNotNil((result["saved"] as? [String: Any])?["savedAccess"])
        XCTAssertNotNil((result["status"] as? [String: Any])?["stage"])
        XCTAssertEqual((result["error"] as? [String: Any])?["code"] as? String, "stateChanged",
                       "The current-role action reaches its state guard, not documentChanged")
        let eventsRestored = try await eventually {
            try await view.callAsyncJavaScript("return clientEvents.length > before;",
                arguments: ["before": try XCTUnwrap(eventsBeforeClient)], in: nil, contentWorld: .page) as? Bool == true
        }
        XCTAssertTrue(eventsRestored, "Restore the existing subscription")
        let eventsAtClient = try await view.callAsyncJavaScript("return clientEvents.length;",
            arguments: [:], in: nil, contentWorld: .page) as? Int
        menu.executeRoleSwitch(from: .client, in: app)
        XCTAssertEqual(app.connectionRole, .home)
        app.showConnection()
        let reopenedEntries = try await eventually {
            try await view.callAsyncJavaScript("return dialogs.length === 3 && clientEvents.length > before;",
                arguments: ["before": try XCTUnwrap(eventsAtClient)], in: nil, contentWorld: .page) as? Bool == true
        }
        XCTAssertTrue(reopenedEntries)
        let reopened = try await view.callAsyncJavaScript("const hello = JSON.parse(await window.happyranchConnection.call('bridge.hello', '{\"minVersion\":1,\"maxVersion\":1}')); const status = JSON.parse(await window.happyranchConnection.call('client.status', '{}')); return JSON.stringify({hello, status, sameDocument: originalContext === window.__hrAppContext, dialogs: dialogs.length, orderedEvents: clientEvents.every((e, i, all) => Number.isSafeInteger(e.sequence) && e.sequence > 0 && (i === 0 || e.sequence > all[i - 1].sequence))});",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let last = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(reopened).utf8)) as? [String: Any])
        let homeHello = try XCTUnwrap(last["hello"] as? [String: Any])
        XCTAssertEqual((homeHello["capabilities"] as? [[String: String]])?.map { $0["command"] }, ["client.status"])
        XCTAssertNotNil((last["status"] as? [String: Any])?["stage"])
        XCTAssertEqual(last["sameDocument"] as? Bool, true)
        XCTAssertEqual(last["dialogs"] as? Int, 3)
        XCTAssertEqual(last["orderedEvents"] as? Bool, true, "Adapter renewal cannot reset Main's event sequence")
        XCTAssertEqual(loads, 1); XCTAssertEqual(scheme.generation, initial)
        XCTAssertFalse(scheme.allowsNavigation, "Role renewal preserves protected Main owners")
        XCTAssertEqual(app.homeInvite.phase, .idle)
        XCTAssertNil(app.clientBridge)
    }

    @MainActor
    func testQueuedOldRoleMainActionIsRefusedIncludingAfterRetirementAndRoleRoundTrip() async throws {
        let preference = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer { UserDefaults.standard.set(preference, forKey: "connectionRolePreference") }
        for roundTrip in [false, true] {
            let app = AppDelegate(); app.connectionRolePreference = .home
            let host = WKWebView(frame: .zero)
            let commands = ConnectionWindow(app: app, mainHost: host, currentDocument: { true }, event: { _ in })
            defer { commands.invalidate(showNotice: false) }
            let lifetime = app.homeInvite.lifetime
            let error: String? = await withCheckedContinuation { continuation in
                // mainCall admits synchronously but dispatches in a MainActor
                // task. Switch roles before that queued action can execute.
                commands.mainCall(command: "client.cancelAttempt", data: "{}") { _, error in
                    continuation.resume(returning: error)
                }
                app.connectionRolePreference = .client
                if roundTrip {
                    XCTAssertTrue(commands.invalidateIfRoleChanged(app.connectionRole, showNotice: false))
                    app.connectionRolePreference = .home
                }
            }
            let refusal = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(error).utf8)) as? [String: Any])
            XCTAssertEqual(refusal["code"] as? String, "documentChanged")
            XCTAssertEqual(app.homeInvite.lifetime, lifetime)
            XCTAssertEqual(app.homeInvite.phase, .idle)
            XCTAssertNil(app.clientBridge)
        }
    }

}
