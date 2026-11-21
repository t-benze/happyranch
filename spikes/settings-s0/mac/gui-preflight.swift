// Read-only GUI admission before building/running the larger throwaway probe.
import AppKit
import WebKit
import CoreGraphics

func record(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text); fflush(stdout) }
}

@MainActor
final class GUIProbe: NSObject, WKNavigationDelegate {
    var view: WKWebView!
    var window: NSWindow!
    func start() {
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        record(["stage": "windowserver", "sessionDictionary": session != nil,
                "onConsole": session?["kCGSSessionOnConsoleKey"] as? Bool ?? false,
                "home": NSHomeDirectory()])
        NSApplication.shared.setActivationPolicy(.prohibited)
        record(["stage": "nsapplication", "connected": true])
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 200),
                          styleMask: [.titled], backing: .buffered, defer: false)
        view = WKWebView(frame: window.contentView!.bounds)
        view.navigationDelegate = self
        window.contentView = view
        record(["stage": "wkwebview-created", "created": true])
        view.loadHTMLString("<html><body>settings-s0-gui</body></html>", baseURL: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
            record(["stage": "result", "usable": false, "reason": "WKWebView load timed out"])
            exit(2)
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("document.body.innerText") { value, error in
            let usable = error == nil && (value as? String) == "settings-s0-gui"
            record(["stage": "result", "usable": usable])
            exit(usable ? 0 : 2)
        }
    }
}
// Synchronous script startup is on the main thread; make its isolation explicit.
MainActor.assumeIsolated {
    let probe = GUIProbe()
    probe.start()
    NSApplication.shared.run()
}
