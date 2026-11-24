import AppKit
import SwiftUI

/// Native chrome only. Main's shared web owner decides every dialog transition.
@MainActor
final class MainToolbarController: NSObject, NSToolbarDelegate {
    var request: ((String) -> Bool)?
    private let toolbar = NSToolbar(identifier: "HappyRanch.Main")
    private weak var window: NSWindow?
    private let status = NSButton(title: "", target: nil, action: nil)
    private let candidate = NSTextField(labelWithString: "")
    private var unsaved = false
    private var retryDialog: String?
    private static let summary = NSToolbarItem.Identifier("summary")
    private static let connection = NSToolbarItem.Identifier("connection")
    private static let settings = NSToolbarItem.Identifier("settings")
    private static let warning = NSToolbarItem.Identifier("access-unsaved")
    private static let retry = NSToolbarItem.Identifier("dialog-retry")

    private func copy(_ en: String, _ zh: String) -> String {
        Locale.preferredLanguages.first?.hasPrefix("zh") == true ? zh : en
    }

    override init() {
        super.init()
        toolbar.delegate = self; toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false; toolbar.autosavesConfiguration = false
        status.bezelStyle = .rounded; status.target = self; status.action = #selector(connectionAction)
        status.setAccessibilityIdentifier("main-connection-status")
        candidate.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        candidate.lineBreakMode = .byTruncatingTail
        status.title = copy("Interface unavailable", "界面暂不可用")
    }

    func attach(_ window: NSWindow) {
        if window.toolbar !== toolbar {
            self.window = window
            window.toolbar = toolbar; window.toolbarStyle = .unified
            window.titleVisibility = .hidden
        }
    }

    @discardableResult
    func open(_ dialog: String) -> Bool {
        guard ["connection", "settings"].contains(dialog) else { return false }
        // Remember only the label for a fresh, explicit Retry click. Never send
        // it on subscription/readiness/recovery or from a state refresh.
        let delivered = request?(dialog) == true
        retryDialog = delivered ? nil : dialog
        optionalItem(Self.retry, visible: retryDialog != nil)
        if let item = toolbar.items.first(where: { $0.itemIdentifier == Self.retry }) {
            item.label = retryLabel; item.toolTip = retryLabel
            (item.view as? NSButton)?.title = retryLabel
        }
        return delivered
    }

    private var retryLabel: String {
        retryDialog == "settings" ? copy("Settings unavailable · Retry", "设置暂不可用 · 重试")
            : copy("Connection unavailable · Retry", "连接暂不可用 · 重试")
    }

    func refresh(_ raw: String) {
        guard let data = raw.data(using: .utf8),
              let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let labels = ["no_target": copy("No connection selected", "尚未选择连接"),
            "target_starting": copy("Starting", "正在启动"), "ready": copy("Connected", "已连接"),
            "unreachable": copy("Unavailable", "无法连接"), "busy": copy("Busy", "忙碌")]
        let target = state["target"] as? [String: Any]
        let name = target?["kind"] as? String == "local" ? copy("This computer", "此电脑") : target?["name"] as? String
        let detail: String
        if state["state"] as? String == "incompatible" {
            let side = (state["incompatible"] as? [String: Any])?["side"] as? String
            detail = side == "host" ? copy("Update Host", "更新主机") : side == "app" ? copy("Update app", "更新应用") : copy("Check compatibility", "检查兼容性")
        } else { detail = labels[state["state"] as? String ?? ""] ?? copy("Unavailable", "无法连接") }
        status.title = name.map { "\($0) · \(detail)" } ?? detail
        status.toolTip = status.title; status.setAccessibilityLabel(status.title)
        let pending = state["candidate"] as? [String: Any]
        let phases = ["signing_in": copy("Signing in", "正在登录"), "awaiting_approval": copy("Awaiting approval", "等待批准"),
            "ready_to_switch": copy("Ready to switch", "准备切换"), "failed": copy("Connection failed", "连接失败"),
            "cancelled": copy("Cancelled", "已取消")]
        candidate.stringValue = pending.map { "\($0["name"] as? String ?? "") · \(phases[$0["phase"] as? String ?? ""] ?? "")" } ?? ""
        candidate.toolTip = candidate.stringValue; candidate.isHidden = pending == nil
        if let value = state["access_unsaved"] as? Bool { unsaved = value }
        optionalItem(Self.warning, visible: unsaved)
    }

    private func optionalItem(_ id: NSToolbarItem.Identifier, visible: Bool) {
        guard window != nil else { return }
        if let index = toolbar.items.firstIndex(where: { $0.itemIdentifier == id }) {
            if !visible { toolbar.removeItem(at: index) }
        } else if visible { toolbar.insertItem(withItemIdentifier: id, at: toolbar.items.count) }
    }

    @objc private func connectionAction() { open("connection") }
    @objc private func settingsAction() { open("settings") }
    @objc private func retryAction() { if let retryDialog { open(retryDialog) } }

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [Self.summary, .flexibleSpace, Self.connection, Self.settings] + (unsaved ? [Self.warning] : []) + (retryDialog != nil ? [Self.retry] : [])
    }
    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [Self.summary, .flexibleSpace, Self.connection, Self.settings, Self.warning, Self.retry]
    }
    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: id)
        if id == Self.summary {
            let stack = NSStackView(views: [status, candidate]); stack.orientation = .vertical; stack.spacing = 1
            item.view = stack; item.minSize = NSSize(width: 120, height: 44); item.maxSize = NSSize(width: 240, height: 44)
            item.label = copy("Connection status", "连接状态"); item.visibilityPriority = .low
        } else {
            let label: String, action: Selector
            switch id {
            case Self.connection: label = copy("Connection", "连接"); action = #selector(connectionAction)
            case Self.settings: label = copy("Settings", "设置"); action = #selector(settingsAction)
            case Self.warning: label = copy("Access wasn't saved", "访问权限未保存"); action = #selector(connectionAction)
            case Self.retry: label = retryLabel; action = #selector(retryAction)
            default: return nil
            }
            let button = NSButton(title: label, target: self, action: action); button.bezelStyle = .rounded
            button.setAccessibilityIdentifier("main-" + id.rawValue)
            item.view = button; item.label = label; item.target = self; item.action = action
            item.visibilityPriority = .high
            item.toolTip = id == Self.warning ? copy(
                "Access wasn't saved. Open Connection to retry saving; reopening may need a new invite.",
                "访问权限未保存。打开连接以重试保存；重新打开时可能需要新的邀请。") : label
            button.toolTip = item.toolTip
        }
        return item
    }
}

struct MainToolbarAttachment: NSViewRepresentable {
    let controller: MainToolbarController
    final class Anchor: NSView {
        weak var controller: MainToolbarController?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { controller?.attach(window) }
        }
    }
    func makeNSView(context: Context) -> Anchor { let view = Anchor(); view.controller = controller; return view }
    func updateNSView(_ view: Anchor, context: Context) {
        view.controller = controller
        if let window = view.window { controller.attach(window) }
    }
}
