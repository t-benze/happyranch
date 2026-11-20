// Throwaway macOS 14+ probe. No real daemon, connector, credential or network.
import AppKit
import WebKit
import CryptoKit

@MainActor
final class Probe: NSObject, NSApplicationDelegate, WKURLSchemeHandler,
    WKScriptMessageHandler, WKNavigationDelegate, WKDownloadDelegate {
    let root: URL
    let downloads: URL
    let mode: String
    var window: NSWindow!
    var view: WKWebView!
    var active = Set<ObjectIdentifier>()
    var runningDownloads = [WKDownload]()

    init(root: URL, downloads: URL, mode: String) {
        self.root = root; self.downloads = downloads; self.mode = mode
    }

    func log(_ name: String, _ detail: Any) {
        let value: [String: Any] = ["name": name, "wallMs": Date().timeIntervalSince1970 * 1000, "detail": detail]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) { print(text); fflush(stdout) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let config = WKWebViewConfiguration()
        // Dedicated persistent profile. Keep the UUID identical across invocations.
        config.websiteDataStore = WKWebsiteDataStore(forIdentifier: UUID(uuidString: "18075000-0000-4000-8000-000000000001")!)
        config.setURLSchemeHandler(self, forURLScheme: "happyranch-app")
        config.userContentController.add(self, name: "probe")
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700), configuration: config)
        view.navigationDelegate = self
        window = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.contentView = view; window.makeKeyAndOrderFront(nil)
        let path = mode == "probe" ? "/probe/page" : (["other", "probe-no-multipart"].contains(mode) ? "/probe/page?omitMultipart=1" : mode)
        let host = mode == "other" ? "other" : "app"
        log("venue", ["os": ProcessInfo.processInfo.operatingSystemVersionString, "mode": mode,
                       "webkit": Bundle(for: WKWebView.self).infoDictionary?["CFBundleVersion"] as? String ?? "unknown"])
        view.load(URLRequest(url: URL(string: "happyranch-app://\(host)\(path)")!))
        DispatchQueue.main.asyncAfter(deadline: .now() + 40) { self.log("timeout-exit", true); NSApp.terminate(nil) }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        log("js", message.body)
        if let text = message.body as? String, text.contains("\"name\":\"done\"") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { NSApp.terminate(nil) }
        }
    }

    func send(_ task: WKURLSchemeTask, data: Data, type: String, attachment: Bool = false, status: Int = 200) {
        var headers = ["Content-Type": type]
        if attachment { headers["Content-Disposition"] = "attachment; filename=\"s0.txt\"" }
        let response = HTTPURLResponse(url: task.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        task.didReceive(response); task.didReceive(data); task.didFinish()
        active.remove(ObjectIdentifier(task))
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task), path = task.request.url!.path
        active.insert(id)
        log("request", ["method": task.request.httpMethod ?? "GET", "path": path])
        do {
            if path == "/probe/body" {
                var data = task.request.httpBody ?? Data()
                let fromStream = task.request.httpBodyStream != nil
                if data.isEmpty, let stream = task.request.httpBodyStream {
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
                let type = task.request.value(forHTTPHeaderField: "Content-Type") ?? ""
                let multipart = type.hasPrefix("multipart/")
                var value: [String: Any] = ["length": data.count, "fromStream": fromStream,
                    "sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()]
                if multipart {
                    // Verify the entire synthetic file, not only a Content-Length.
                    let marker = Data("\r\n\r\n".utf8), suffix = Data("\r\n--".utf8)
                    if let head = data.range(of: marker), let tail = data.range(of: suffix, options: .backwards), tail.lowerBound >= head.upperBound {
                        let file = data.subdata(in: head.upperBound..<tail.lowerBound)
                        value["fileLengths"] = [file.count]
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
                        if i == count { task.didFinish(); self.active.remove(id); return }
                        let value: [String: Any] = ["i": i, "nativeMs": Date().timeIntervalSince(began) * 1000,
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
                send(task, data: try Data(contentsOf: root.appendingPathComponent("spikes/settings-s0/probe.js")), type: "text/javascript")
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
                send(task, data: try Data(contentsOf: file), type: mime)
            }
        } catch { active.remove(id); task.didFailWithError(error); log("handler-error", String(describing: type(of: error))) }
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        active.remove(ObjectIdentifier(task)); log("stop", task.request.url!.path)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        log("load-finished", webView.url?.absoluteString ?? "none")
        if mode == "/" || mode == "/settings" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 7) {
                webView.evaluateJavaScript("JSON.stringify({path:location.pathname,origin:location.origin,root:document.getElementById('root').childElementCount,text:document.body.innerText.slice(0,100)})") { value, error in
                    self.log("spa-dom", value ?? error.map { String(describing: type(of: $0)) } ?? "none")
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.contains("attachment") ? .download : .allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { attach(download) }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { attach(download) }
    func attach(_ download: WKDownload) { runningDownloads.append(download); download.delegate = self; log("download-started", true) }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let file = downloads.appendingPathComponent(UUID().uuidString + "-" + URL(fileURLWithPath: suggestedFilename).lastPathComponent)
        completionHandler(file)
    }
    func downloadDidFinish(_ download: WKDownload) { log("download-finished", true) }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { log("download-failed", String(describing: type(of: error))) }
}

// No production app dependencies. Run only after explicit Mac allowance.
let arguments = CommandLine.arguments
guard arguments.count == 4 else { fatalError("usage: SettingsSchemeProbe REPO_ROOT DOWNLOAD_DIR probe|probe-no-multipart|other|/|/settings") }
let delegate = Probe(root: URL(fileURLWithPath: arguments[1]), downloads: URL(fileURLWithPath: arguments[2]), mode: arguments[3])
try FileManager.default.createDirectory(at: delegate.downloads, withIntermediateDirectories: true)
NSApplication.shared.setActivationPolicy(.regular)
NSApplication.shared.delegate = delegate
NSApplication.shared.run()
