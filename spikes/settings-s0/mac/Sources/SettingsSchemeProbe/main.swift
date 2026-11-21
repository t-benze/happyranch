// Throwaway macOS 14+ probe. No real daemon, connector, credential or network.
import AppKit
import WebKit
import CryptoKit
import Darwin

// Independent of the main actor: a blocked body read/AppKit callback cannot starve the watchdog.
final class EventLog: @unchecked Sendable {
    private let lock = NSLock()
    private let file: FileHandle
    private var phase = "process_start"
    private var bytes = 0

    init(path: String) throws {
        guard FileManager.default.createFile(atPath: path, contents: nil) else {
            throw NSError(domain: "S0-event-file", code: 1)
        }
        file = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        setbuf(stdout, nil); setbuf(stderr, nil)
    }

    func write(_ name: String, _ detail: Any) {
        lock.lock(); defer { lock.unlock() }
        phase = name == "js" ? "js:\(String(describing: detail).prefix(256))" : name
        let value: [String: Any] = ["name": name, "wallMs": Date().timeIntervalSince1970 * 1000, "detail": detail]
        do {
            var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
            data.append(10)
            // Synthetic messages are small; refuse overflow rather than grow archived evidence.
            guard data.count <= 8192, bytes + data.count <= 256 * 1024 else { Darwin.exit(70) }
            try file.write(contentsOf: data)
            try file.synchronize()
            bytes += data.count
            FileHandle.standardOutput.write(data)
            fflush(stdout); fflush(stderr)
        } catch {
            FileHandle.standardError.write(Data("event file write/sync failed\n".utf8))
            fflush(stderr); Darwin.exit(74)
        }
    }

    func watchdog() {
        lock.lock(); let stoppedAt = phase; lock.unlock()
        write("watchdog_fired", ["phase": stoppedAt])
        Darwin.exit(124)
    }
}

@MainActor
final class Probe: NSObject, NSApplicationDelegate, WKURLSchemeHandler,
    WKScriptMessageHandler, WKNavigationDelegate, WKDownloadDelegate {
    let root: URL
    let downloads: URL
    let mode: String
    let check: String
    let events: EventLog
    var window: NSWindow!
    var view: WKWebView!
    var active = Set<ObjectIdentifier>()
    var runningDownloads = [WKDownload]()
    var deepReloaded = false
    var domScheduled = false
    var requests = [ObjectIdentifier: [String: Any]]()

    init(root: URL, downloads: URL, mode: String, check: String, events: EventLog) {
        self.root = root; self.downloads = downloads; self.mode = mode
        self.check = check; self.events = events
    }

    func log(_ name: String, _ detail: Any) {
        events.write(name, detail)
    }

    func finish() {
        log("check-finished", ["check": check, "mode": mode])
        log("orderly_exit", ["code": 0])
        // Do not depend on AppKit termination negotiation or child pipe closure.
        Darwin.exit(0)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("nsapplication-launched", true)
        let config = WKWebViewConfiguration()
        // Dedicated persistent profile. Keep the UUID identical across invocations.
        log("store-creating", true)
        config.websiteDataStore = WKWebsiteDataStore(forIdentifier: UUID(uuidString: ProcessInfo.processInfo.environment["SETTINGS_S0_STORE_UUID"] ?? "18075000-0000-4000-8000-000000000001")!)
        log("store-created", true)
        config.setURLSchemeHandler(self, forURLScheme: "happyranch-app")
        log("scheme-handler-registered", true)
        config.userContentController.add(self, name: "probe")
        log("wkwebview-creating", true)
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700), configuration: config)
        log("wkwebview-created", true)
        view.navigationDelegate = self
        window = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.contentView = view; window.makeKeyAndOrderFront(nil)
        let paths = ["probe": "/probe/page", "other": "/probe/page",
                     "probe-serialized": "/probe/page?serializeMultipart=1",
                     "probe-direct": "/probe/page?directOnly=1"]
        let path = paths[mode] ?? mode
        let host = mode == "other" ? "other" : "app"
        log("venue", ["os": ProcessInfo.processInfo.operatingSystemVersionString, "mode": mode,
                       "webkit": Bundle(for: WKWebView.self).infoDictionary?["CFBundleVersion"] as? String ?? "unknown"])
        let separator = path.contains("?") ? "&" : "?"
        let url = URL(string: "happyranch-app://\(host)\(path)\(separator)check=\(check)")!
        log("check-started", ["check": check, "mode": mode])
        log("load-requested", url.absoluteString)
        view.load(URLRequest(url: url))
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        log("js", message.body)
        if let text = message.body as? String,
           let data = text.data(using: .utf8),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           value["name"] as? String == "done" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { self.finish() }
        }
    }

    func send(_ task: WKURLSchemeTask, data: Data, type: String, attachment: Bool = false, status: Int = 200) {
        var headers = ["Content-Type": type]
        if attachment { headers["Content-Disposition"] = "attachment; filename=\"s0.txt\"" }
        let response = HTTPURLResponse(url: task.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        task.didReceive(response); task.didReceive(data); task.didFinish()
        log("handler-finished", requests[ObjectIdentifier(task)] ?? [:])
        active.remove(ObjectIdentifier(task))
        requests.removeValue(forKey: ObjectIdentifier(task))
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task), path = task.request.url!.path
        active.insert(id)
        let body = task.request.httpBody ?? (task.request.httpBodyStream == nil ? Data() : nil)
        requests[id] = ["method": task.request.httpMethod ?? "GET", "path": path,
                        "bodyBytes": body.map { $0.count as Any } ?? NSNull(),
                        "bodySHA256": body.map { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() as Any } ?? NSNull(),
                        "bodyStream": task.request.httpBodyStream != nil]
        log("handler-start", requests[id]!)
        do {
            if path == "/probe/body" {
                var data = task.request.httpBody ?? Data()
                let fromStream = task.request.httpBodyStream != nil
                let hadBody = task.request.httpBody != nil
                if data.count > 12 * 1024 * 1024 { throw NSError(domain: "S0", code: 2) }
                if data.isEmpty, let stream = task.request.httpBodyStream {
                    log("body-read-start", requests[id]!)
                    stream.open(); defer { stream.close() }
                    var buffer = [UInt8](repeating: 0, count: 65536)
                    while true {
                        let size = stream.read(&buffer, maxLength: buffer.count)
                        if size < 0 { throw stream.streamError ?? NSError(domain: "S0", code: 1) }
                        if size == 0 { break }
                        data.append(contentsOf: buffer.prefix(size))
                        if data.count > 12 * 1024 * 1024 { throw NSError(domain: "S0", code: 2) }
                    }
                }
                requests[id]?["bodyBytes"] = data.count
                requests[id]?["bodySHA256"] = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                log("handler-body", requests[id]!)
                let type = task.request.value(forHTTPHeaderField: "Content-Type") ?? ""
                let multipart = type.hasPrefix("multipart/")
                var value: [String: Any] = ["length": data.count, "fromStream": fromStream, "hadBody": hadBody,
                    "sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()]
                if multipart {
                    // Verify the entire synthetic file, not only a Content-Length.
                    let marker = Data("\r\n\r\n".utf8), suffix = Data("\r\n--".utf8)
                    if let head = data.range(of: marker), let tail = data.range(of: suffix, options: .backwards), tail.lowerBound >= head.upperBound {
                        let file = data.subdata(in: head.upperBound..<tail.lowerBound)
                        value["fileLengths"] = [file.count]
                        value["fileSHA256"] = SHA256.hash(data: file).map { String(format: "%02x", $0) }.joined()
                        value["intact"] = file == Data(repeating: 115, count: 10 * 1024 * 1024)
                    } else { value["intact"] = false }
                } else { value["text"] = String(data: data, encoding: .utf8) ?? "invalid-utf8" }
                log("body", value)
                send(task, data: try JSONSerialization.data(withJSONObject: value), type: "application/json")
            } else if path == "/probe/stream" || path == "/probe/cancel" {
                let response = HTTPURLResponse(url: task.request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
                                               headerFields: ["Content-Type": "text/event-stream"])!
                task.didReceive(response)
                let began = Date()
                let count = path == "/probe/stream" ? 5 : 10
                for i in 0...count {
                    DispatchQueue.main.asyncAfter(deadline: .now() + Double(i)) {
                        guard self.active.contains(id) else { return }
                        if i == count {
                            task.didFinish(); self.active.remove(id)
                            self.log("handler-finished", self.requests.removeValue(forKey: id) ?? [:]); return
                        }
                        let value: [String: Any] = ["path": path, "i": i, "nativeMs": Date().timeIntervalSince(began) * 1000,
                                                  "nativeWallMs": Date().timeIntervalSince1970 * 1000]
                        let json = String(data: try! JSONSerialization.data(withJSONObject: value), encoding: .utf8)!
                        task.didReceive(Data("data: \(json)\n\n".utf8)); self.log("chunk-written", value)
                    }
                }
            } else if path == "/probe/download" {
                send(task, data: Data("s0 download\n".utf8), type: "application/octet-stream", attachment: true)
            } else if path == "/probe/page" {
                send(task, data: Data("<html><body>S0<script type=\"module\" src=\"/probe/probe.js\"></script></body></html>".utf8), type: "text/html")
            } else if path == "/probe/probe.js" {
                send(task, data: try Data(contentsOf: root.appendingPathComponent("spikes/settings-s0/mac/probe.js")), type: "text/javascript")
            } else if path.hasPrefix("/probe/") && ["fetch-event-source.js", "fetch", "fetch.js", "parse", "parse.js"].contains(task.request.url!.lastPathComponent) {
                let name = task.request.url!.lastPathComponent
                let file = name == "fetch-event-source.js" ? "index.js" : (name.hasSuffix(".js") ? name : name + ".js")
                send(task, data: try Data(contentsOf: root.appendingPathComponent("web/node_modules/@microsoft/fetch-event-source/lib/esm/\(file)")), type: "text/javascript")
            } else if path.hasPrefix("/api/") {
                // Explicitly unavailable: the Mac probe never contacts a daemon.
                send(task, data: Data("{\"unavailable\":true}".utf8), type: "application/json", status: 503)
            } else {
                let base = root.appendingPathComponent("web/dist").standardizedFileURL
                var file = base.appendingPathComponent(String(path.dropFirst())).standardizedFileURL
                guard file.path == base.path || file.path.hasPrefix(base.path + "/") else { throw NSError(domain: "S0", code: 3) }
                var directory: ObjCBool = false
                if !FileManager.default.fileExists(atPath: file.path, isDirectory: &directory) || directory.boolValue {
                    file = base.appendingPathComponent("index.html")
                }
                let mime = ["html":"text/html", "js":"text/javascript", "css":"text/css", "svg":"image/svg+xml", "woff2":"font/woff2"][file.pathExtension] ?? "application/octet-stream"
                let data = try Data(contentsOf: file)
                log("served", ["path": path, "file": file.lastPathComponent, "mime": mime, "bytes": data.count])
                send(task, data: data, type: mime)
            }
        } catch {
            active.remove(id); task.didFailWithError(error)
            log("handler-error", ["request": requests.removeValue(forKey: id) ?? [:], "error": String(describing: error)])
        }
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        active.remove(id)
        log("stop", requests.removeValue(forKey: id) ?? ["path": task.request.url?.path ?? "none",
                                                        "method": task.request.httpMethod ?? "GET",
                                                        "bodyBytes": NSNull(), "bodySHA256": NSNull()])
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        log("didFinish", webView.url?.absoluteString ?? "none")
        if mode == "/settings" && !deepReloaded {
            deepReloaded = true
            log("deep-reload", webView.url?.absoluteString ?? "none")
            webView.reload()
            return
        }
        if (mode == "/" || mode == "/settings") && !domScheduled {
            domScheduled = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 7) {
                webView.evaluateJavaScript("JSON.stringify({path:location.pathname,origin:location.origin,root:document.getElementById('root').childElementCount,text:document.body.innerText.slice(0,100)})") { value, error in
                    self.log("spa-dom", value ?? error.map { String(describing: type(of: $0)) } ?? "none")
                    self.finish()
                }
            }
        }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        log("didStartProvisionalNavigation", webView.url?.absoluteString ?? "none")
    }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        log("didCommit", webView.url?.absoluteString ?? "none")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        log("didFailProvisionalNavigation", String(describing: error))
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        log("didFail", String(describing: error))
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { log("web-process-terminated", true) }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        log("navigation-response-policy", response.response.url?.absoluteString ?? "none")
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.contains("attachment") ? .download : .allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        log("navigation-action-policy", action.request.url?.absoluteString ?? "none")
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        log("navigation-response-download", true); attach(download)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        log("navigation-action-download", true); attach(download)
    }
    func attach(_ download: WKDownload) { runningDownloads.append(download); download.delegate = self; log("download-started", true) }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let file = downloads.appendingPathComponent(UUID().uuidString + "-" + URL(fileURLWithPath: suggestedFilename).lastPathComponent)
        log("download-destination", ["source": response.url?.absoluteString ?? "none", "name": file.lastPathComponent])
        completionHandler(file)
    }
    func downloadDidFinish(_ download: WKDownload) { log("download-finished", true) }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { log("download-failed", String(describing: type(of: error))) }
}

// No production app dependencies. Run only after explicit Mac allowance.
let arguments = CommandLine.arguments
guard arguments.count == 7, let watchdog = Double(arguments[6]), watchdog > 0, watchdog <= 25 else {
    fatalError("usage: SettingsSchemeProbe REPO_ROOT DOWNLOAD_DIR MODE CHECK EVENT_FILE WATCHDOG_SECONDS(0..25)")
}
let events = try EventLog(path: arguments[5])
events.write("process_start", ["pid": ProcessInfo.processInfo.processIdentifier, "mode": arguments[3], "check": arguments[4]])
DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + watchdog) { events.watchdog() }
// Keep AppKit startup and the delegate alive within the main-thread actor scope.
try MainActor.assumeIsolated {
    let delegate = Probe(root: URL(fileURLWithPath: arguments[1]), downloads: URL(fileURLWithPath: arguments[2]),
                         mode: arguments[3], check: arguments[4], events: events)
    try FileManager.default.createDirectory(at: delegate.downloads, withIntermediateDirectories: true)
    events.write("nsapplication-creating", true)
    NSApplication.shared.setActivationPolicy(.regular)
    NSApplication.shared.delegate = delegate
    events.write("nsapplication-run", true)
    NSApplication.shared.run()
    events.write("app-run-returned", true)
    delegate.finish()
}
