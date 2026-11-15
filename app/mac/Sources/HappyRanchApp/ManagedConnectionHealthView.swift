import SwiftUI
import HappyRanchSupervisor

struct ManagedConnectionHealthView: View {
    let bridge: ClientBridge
    @Environment(\.scenePhase) private var scenePhase
    @State private var windowVisible = false
    @State private var inFlight = false
    @State private var record: TsnetHealthRecord?

    private var refreshAllowed: Bool { windowVisible && scenePhase == .active }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let record {
                Text(record.summary).font(.caption).foregroundColor(.secondary)
            }
            Button("Check connection") { Task { await refresh() } }
                .disabled(inFlight || !refreshAllowed)
        }
        .background(HealthWindowVisibility(visible: $windowVisible).frame(width: 0, height: 0))
        .task(id: refreshAllowed) {
            guard refreshAllowed else { return }
            while !Task.isCancelled, refreshAllowed {
                let next = ContinuousClock.now.advanced(by: .seconds(60))
                await refresh()
                do { try await Task.sleep(until: next, clock: .continuous) }
                catch { return }
            }
        }
    }

    @MainActor private func refresh() async {
        guard refreshAllowed, !inFlight, !Task.isCancelled else { return }
        inFlight = true
        defer { inFlight = false }
        let result = await bridge.healthRecord(timeoutMs: 10_000)
        guard !Task.isCancelled else { return }
        record = result
    }
}

/// Observe this view's hosting window, rather than an unrelated key window.
/// SwiftUI cancels the cadence on disappearance; occlusion/minimization changes
/// cancel it even when the view remains in the hierarchy.
private struct HealthWindowVisibility: NSViewRepresentable {
    @Binding var visible: Bool

    func makeNSView(context: Context) -> ObserverView {
        let view = ObserverView()
        view.changed = { visible = $0 }
        return view
    }

    func updateNSView(_ view: ObserverView, context: Context) {}

    static func dismantleNSView(_ view: ObserverView, coordinator: ()) {
        NotificationCenter.default.removeObserver(view)
        view.changed = nil
    }

    final class ObserverView: NSView {
        var changed: ((Bool) -> Void)?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            let center = NotificationCenter.default
            center.removeObserver(self)
            if let window {
                for name in [NSWindow.didChangeOcclusionStateNotification,
                             NSWindow.didMiniaturizeNotification, NSWindow.didDeminiaturizeNotification,
                             NSWindow.willCloseNotification] {
                    center.addObserver(self, selector: #selector(updateVisibility), name: name, object: window)
                }
            }
            updateVisibility()
        }

        @objc private func updateVisibility() {
            // Defer binding changes until after the current SwiftUI update.
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                let visible = self.window.map {
                    $0.isVisible && !$0.isMiniaturized && $0.occlusionState.contains(.visible)
                } ?? false
                self.changed?(visible)
            }
        }
    }
}
