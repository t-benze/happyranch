import AppKit

enum MacLifetimeIntent: Equatable {
    case close, quit, update
    var wireAction: String { self == .update ? "relaunch" : self == .close ? "close" : "quit" }
}

/// A captured observation, never permission derived from the selected computer.
struct MacLifetimeObservation: Equatable {
    let process: ObjectIdentifier?
    let running: Bool?
    let owned: Bool
    let connection: HomeInviteModel.Lifetime
    let role: String
}

@MainActor
protocol MacAppLifetimeBackend: AnyObject {
    var lifetimeScheme: AppScheme? { get }
    var lifetimeObservation: MacLifetimeObservation { get }
    var lifetimeRefusal: String? { get }
    func prepareLifecycleConfirmation(token: UUID, intent: MacLifetimeIntent)
    func presentLifecycleConfirmation(_ prompt: MacAppLifetime.Prompt)
    func lifecycleConfirmed(token: UUID, choice: MacAppLifetime.Choice)
    func completeLifecycle(_ intent: MacLifetimeIntent, token: UUID) -> Bool
    func lifecycleHeld(_ reason: String)
}

/// One app-owned reservation, shared by Main close, system Quit and Sparkle.
/// Human consent precedes the web gate. Its reply never waits for a human or I/O.
@MainActor
final class MacAppLifetime {
    enum Kind: Equatable { case exit, updateIdle, updateBusy(Int?) }
    enum Choice: Equatable { case proceed, restart, now, whenIdle }
    struct Prompt {
        let token: UUID
        let intent: MacLifetimeIntent
        let kind: Kind
    }
    private enum Phase: Equatable { case preparing, confirming, web, executing, unknown }
    private struct Attempt {
        let token: UUID
        let intent: MacLifetimeIntent
        let scheme: AppScheme
        let anchor: AppScheme.LifecycleAnchor
        let observation: MacLifetimeObservation
        var phase = Phase.preparing
        var prompt: Prompt?
        var confirmedChoice: Choice?
    }
    private weak var backend: (any MacAppLifetimeBackend)?
    private var attempt: Attempt?
    var busy: Bool { attempt != nil }
    var confirmation: Prompt? { attempt?.phase == .confirming ? attempt?.prompt : nil }
    var token: UUID? { attempt?.token }
    var intent: MacLifetimeIntent? { attempt?.intent }

    init(backend: any MacAppLifetimeBackend) { self.backend = backend }

    @discardableResult
    func request(_ intent: MacLifetimeIntent) -> Bool {
        if let attempt {
            if attempt.phase == .web { attempt.scheme.emitLifecycle(attempt.token, action: attempt.intent.wireAction) }
            return false
        }
        guard let backend, let scheme = backend.lifetimeScheme, let anchor = scheme.lifecycleAnchor,
              backend.lifetimeRefusal == nil else {
            backend?.lifecycleHeld("lifecycle_unavailable"); return false
        }
        let token = UUID()
        attempt = Attempt(token: token, intent: intent, scheme: scheme, anchor: anchor,
                          observation: backend.lifetimeObservation)
        backend.prepareLifecycleConfirmation(token: token, intent: intent)
        return true
    }

    func offerConfirmation(token: UUID, kind: Kind) {
        guard var current = attempt, current.token == token, current.phase == .preparing else { return }
        guard unchanged(current) else { refuseBeforeGate("stateChanged"); return }
        let prompt = Prompt(token: token, intent: current.intent, kind: kind)
        current.phase = .confirming; current.prompt = prompt; current.confirmedChoice = nil; attempt = current
        backend?.presentLifecycleConfirmation(prompt)
    }

    /// The only native response ingress, shared by the AppKit completion and
    /// headless backend. Stale/duplicate/abort responses never grant permission.
    func respondToLifecycleConfirmation(token: UUID, response: NSApplication.ModalResponse) {
        guard var current = attempt, current.token == token, current.phase == .confirming,
              let prompt = current.prompt else { return }
        let choice: Choice
        switch (prompt.kind, response) {
        case (.exit, .alertSecondButtonReturn): choice = .proceed
        case (.updateIdle, .alertSecondButtonReturn): choice = .restart
        case (.updateBusy, .alertSecondButtonReturn): choice = .whenIdle
        case (.updateBusy(let count), .alertThirdButtonReturn) where count != nil: choice = .now
        default: refuseBeforeGate("permissionDenied"); return
        }
        guard unchanged(current) else { refuseBeforeGate("stateChanged"); return }
        current.phase = .preparing; current.prompt = nil; current.confirmedChoice = choice; attempt = current
        backend?.lifecycleConfirmed(token: token, choice: choice)
    }

    func beginWebGate(token: UUID) {
        guard var current = attempt, current.token == token, current.phase == .preparing else { return }
        if current.intent != .update {
            guard current.confirmedChoice == .proceed else { return }
        } else if current.observation.owned && current.observation.running != false {
            guard current.confirmedChoice == .restart || current.confirmedChoice == .now else { return }
        }
        guard unchanged(current) else { refuseBeforeGate("stateChanged"); return }
        current.phase = .web; attempt = current
        current.scheme.emitLifecycle(token, action: current.intent.wireAction)
    }

    /// Called only by the admitted document's synchronous WithReply dispatcher.
    /// ACK is receipt/admission, not a claim that teardown or installation ended.
    func receiveGate(scheme: AppScheme, requestID: String, decision: String,
                     reply: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        if let current = attempt, current.scheme === scheme, current.token.uuidString == requestID,
           current.phase == .executing || current.phase == .unknown {
            reply(nil, MacLocalControls.error("outcomeUnknown", effect: "unknown")); return
        }
        guard var current = attempt, current.scheme === scheme, current.phase == .web,
              current.token.uuidString == requestID else {
            reply(nil, MacLocalControls.error("stateChanged", recovery: "explicitRetry")); return
        }
        if decision == "keep" {
            attempt = nil; reply("{}", nil); return
        }
        guard unchanged(current), scheme.switchRefusal == nil else {
            attempt = nil
            reply(nil, MacLocalControls.error("stateChanged", recovery: "explicitRetry")); return
        }
        current.phase = .executing; attempt = current
        reply("{}", nil)
        // Reentrant requests now see the consumed slot. Incomplete effects keep
        // it even after document loss, subscription changes or a new summary.
        if backend?.completeLifecycle(current.intent, token: current.token) == true { attempt = nil }
        else {
            attempt?.phase = .unknown
            backend?.lifecycleHeld("lifecycle_outcome_unknown")
        }
    }

    func retire(_ scheme: AppScheme) {
        guard let current = attempt, current.scheme === scheme,
              current.phase != .executing, current.phase != .unknown else { return }
        refuseBeforeGate("documentChanged")
    }

    func cancelUnstarted(token: UUID) {
        guard let current = attempt, current.token == token,
              [Phase.preparing, .confirming, .web].contains(current.phase) else { return }
        refuseBeforeGate("permissionDenied")
    }

    func isExecuting(_ token: UUID, intent: MacLifetimeIntent) -> Bool {
        attempt?.token == token && attempt?.intent == intent && attempt?.phase == .executing
    }
    func effectsStillCurrent(_ token: UUID, intent: MacLifetimeIntent) -> Bool {
        guard let current = attempt, isExecuting(token, intent: intent) else { return false }
        return unchanged(current) && current.scheme.switchRefusal == nil
    }
    /// Child settlement may change its running/ownership observations. It must
    /// not erase a different document, target or native operation observed by
    /// the stop's nested run loop.
    func frontendStillCurrent(_ token: UUID, intent: MacLifetimeIntent) -> Bool {
        guard let current = attempt, let backend, isExecuting(token, intent: intent) else { return false }
        let observation = backend.lifetimeObservation
        return backend.lifetimeScheme === current.scheme && current.scheme.lifecycleAnchor == current.anchor
            && current.scheme.switchRefusal == nil && backend.lifetimeRefusal == nil
            && observation.connection == current.observation.connection && observation.role == current.observation.role
    }

    private func unchanged(_ current: Attempt) -> Bool {
        guard let backend else { return false }
        return backend.lifetimeScheme === current.scheme && current.scheme.lifecycleAnchor == current.anchor
            && backend.lifetimeObservation == current.observation && backend.lifetimeRefusal == nil
    }
    private func refuseBeforeGate(_ reason: String) {
        attempt = nil; backend?.lifecycleHeld(reason)
    }
}

/// Preserve SwiftUI's other delegate callbacks while routing the actual close
/// admission through the same app-lifetime owner as system Quit.
@MainActor
final class MacMainWindowOwner: NSObject, NSWindowDelegate {
    private weak var app: AppDelegate?
    private weak var previous: (any NSWindowDelegate)?
    private(set) weak var window: NSWindow?
    init(app: AppDelegate, window: NSWindow) {
        self.app = app; self.window = window; previous = window.delegate
        super.init(); window.delegate = self
    }
    nonisolated override func responds(to selector: Selector!) -> Bool {
        super.responds(to: selector) || MainActor.assumeIsolated { previous?.responds(to: selector) == true }
    }
    nonisolated override func forwardingTarget(for selector: Selector!) -> Any? {
        // NSObject's forwarding entry is nonisolated, but NSWindow delegate
        // dispatch is synchronous on the main actor. Verify that executor before
        // reading previous. Only this stack-local result crosses Swift's static
        // isolation boundary: the closure cannot escape or suspend, and the
        // reference returns to the same AppKit call without a task/thread hop.
        // NSWindowDelegate itself is not Sendable; do not declare it so.
        nonisolated(unsafe) var target: (any NSWindowDelegate)? = nil
        MainActor.assumeIsolated { target = previous }
        return target
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        app?.appLifetime.request(.close)
        return false
    }
    func closeAfterAdmission() { window?.close() }
}

@MainActor
protocol MacTerminationDelivery {
    func completeQuit()
}
struct AppKitTerminationDelivery: MacTerminationDelivery {
    func completeQuit() { NSApplication.shared.terminate(nil) }
}
