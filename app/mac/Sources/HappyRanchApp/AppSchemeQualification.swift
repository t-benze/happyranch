import AppKit
import SwiftUI
import CoreFoundation
import WebKit
import HappyRanchSupervisor
import Darwin

private final class AppSchemeSyntheticCredential: DaemonCredentialProvider, @unchecked Sendable {
    func credential() throws -> String { "synthetic-mac1" }
}
private final class AppSchemeEventLog: @unchecked Sendable {
    private let lock = NSLock()
    private let descriptor: Int32
    init(_ path: String) throws {
        descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        if descriptor < 0 { throw AppAPIError.unavailable }
    }
    func write(_ event: String, _ data: [String: Any] = [:]) {
        lock.withLock {
            var row = data; row["event"] = event; row["nativeWallMs"] = Date().timeIntervalSince1970 * 1000
            guard let bytes = try? JSONSerialization.data(withJSONObject: row), bytes.count <= 8192 else { return }
            var line = bytes; line.append(10)
            line.withUnsafeBytes { buffer in
                var offset = 0
                while offset < buffer.count {
                    let count = Darwin.write(descriptor, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                    if count <= 0 { _exit(3) }; offset += count
                }
            }
            fsync(descriptor)
        }
    }
}

/// Project even forged page values to a closed, bounded evidence schema.
enum AppSchemePageLiveness {
    static func fields(_ raw: Any?) -> [String: Any] {
        let source = raw as? [String: Any] ?? [:]
        var facts: [String: Any] = ["result": raw == nil || raw is NSNull ? "missing" : "returned"]
        for (key, allowed) in [
            ("step", ["startup", "navigation", "json", "upload", "stream", "abort", "download", "done"]),
            ("readerState", ["idle", "fetch-pending", "status", "read-pending", "read-done", "decode", "parse",
                "receipt-emit", "abort-emit", "abort-sent", "end-emit", "complete", "blob-pending", "blob-emit", "save-pending"])
        ] {
            facts[key] = (source[key] as? String).flatMap { allowed.contains($0) ? $0 : nil } ?? "invalid"
        }
        for key in ["bytesReceived", "framesReceived", "pendingBytes", "pendingTimers"] {
            if let value = source[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
               value.doubleValue >= 0, value.doubleValue <= 67108864, value.doubleValue.rounded() == value.doubleValue {
                facts[key] = value.intValue
            } else { facts[key] = "invalid" }
        }
        return facts
    }
}

/// A background timer records requests and missing dispatch/replies even when
/// MainActor or the page is stalled. No page->native message is needed.
private final class AppSchemeLivenessProbe: @unchecked Sendable {
    private let log: AppSchemeEventLog
    private let lock = NSLock()
    private var sequence = 0
    private var pending: [Int: String] = [:]
    private var timer: DispatchSourceTimer?
    init(_ log: AppSchemeEventLog) { self.log = log }
    func start(_ probe: @escaping @MainActor @Sendable (Int) -> Void) {
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global())
        self.timer = timer
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            let id: Int? = self.lock.withLock {
                guard self.sequence < 35 else { return nil }
                self.sequence += 1; self.pending[self.sequence] = "main-dispatch-pending"; return self.sequence
            }
            guard let id else { return }
            self.log.write("page_probe_requested", ["probe": id])
            DispatchQueue.main.async { probe(id) }
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.75) { [weak self] in
                guard let self else { return }
                if let state = self.lock.withLock({ self.pending.removeValue(forKey: id) }) {
                    self.log.write("page_probe_pending", ["probe": id, "state": state])
                }
            }
        }
        timer.resume()
    }
    func dispatched(_ id: Int) {
        lock.withLock { if pending[id] != nil { pending[id] = "javascript-reply-pending" } }
        log.write("page_probe_dispatched", ["probe": id])
    }
    func completed(_ id: Int, _ facts: [String: Any]) {
        _ = lock.withLock { pending.removeValue(forKey: id) }
        log.write("page_probe_result", facts.merging(["probe": id]) { current, _ in current })
    }
}

/// Explicit MAC-1 qualification entry, before AppDelegate/SwiftUI startup. It
/// uses the shipping loader, fetch adapter, bundle and selected-local transport
/// against a private synthetic server. It never reads real credentials.
@MainActor
final class AppSchemeQualification: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKDownloadDelegate {
    private let log: AppSchemeEventLog
    private let scheme: AppScheme
    private let downloads: URL
    private let mode: String
    private var liveness: AppSchemeLivenessProbe?
    private var abortStopped = false
    private var streamOwnerCount = 0
    private var abortOwner: String?
    private var window: NSWindow?
    private var view: WKWebView?
    private var navigation: WKNavigation?
    private var activeDownloads: [WKDownload] = []
    private var pageFinished = false
    private var downloadFinished = false
    private var qualificationFailed = false
    private var pageStreamFacts: [String: [String: Any]] = [:]
    private var reloadServedPath = "not-served"
    private var reloadNavigationPath = "not-observed"
    private var reloadNavigationDecision = "not-observed"
    private var awaitingReload = false
    private var dialogApp: AppDelegate?
    private var capturingDialogs = false
    private static let reloadPath = "/onboarding"

    private static func closedPath(_ path: String?) -> String {
        guard let path else { return "missing" }
        return ["/", "/onboarding", "/settings"].contains(path) ? path : "other"
    }

    init(root: URL, policy: URL, port: UInt16, events: String, downloads: URL, mode: String) throws {
        log = try AppSchemeEventLog(events); self.downloads = downloads; self.mode = mode
        scheme = try AppScheme(root: root, transport: LocalAppAPITransport(port: port, credential: AppSchemeSyntheticCredential()),
            remote: false, policyURL: policy)
        super.init()
        if mode.hasPrefix("dialogs-") {
            let app = AppDelegate(); dialogApp = app; app.bindMainScheme(scheme)
            scheme.candidateSnapshot = nil // Synthetic capture input, no live candidate/session.
            scheme.updateCandidate(["kind": "remote", "id": "candidate", "name": "Studio Host — Long computer name / 工作室主机",
                "phase": "ready_to_switch", "deadline": NSNull()], accessUnsaved: true)
        }
        scheme.observe = { [weak self] event, value in
            guard let self else { return }
            if event == "native_document_served" {
                if self.awaitingReload { self.reloadServedPath = value }
            } else {
                self.log.write(event, ["requestID": value])
                if event == "native_stream_open" {
                    self.streamOwnerCount += 1
                    if self.mode == "abort" || (self.mode == "all" && self.streamOwnerCount == 2) { self.abortOwner = value }
                }
                if event == "native_stop", value == self.abortOwner {
                    self.abortStopped = true
                    DispatchQueue.main.async { [weak self] in self?.finishIfReady() }
                }
            }
        }
        let log = log
        scheme.observeLifecycle = { log.write("scheme_lifecycle", $0) }
    }
    func start() {
        log.write("startup", ["mode": mode, "pid": getpid()])
        let log = log
        DispatchQueue.global().asyncAfter(deadline: .now() + 35) { log.write("watchdog"); _exit(124) }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        scheme.configure(configuration)
        configuration.userContentController.add(self, name: "macQualification")
        let script = "window.__hrQualificationMode = '\(mode)';\n" + Self.page
        if dialogApp == nil {
            configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700), configuration: configuration)
        self.view = view; view.navigationDelegate = self
        let liveness = AppSchemeLivenessProbe(log); self.liveness = liveness
        liveness.start { [weak self, weak view, weak liveness] id in
            guard let self, let view, let liveness else { return }
            liveness.dispatched(id)
            view.evaluateJavaScript("window.__hrQualificationFacts || null") { raw, error in
                guard self.view === view else { liveness.completed(id, ["result": "view-changed"]); return }
                liveness.completed(id, error == nil ? AppSchemePageLiveness.fields(raw) : ["result": "threw"])
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 34) { [weak self, weak view] in
            guard let self else { return }
            if self.awaitingReload { self.logReloadFailure(finalPath: view?.url?.path) }
            self.logStreamFailure()
            self.log.write("failure", ["reason": "qualification timeout"]); self.scheme.close(); exit(124)
        }
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = view; self.window = window
        if let dialogApp {
            dialogApp.mainToolbar.attach(window)
            view.autoresizingMask = [.width, .height]
            window.setFrame(NSRect(x: 100, y: 100, width: 760, height: 560), display: true)
            window.makeKeyAndOrderFront(nil)
        }
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
    }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === view, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "happyranch-app", message.frameInfo.securityOrigin.host == "app",
              let item = message.body as? [String: Any], let event = item["event"] as? String,
              ["root", "deep_reload", "receipt", "abort", "stream_end", "stream_state", "step_diagnostic", "step", "json", "upload", "blob", "done", "failure"].contains(event) else { return }
        if event == "stream_state" || event == "step_diagnostic" {
            guard let step = item["step"] as? String, ["stream", "abort", "download"].contains(step) else { return }
            let allowed = ["fetch-pending", "status", "read-pending", "read-done", "decode", "parse", "receipt-emit",
                           "abort-emit", "abort-sent", "end-emit", "complete", "blob-pending", "blob-emit", "save-pending"]
            var facts: [String: Any] = ["step": step]
            facts["lastPageStep"] = (item["lastPageStep"] as? String).flatMap { allowed.contains($0) ? $0 : nil } ?? "invalid"
            for key in ["bytesReceived", "framesReceived", "pendingBytes"] {
                if let number = item[key] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                   number.doubleValue >= 0, number.doubleValue <= 67108864, number.doubleValue.rounded() == number.doubleValue {
                    facts[key] = number.intValue
                }
            }
            pageStreamFacts[step] = facts
            if event == "step_diagnostic" { qualificationFailed = true; logStreamFailure() }
            return
        }
        if event == "step" {
            guard let step = item["step"] as? String, ["stream", "abort", "download"].contains(step),
                  let status = item["status"] as? String, ["PASS", "FAIL"].contains(status) else { return }
            if status == "FAIL" { qualificationFailed = true }
            log.write("step", ["step": step, "status": status]); return
        }
        log.write(event, item)
        if event == "root" {
            awaitingReload = true
            reloadServedPath = "not-served"
            reloadNavigationPath = "not-observed"; reloadNavigationDecision = "not-observed"
        }
        if event == "deep_reload" {
            // Final pathname alone could hide a reload of `/` followed by the
            // empty-org redirect. Require the actual deep document request too.
            guard item["path"] as? String == Self.reloadPath,
                  item["origin"] as? String == AppSchemeRoute.origin,
                  (item["root"] as? Int ?? 0) > 0,
                  reloadServedPath == Self.reloadPath,
                  reloadNavigationPath == Self.reloadPath, reloadNavigationDecision == "allow" else {
                logReloadFailure(finalPath: item["path"] as? String)
                log.write("failure", ["reason": "deep reload"]); scheme.close(); exit(1)
            }
            awaitingReload = false
        }
        if event == "done" { pageFinished = true; finishIfReady() }
        if event == "failure" {
            if awaitingReload { logReloadFailure(finalPath: view?.url?.path) }
            logStreamFailure()
            scheme.close(); exit(1)
        }
    }
    private func logStreamFailure() {
        for facts in pageStreamFacts.values { log.write("page_stream_failure", facts) }
        for diagnostic in scheme.streamDiagnostics { log.write("native_stream_failure", diagnostic.fields) }
    }
    private func logReloadFailure(finalPath: String?) {
        log.write("deep_reload_failure", ["requestedPath": Self.reloadPath, "servedPath": reloadServedPath,
            "navigationPath": reloadNavigationPath, "navigationDecision": reloadNavigationDecision,
            "finalPath": Self.closedPath(finalPath)])
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        if action.request.url?.scheme == "blob", action.shouldPerformDownload { decisionHandler(.download); return }
        let allowed = action.request.url.flatMap { try? AppSchemeRoute($0) } != nil
        if awaitingReload, action.targetFrame?.isMainFrame == true {
            reloadNavigationPath = Self.closedPath(action.request.url?.path)
            reloadNavigationDecision = allowed ? "allow" : "cancel"
        }
        decisionHandler(allowed ? .allow : .cancel)
        if awaitingReload, !allowed {
            logReloadFailure(finalPath: webView.url?.path)
            log.write("failure", ["reason": "deep reload navigation"]); scheme.close(); exit(1)
        }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        self.navigation = navigation; scheme.documentDidStart()
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let navigation, self.navigation === navigation else { return }
        Task {
            do {
                try await scheme.admitDocument()
                if dialogApp != nil, !capturingDialogs {
                    capturingDialogs = true
                    try await captureDialogs(webView)
                    scheme.close(); log.write("exit"); exit(0)
                }
            }
            catch {
                guard self.navigation === navigation else { return }
                if awaitingReload { logReloadFailure(finalPath: webView.url?.path) }
                log.write("admission_failure"); scheme.close(); exit(1)
            }
        }
    }

    /// Capture the actual shared dialogs and native toolbar without screen
    /// recording permission. WebKit supplies its composited page snapshot;
    /// AppKit draws the enclosing native frame at the same logical size.
    private func captureDialogs(_ view: WKWebView) async throws {
        guard let window, let frame = window.contentView?.superview, let app = dialogApp else { throw AppAPIError.unavailable }
        let locale = mode == "dialogs-zh" ? "zh" : "en"
        for theme in ["light", "dark"] {
            window.appearance = NSAppearance(named: theme == "dark" ? .darkAqua : .aqua)
            for scale in [100, 200] {
                _ = try await view.callAsyncJavaScript("document.documentElement.style.fontSize = size; localStorage.setItem('theme', theme); document.documentElement.classList.toggle('dark', theme === 'dark');",
                    arguments: ["size": "\(scale)%", "theme": theme], in: nil, contentWorld: .page)
                for dialog in ["settings", "connection", "main"] {
                    if dialog == "main" {
                        _ = try await view.callAsyncJavaScript("document.querySelector('[data-desktop-dialog] > button[aria-label]')?.click();",
                            arguments: [:], in: nil, contentWorld: .page)
                    } else {
                        let deadline = Date().addingTimeInterval(4)
                        while !app.mainToolbar.open(dialog) {
                            guard Date() < deadline else { throw AppAPIError.unavailable }
                            try await Task.sleep(for: .milliseconds(25))
                        }
                    }
                    var ready = false
                    for _ in 0..<80 {
                        let value = try await view.callAsyncJavaScript("const pane = document.querySelector('[data-desktop-dialog]'); return expected === 'main' ? !pane : pane?.getAttribute('data-desktop-dialog') === expected && pane.contains(document.activeElement);",
                            arguments: ["expected": dialog], in: nil, contentWorld: .page)
                        if value as? Bool == true { ready = true; break }
                        try await Task.sleep(for: .milliseconds(25))
                    }
                    guard ready else { throw AppAPIError.unavailable }
                    let localeReady = try await view.callAsyncJavaScript("return document.documentElement.lang.toLowerCase().startsWith(expected);",
                        arguments: ["expected": locale], in: nil, contentWorld: .page)
                    guard localeReady as? Bool == true else { throw AppAPIError.unavailable }
                    try await Task.sleep(for: .milliseconds(200)) // settle dialog/theme animation
                    let page = try await view.takeSnapshot(configuration: nil)
                    frame.layoutSubtreeIfNeeded()
                    guard let native = frame.bitmapImageRepForCachingDisplay(in: frame.bounds),
                          let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 760, pixelsHigh: 560,
                            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
                          let graphics = NSGraphicsContext(bitmapImageRep: bitmap) else { throw AppAPIError.unavailable }
                    frame.cacheDisplay(in: frame.bounds, to: native)
                    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = graphics
                    native.draw(in: NSRect(x: 0, y: 0, width: 760, height: 560))
                    page.draw(in: view.convert(view.bounds, to: frame))
                    NSGraphicsContext.restoreGraphicsState()
                    guard let png = bitmap.representation(using: .png, properties: [:]), png.count <= 2 * 1024 * 1024 else { throw AppAPIError.unavailable }
                    let name = "\(locale)-\(theme)-\(scale)-\(dialog).png"
                    try png.write(to: downloads.appendingPathComponent(name), options: .withoutOverwriting)
                    log.write("screenshot", ["name": name, "width": 760, "height": 560, "textPercent": scale,
                        "locale": locale, "theme": theme, "dialog": dialog])
                }
            }
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, stage: "provisional")
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, stage: "committed")
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        log.write("content_process_terminated", ["mode": mode])
        qualificationFailed = true; logStreamFailure(); scheme.close(); exit(1)
    }
    private func navigationFailed(_ webView: WKWebView, stage: String) {
        log.write("navigation_failure", ["stage": stage, "mode": mode])
        if awaitingReload { logReloadFailure(finalPath: webView.url?.path) }
        log.write("failure", ["reason": "navigation"]); scheme.close(); exit(1)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        activeDownloads.append(download); download.delegate = self
    }
    private func finishIfReady() {
        guard pageFinished else { return }
        if !qualificationFailed && (mode == "abort" || mode == "all") && !abortStopped { return }
        let passed = !qualificationFailed && (!(mode == "download" || mode == "all") || downloadFinished)
        if !passed { logStreamFailure() }
        scheme.close(); log.write("exit"); exit(passed ? 0 : 1)
    }
    func downloadDidFinish(_ download: WKDownload) {
        downloadFinished = true; log.write("download_finish"); settlePageDownload(true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        qualificationFailed = true; log.write("download_failure"); settlePageDownload(false)
    }
    private func settlePageDownload(_ success: Bool) {
        // No return value is consumed: intentionally use the callback overload.
        view?.callAsyncJavaScript("window.__hrQualificationDownload?.(success);", arguments: ["success": success],
            in: nil, in: .page, completionHandler: nil)
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping @MainActor @Sendable (URL?) -> Void) {
        completionHandler(downloads.appendingPathComponent("mac1.txt"))
    }
    private static let page = #"""
(async () => {
  if (window !== top || location.origin !== 'happyranch-app://app') return;
  const selected = window.__hrQualificationMode || 'all';
  window.__hrQualificationFacts = {step:'startup', readerState:'idle', bytesReceived:0, framesReceived:0, pendingBytes:0, pendingTimers:0};
  const emit = (event, fields = {}) => window.webkit.messageHandlers.macQualification.postMessage({event, ...fields});
  const pause = ms => new Promise(resolve => {
    window.__hrQualificationFacts.pendingTimers++;
    setTimeout(() => { window.__hrQualificationFacts.pendingTimers--; resolve(); }, ms);
  });
  try {
    window.__hrQualificationFacts.step = 'navigation';
    if (!sessionStorage.getItem('mac1.reload')) {
      for (let i = 0; i < 100 && location.pathname !== '/onboarding'; i++) await pause(50);
      emit('root', {path:location.pathname, root:document.getElementById('root')?.childElementCount || 0});
      if (location.pathname !== '/onboarding') throw new Error('empty-org redirect');
      // DesktopSettingsRoute opens a dialog and replaces `/settings` with `/`.
      // `/onboarding` is the stable global deep route for this empty-org fixture.
      // RootRedirect already replaced history with it; reload that history URL.
      sessionStorage.setItem('mac1.reload', '1'); location.reload(); return;
    }
    for (let i = 0; i < 100 && !document.getElementById('root')?.childElementCount; i++) await pause(50);
    emit('deep_reload', {path:location.pathname, origin:location.origin, root:document.getElementById('root')?.childElementCount || 0});
    if (location.pathname !== '/onboarding' || location.origin !== 'happyranch-app://app') throw new Error('deep reload');
    const status = JSON.parse(await window.happyranchConnection.call('app.status', '{}'));
    const headers = {'X-HappyRanch-Target':status.generation};
    window.__hrQualificationFacts.step = 'json';
    for (const method of ['POST','PUT']) {
      const response = await fetch('/api/v1/mac-qualification/echo', {method, headers:{...headers, 'Content-Type':'application/json'}, body:'{"synthetic":"mac1"}'});
      const result = await response.json(); emit('json', {method, ...result});
      if (!response.ok || result.bytes !== 20) throw new Error('JSON body');
    }
    // This is the shared multipart.ts adaptation: the platform encoder retains
    // its boundary/content type, then only Uint8Array bytes cross WebKit.
    window.__hrQualificationFacts.step = 'upload';
    const form = new FormData(); form.append('file', new Blob([new Uint8Array(10485760).fill(115)]), 'synthetic.bin');
    const encoded = new Response(form);
    const upload = await fetch('/api/v1/mac-qualification/upload', {method:'POST', headers:{...headers, 'Content-Type':encoded.headers.get('Content-Type')}, body:new Uint8Array(await encoded.arrayBuffer())});
    const uploaded = await upload.json(); emit('upload', uploaded);
    if (!upload.ok || uploaded.fileBytes !== 10485760 || !uploaded.intact) throw new Error('upload');
    async function step(name, milliseconds, action) {
      const controller = new AbortController();
      const facts = {step:name, bytesReceived:0, framesReceived:0, pendingBytes:0, lastPageStep:'fetch-pending'};
      window.__hrQualificationFacts = facts; facts.readerState = 'fetch-pending'; facts.pendingTimers = 1;
      let timer, active = true;
      const mark = lastPageStep => { if (active) { facts.lastPageStep = lastPageStep; facts.readerState = lastPageStep; emit('stream_state', facts); } };
      try {
        await Promise.race([action(controller, facts, mark, () => active), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('step timeout')), milliseconds);
        })]);
        emit('step', {step:name, status:'PASS'});
      } catch {
        // No error text: it may include body, URL or native context values.
        emit('step_diagnostic', facts);
        emit('step', {step:name, status:'FAIL'});
      } finally { active = false; clearTimeout(timer); facts.pendingTimers = 0; controller.abort(); }
    }
    async function stream(mode, controller, facts, mark, active) {
      mark('fetch-pending');
      const response = await fetch('/api/v1/mac-qualification/' + mode, {headers, signal:controller.signal});
      mark('status'); if (!response.ok) throw new Error('stream status');
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let pending = '', count = 0;
      try {
        while (true) {
          mark('read-pending');
          const part = await reader.read();
          if (!active()) return;
          if (part.done) { mark('read-done'); break; }
          facts.bytesReceived += part.value.byteLength;
          mark('decode');
          pending += decoder.decode(part.value, {stream:true});
          facts.pendingBytes = new TextEncoder().encode(pending).byteLength;
          let end;
          while ((end = pending.indexOf('\n\n')) !== -1) {
            const frame = pending.slice(0, end); pending = pending.slice(end + 2);
            facts.framesReceived++; facts.pendingBytes = new TextEncoder().encode(pending).byteLength;
            if (!frame.startsWith('data: ')) continue;
            mark('parse');
            const value = JSON.parse(frame.slice(6));
            // Emit NOW, before awaiting another chunk or stream completion.
            const receiptWallMs = Date.now();
            mark('receipt-emit');
            emit('receipt', {mode, ...value, receiptWallMs}); count++;
            if (mode === 'abort' && count === 2) {
              mark('abort-emit'); emit('abort', {pageWallMs:Date.now()});
              controller.abort(); mark('abort-sent'); return;
            }
          }
        }
      } finally {
        // A pending native read must not hold the next step, even on failure.
        reader.cancel().catch(() => {});
      }
      if (mode !== 'stream' || count !== 5 || pending.length !== 0) throw new Error('stream completion');
      mark('end-emit');
      emit('stream_end', {mode, count});
      mark('complete');
    }
    if (selected === 'all' || selected === 'stream') await step('stream', 9000, (...args) => stream('stream', ...args));
    if (selected === 'all' || selected === 'abort') await step('abort', 4000, (...args) => stream('abort', ...args));
    if (selected === 'all' || selected === 'download') await step('download', 5000, async (controller, facts, mark, active) => {
      const response = await fetch('/api/v1/mac-qualification/download', {headers, signal:controller.signal});
      mark('status'); if (!response.ok) throw new Error('download status');
      mark('blob-pending'); const blob = await response.blob(), text = await blob.text();
      if (!active()) return;
      facts.bytesReceived = blob.size;
      mark('blob-emit'); emit('blob', {text}); if (text !== 'mac1 download\n') throw new Error('download');
      const saved = new Promise(resolve => {
        window.__hrQualificationDownload = resolve;
        controller.signal.addEventListener('abort', () => resolve(false), {once:true});
      });
      const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'mac1.txt';
      try {
        mark('save-pending'); document.body.append(link); link.click();
        if (!await saved) throw new Error('download save');
        mark('complete');
      } finally { link.remove(); URL.revokeObjectURL(link.href); delete window.__hrQualificationDownload; }
    });
    window.__hrQualificationFacts.step = 'done';
    emit('done');
  } catch { emit('failure', {reason:'qualification'}); }
})();
"""#

    /// Existing product qualification venue, using the actual ContentView and
    /// AppDelegate composition with resources staged beside this scratch binary.
    private static func qualifyColdClient(events: String) -> Never {
        do {
            let log = try AppSchemeEventLog(events)
            let application = NSApplication.shared
            application.setActivationPolicy(.regular)
            let app = AppDelegate()
            app.connectionRolePreference = .client
            app.showSettings() // Unavailable entry requires a new manual Retry.
            let host = NSHostingView(rootView: ContentView().environmentObject(app))
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                                  styleMask: [.titled], backing: .buffered, defer: false)
            window.contentView = host; window.makeKeyAndOrderFront(nil)
            DispatchQueue.global().asyncAfter(deadline: .now() + 35) { log.write("watchdog"); _exit(124) }
            Task { @MainActor in
                var checkpoint = "composition"
                @MainActor func findWebView(_ parent: NSView) -> WKWebView? {
                    if let view = parent as? WKWebView { return view }
                    return parent.subviews.lazy.compactMap { findWebView($0) }.first
                }
                do {
                    let deadline = Date().addingTimeInterval(25)
                    while findWebView(host) == nil, Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
                    guard let view = findWebView(host),
                          let scheme = view.configuration.urlSchemeHandler(forURLScheme: "happyranch-app") as? AppScheme else { throw AppAPIError.unavailable }
                    defer { scheme.close() }
                    checkpoint = "document-admission"
                    while scheme.documentAdmission.readiness != "no_target", Date() < deadline {
                        try await Task.sleep(for: .milliseconds(10))
                    }
                    guard scheme.documentAdmission.bind == "true", scheme.documentAdmission.readiness == "no_target" else { throw AppAPIError.unavailable }
                    let generation = scheme.generation
                    checkpoint = "subscription"
                    _ = try await view.callAsyncJavaScript("window.coldDialogs = []; window.happyranchConnection.on('app.dialogRequested', raw => coldDialogs.push(JSON.parse(raw).dialog)); await window.happyranchConnection.call('app.status', '{}');", arguments: [:], in: nil, contentWorld: .page)
                    try await Task.sleep(for: .milliseconds(100))
                    let queued = try await view.callAsyncJavaScript("return coldDialogs.length;", arguments: [:], in: nil, contentWorld: .page) as? Int
                    guard queued == 0, let toolbar = window.toolbar else { throw AppAPIError.unavailable }
                    @MainActor func click(_ id: String) throws {
                        guard let button = toolbar.items.first(where: { $0.itemIdentifier.rawValue == id })?.view as? NSButton else { throw AppAPIError.unavailable }
                        button.performClick(nil)
                    }
                    @MainActor func requireDialog(_ expected: String) async throws {
                        while Date() < deadline {
                            let ready = try await view.callAsyncJavaScript("const pane = document.querySelector('[data-desktop-dialog]'); return pane?.getAttribute('data-desktop-dialog') === expected && pane.contains(document.activeElement);", arguments: ["expected": expected], in: nil, contentWorld: .page) as? Bool
                            if ready == true { return }
                            try await Task.sleep(for: .milliseconds(25))
                        }
                        throw AppAPIError.unavailable
                    }
                    checkpoint = "retry-settings"
                    try click("dialog-retry"); try await requireDialog("settings")
                    checkpoint = "menu-connection"
                    app.showConnection(); try await requireDialog("connection")
                    checkpoint = "menu-settings"
                    app.showSettings(); try await requireDialog("settings") // Cmd+, action.
                    checkpoint = "toolbar-connection"
                    try click("connection"); try await requireDialog("connection")
                    checkpoint = "toolbar-settings"
                    try click("settings"); try await requireDialog("settings")
                    let expected = #"["settings","connection","settings","connection","settings"]"#
                    var received: String?
                    repeat {
                        received = try await view.callAsyncJavaScript("return JSON.stringify(coldDialogs);", arguments: [:], in: nil, contentWorld: .page) as? String
                        if received != expected { try await Task.sleep(for: .milliseconds(10)) }
                    } while received != expected && Date() < deadline
                    guard received == expected else { throw AppAPIError.unavailable }
                    checkpoint = "api-no-target"
                    let raw = try await view.callAsyncJavaScript("const r = await fetch('happyranch-app://app/api/v1/auth/bootstrap'); return JSON.stringify({status:r.status,body:await r.json()});", arguments: [:], in: nil, contentWorld: .page) as? String
                    guard let raw, let response = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any],
                          response["status"] as? Int == 503, let body = response["body"] as? [String: Any],
                          (body["detail"] as? [String: String])?["code"] == "no_target",
                          scheme.generation == generation, app.webViewURL == nil, app.appAPITransport == nil,
                          app.clientBridge == nil, !app.homeInvite.hasActiveSession, app.homeInvite.candidateID == nil,
                          !app.isConnecting, app.currentHandle == nil, app.supervisor.state == .notConfigured else { throw AppAPIError.unavailable }
                    log.write("cold_client", ["status": "PASS", "entries": 5, "dialogs": true, "no_target": true, "unchanged_target": true])
                    scheme.close()
                    window.orderOut(nil); window.contentView = nil
                    exit(0)
                } catch {
                    log.write("cold_client", ["status": "FAIL", "checkpoint": checkpoint])
                    exit(1)
                }
            }
            application.run()
            exit(1)
        } catch { exit(2) }
    }

    static func run(_ arguments: [String]) -> Never {
        guard [6, 7].contains(arguments.count), let port = UInt16(arguments[3]) else { exit(2) }
        let mode = arguments.count == 7 ? arguments[6] : "all"
        guard ["all", "stream", "abort", "download", "dialogs-en", "dialogs-zh", "cold-client"].contains(mode) else { exit(2) }
        if mode == "cold-client" {
            guard port == 0 else { exit(2) } // Portless: no transport is constructed.
            qualifyColdClient(events: arguments[4])
        }
        guard port > 0 else { exit(2) }
        if mode.hasPrefix("dialogs-") {
            UserDefaults.standard.setVolatileDomain(["AppleLanguages": [mode == "dialogs-zh" ? "zh-Hans" : "en"]], forName: UserDefaults.argumentDomain)
        }
        do {
            let probe = try AppSchemeQualification(root: URL(fileURLWithPath: arguments[1]), policy: URL(fileURLWithPath: arguments[2]),
                port: port, events: arguments[4], downloads: URL(fileURLWithPath: arguments[5]), mode: mode)
            let application = NSApplication.shared
            application.setActivationPolicy(mode.hasPrefix("dialogs-") ? .regular : .prohibited)
            probe.start()
            withExtendedLifetime(probe) { application.run() }
            exit(1)
        } catch { exit(2) }
    }
}
