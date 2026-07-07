import Testing
import Foundation
@testable import HappyRanchSupervisor

@Suite("ConnectionState enum")
struct ConnectionStateTests {

    @Test("all states are defined")
    func allStatesDefined() {
        let states: [ConnectionState] = [
            .online,
            .offline,
            .reconnecting,
            .tailnetNotDetected,
            .pairedUnreachable
        ]
        #expect(states.count == 5)
    }

    @Test("online is connected")
    func onlineIsConnected() {
        #expect(ConnectionState.online.isConnected)
    }

    @Test("offline, reconnecting, tailnetNotDetected, pairedUnreachable are not connected")
    func nonOnlineAreNotConnected() {
        #expect(!ConnectionState.offline.isConnected)
        #expect(!ConnectionState.reconnecting.isConnected)
        #expect(!ConnectionState.tailnetNotDetected.isConnected)
        #expect(!ConnectionState.pairedUnreachable.isConnected)
    }

    @Test("only reconnecting and online are transient")
    func transientStates() {
        #expect(ConnectionState.online.isTransient)
        #expect(ConnectionState.reconnecting.isTransient)
        #expect(!ConnectionState.offline.isTransient)
        #expect(!ConnectionState.tailnetNotDetected.isTransient)
        #expect(!ConnectionState.pairedUnreachable.isTransient)
    }

    @Test("description matches rawValue")
    func descriptionMatchesRawValue() {
        #expect(ConnectionState.online.description == "online")
        #expect(ConnectionState.offline.description == "offline")
        #expect(ConnectionState.reconnecting.description == "reconnecting")
        #expect(ConnectionState.tailnetNotDetected.description == "tailnetNotDetected")
        #expect(ConnectionState.pairedUnreachable.description == "pairedUnreachable")
    }
}

@Suite("ConnectionStateManager state machine")
struct ConnectionStateManagerStateMachineTests {

    // MARK: - Initial state

    @Test("initial state is tailnetNotDetected before any detection")
    func initialState() {
        let manager = ConnectionStateManager()
        #expect(manager.state == .tailnetNotDetected)
        #expect(manager.lastHeartbeat == nil)
    }

    // MARK: - Tailscale detection transitions

    @Test("tailnetNotDetected -> offline when tailscale is detected but home node not found")
    func tailnetDetectedNoHomeNode() {
        let manager = ConnectionStateManager()
        #expect(manager.state == .tailnetNotDetected)

        manager.onTailscaleDetected(homeNodeOnline: false)
        #expect(manager.state == .offline)
    }

    @Test("tailnetNotDetected -> online when tailscale is detected AND home node is online")
    func tailnetDetectedHomeNodeOnline() {
        let manager = ConnectionStateManager()
        #expect(manager.state == .tailnetNotDetected)

        manager.onTailscaleDetected(homeNodeOnline: true)
        #expect(manager.state == .online)
        #expect(manager.lastHeartbeat != nil)
    }

    // MARK: - Online/offline toggle (heartbeat-based)

    @Test("online -> offline on heartbeat failure")
    func onlineToOffline() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        #expect(manager.state == .online)

        manager.onHeartbeatFailed()
        #expect(manager.state == .offline)
    }

    @Test("offline -> online on heartbeat success")
    func offlineToOnline() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        manager.onHeartbeatFailed()
        #expect(manager.state == .offline)

        manager.onHeartbeatSucceeded()
        #expect(manager.state == .online)
        #expect(manager.lastHeartbeat != nil)
    }

    // MARK: - Reconnecting transitions

    @Test("online -> reconnecting on connection drop")
    func onlineToReconnecting() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        #expect(manager.state == .online)

        manager.onConnectionDrop()
        #expect(manager.state == .reconnecting)
    }

    @Test("reconnecting -> online on reconnect")
    func reconnectingToOnline() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        manager.onConnectionDrop()
        #expect(manager.state == .reconnecting)

        manager.onReconnected()
        #expect(manager.state == .online)
        #expect(manager.lastHeartbeat != nil)
    }

    @Test("reconnecting -> pairedUnreachable after reconnect timeout")
    func reconnectingToPairedUnreachable() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        manager.onConnectionDrop()
        #expect(manager.state == .reconnecting)

        manager.onReconnectTimeout()
        #expect(manager.state == .pairedUnreachable)
    }

    // MARK: - offline transitions

    @Test("offline -> reconnecting on connection drop (recovery attempt)")
    func offlineToReconnecting() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: false)
        #expect(manager.state == .offline)

        // If we try to connect and it drops — go to reconnecting not pairedUnreachable
        manager.onConnectionDrop()
        #expect(manager.state == .reconnecting)
    }

    // MARK: - pairedUnreachable recovery

    @Test("pairedUnreachable -> online on reconnect after unreachable")
    func pairedUnreachableToOnline() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        manager.onConnectionDrop()
        manager.onReconnectTimeout()
        #expect(manager.state == .pairedUnreachable)

        manager.onReconnected()
        #expect(manager.state == .online)
    }

    // MARK: - Tailscale loss

    @Test("any state -> tailnetNotDetected when tailscale disappears")
    func anyStateToTailnetNotDetected() {
        let states: [ConnectionState] = [.online, .offline, .reconnecting, .pairedUnreachable]
        for initialState in states {
            let manager = ConnectionStateManager()
            // Force into the initial state
            switch initialState {
            case .online:
                manager.onTailscaleDetected(homeNodeOnline: true)
            case .offline:
                manager.onTailscaleDetected(homeNodeOnline: false)
            case .reconnecting:
                manager.onTailscaleDetected(homeNodeOnline: true)
                manager.onConnectionDrop()
            case .pairedUnreachable:
                manager.onTailscaleDetected(homeNodeOnline: true)
                manager.onConnectionDrop()
                manager.onReconnectTimeout()
            default:
                break
            }
            #expect(manager.state == initialState, "failed to enter \(initialState)")

            manager.onTailscaleLost()
            #expect(manager.state == .tailnetNotDetected, "\(initialState) -> tailnetNotDetected failed")
        }
    }

    // MARK: - Heartbeat timestamp

    @Test("lastHeartbeat is updated on online transition")
    func heartbeatUpdatedOnOnline() {
        let manager = ConnectionStateManager()
        #expect(manager.lastHeartbeat == nil)

        // Going online from tailnet detection sets heartbeat
        manager.onTailscaleDetected(homeNodeOnline: true)
        let firstHeartbeat = manager.lastHeartbeat
        #expect(firstHeartbeat != nil)

        // A short sleep (real code will have different timestamps)
        // In tests timestamps are nearly identical; verify the field is set
        manager.onHeartbeatSucceeded()
        #expect(manager.lastHeartbeat != nil)
    }

    @Test("lastHeartbeat is NOT updated when transitioning to non-online")
    func heartbeatNotUpdatedOnNonOnline() {
        let manager = ConnectionStateManager()
        manager.onTailscaleDetected(homeNodeOnline: true)
        let beforeHeartbeat = manager.lastHeartbeat
        #expect(beforeHeartbeat != nil)

        manager.onConnectionDrop()
        // Heartbeat should remain from the last online moment
        #expect(manager.state == .reconnecting)
        #expect(manager.lastHeartbeat == beforeHeartbeat)
    }
}
