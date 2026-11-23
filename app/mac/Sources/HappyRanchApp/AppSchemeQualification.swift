import AppKit
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

/// Explicit MAC-1 qualification entry, before AppDelegate/SwiftUI startup. It
/// uses the shipping loader, fetch adapter, bundle and selected-local transport
/// against a private synthetic server. It never reads real credentials.
@MainActor
final class AppSchemeQualification: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKDownloadDelegate {
    private let log: AppSchemeEventLog
    private let scheme: AppScheme
    private let downloads: URL
    private var window: NSWindow?
    private var view: WKWebView?
    private var navigation: WKNavigation?
    private var activeDownloads: [WKDownload] = []
    private var pageFinished = false
    private var downloadFinished = false
    private var reloadServedPath = "not-served"
    private var reloadNavigationPath = "not-observed"
    private var reloadNavigationDecision = "not-observed"
    private var awaitingReload = false
    private static let reloadPath = "/onboarding"

    private static func closedPath(_ path: String?) -> String {
        guard let path else { return "missing" }
        return ["/", "/onboarding", "/settings"].contains(path) ? path : "other"
    }

    init(root: URL, policy: URL, port: UInt16, events: String, downloads: URL) throws {
        log = try AppSchemeEventLog(events); self.downloads = downloads
        scheme = try AppScheme(root: root, transport: LocalAppAPITransport(port: port, credential: AppSchemeSyntheticCredential()),
            remote: false, policyURL: policy)
        super.init()
        scheme.observe = { [weak self] event, value in
            guard let self else { return }
            if event == "native_document_served" {
                if self.awaitingReload { self.reloadServedPath = value }
            } else { self.log.write(event, ["requestID": value]) }
        }
    }
    func start() {
        log.write("startup")
        let log = log
        DispatchQueue.global().asyncAfter(deadline: .now() + 35) { log.write("watchdog"); _exit(124) }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        scheme.configure(configuration)
        configuration.userContentController.add(self, name: "macQualification")
        configuration.userContentController.addUserScript(WKUserScript(source: Self.page, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700), configuration: configuration)
        self.view = view; view.navigationDelegate = self
        DispatchQueue.main.asyncAfter(deadline: .now() + 34) { [weak self, weak view] in
            guard let self, self.awaitingReload else { return }
            self.logReloadFailure(finalPath: view?.url?.path)
            self.log.write("failure", ["reason": "deep reload timeout"]); self.scheme.close(); exit(124)
        }
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = view; self.window = window
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
    }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === view, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "happyranch-app", message.frameInfo.securityOrigin.host == "app",
              let item = message.body as? [String: Any], let event = item["event"] as? String,
              ["root", "deep_reload", "receipt", "abort", "stream_end", "json", "upload", "blob", "done", "failure"].contains(event) else { return }
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
            scheme.close(); exit(1)
        }
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
            do { try await scheme.admitDocument() }
            catch {
                guard self.navigation === navigation else { return }
                if awaitingReload { logReloadFailure(finalPath: webView.url?.path) }
                log.write("admission_failure"); scheme.close(); exit(1)
            }
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView)
    }
    private func navigationFailed(_ webView: WKWebView) {
        if awaitingReload { logReloadFailure(finalPath: webView.url?.path) }
        log.write("failure", ["reason": "navigation"]); scheme.close(); exit(1)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        activeDownloads.append(download); download.delegate = self
    }
    private func finishIfReady() {
        if pageFinished && downloadFinished { scheme.close(); log.write("exit"); exit(0) }
    }
    func downloadDidFinish(_ download: WKDownload) {
        downloadFinished = true; log.write("download_finish"); finishIfReady()
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        log.write("download_failure"); scheme.close(); exit(1)
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping @MainActor @Sendable (URL?) -> Void) {
        completionHandler(downloads.appendingPathComponent("mac1.txt"))
    }
    private static let page = #"""
(async () => {
  if (window !== top || location.origin !== 'happyranch-app://app') return;
  const emit = (event, fields = {}) => window.webkit.messageHandlers.macQualification.postMessage({event, ...fields});
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  try {
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
    for (const method of ['POST','PUT']) {
      const response = await fetch('/api/v1/mac-qualification/echo', {method, headers:{...headers, 'Content-Type':'application/json'}, body:'{"synthetic":"mac1"}'});
      const result = await response.json(); emit('json', {method, ...result});
      if (!response.ok || result.bytes !== 20) throw new Error('JSON body');
    }
    // This is the shared multipart.ts adaptation: the platform encoder retains
    // its boundary/content type, then only Uint8Array bytes cross WebKit.
    const form = new FormData(); form.append('file', new Blob([new Uint8Array(10485760).fill(115)]), 'synthetic.bin');
    const encoded = new Response(form);
    const upload = await fetch('/api/v1/mac-qualification/upload', {method:'POST', headers:{...headers, 'Content-Type':encoded.headers.get('Content-Type')}, body:new Uint8Array(await encoded.arrayBuffer())});
    const uploaded = await upload.json(); emit('upload', uploaded);
    if (!upload.ok || uploaded.fileBytes !== 10485760 || !uploaded.intact) throw new Error('upload');
    async function stream(mode) {
      const controller = new AbortController();
      const response = await fetch('/api/v1/mac-qualification/' + mode, {headers, signal:controller.signal});
      if (!response.ok) throw new Error('stream status');
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let pending = '', count = 0;
      try {
        while (true) {
          const part = await reader.read(); if (part.done) break;
          pending += decoder.decode(part.value, {stream:true});
          let end;
          while ((end = pending.indexOf('\n\n')) !== -1) {
            const frame = pending.slice(0, end); pending = pending.slice(end + 2);
            if (!frame.startsWith('data: ')) continue;
            const value = JSON.parse(frame.slice(6));
            // Emit NOW, before awaiting another chunk or stream completion.
            const receiptWallMs = Date.now();
            emit('receipt', {mode, ...value, receiptWallMs}); count++;
            if (mode === 'abort' && count === 2) {
              emit('abort', {pageWallMs:Date.now()}); controller.abort(); return;
            }
          }
        }
      } catch (error) { if (!controller.signal.aborted) throw error; }
      emit('stream_end', {mode, count});
    }
    await stream('stream'); await stream('abort');
    const response = await fetch('/api/v1/mac-qualification/download', {headers});
    const blob = await response.blob(), text = await blob.text();
    emit('blob', {text}); if (text !== 'mac1 download\n') throw new Error('download');
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'mac1.txt';
    document.body.append(link); link.click(); await pause(1200); link.remove(); URL.revokeObjectURL(link.href);
    emit('done');
  } catch (error) { emit('failure', {reason:String(error)}); }
})();
"""#

    static func run(_ arguments: [String]) -> Never {
        guard arguments.count == 6, let port = UInt16(arguments[3]), port > 0 else { exit(2) }
        do {
            let probe = try AppSchemeQualification(root: URL(fileURLWithPath: arguments[1]), policy: URL(fileURLWithPath: arguments[2]),
                port: port, events: arguments[4], downloads: URL(fileURLWithPath: arguments[5]))
            let application = NSApplication.shared
            application.setActivationPolicy(.prohibited)
            probe.start()
            withExtendedLifetime(probe) { application.run() }
            exit(1)
        } catch { exit(2) }
    }
}
