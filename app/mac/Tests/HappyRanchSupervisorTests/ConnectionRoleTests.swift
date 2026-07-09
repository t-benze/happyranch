import Testing
@testable import HappyRanchSupervisor

@Suite("ConnectionRolePreference enum")
struct ConnectionRolePreferenceTests {

    @Test("all preferences are defined")
    func allPreferencesDefined() {
        let prefs: [ConnectionRolePreference] = [.undetermined, .home, .client]
        #expect(prefs.count == 3)
    }

    @Test("preference rawValues match expected strings")
    func preferenceRawValues() {
        #expect(ConnectionRolePreference.undetermined.rawValue == "undetermined")
        #expect(ConnectionRolePreference.home.rawValue == "home")
        #expect(ConnectionRolePreference.client.rawValue == "client")
    }

    @Test("preference is CaseIterable")
    func preferenceIsCaseIterable() {
        let all = ConnectionRolePreference.allCases
        #expect(all.contains(.undetermined))
        #expect(all.contains(.home))
        #expect(all.contains(.client))
    }
}

@Suite("ConnectionRole enum")
struct ConnectionRoleTests {

    @Test("all roles are defined")
    func allRolesDefined() {
        let roles: [ConnectionRole] = [.home, .client]
        #expect(roles.count == 2)
    }

    @Test("home role is local")
    func homeIsLocal() {
        #expect(ConnectionRole.home.isLocal)
    }

    @Test("client role is not local")
    func clientIsNotLocal() {
        #expect(!ConnectionRole.client.isLocal)
    }

    @Test("roles are disjoint")
    func rolesAreDisjoint() {
        #expect(ConnectionRole.home != ConnectionRole.client)
    }

    @Test("description matches rawValue")
    func descriptionMatchesRawValue() {
        #expect(ConnectionRole.home.description == "home")
        #expect(ConnectionRole.client.description == "client")
    }
}

@Suite("ConnectionRole detection")
struct ConnectionRoleDetectionTests {

    @Test("detects HOME when daemon is configured and running")
    func detectsHomeWhenRunning() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try? supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .home)
    }

    @Test("detects HOME when daemon is stopped but configured")
    func detectsHomeWhenStopped() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        // .stopped after configure

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .home)
    }

    @Test("detects HOME when daemon is externalRunning")
    func detectsHomeWhenExternalRunning() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.onExternalDaemonDetected(pid: 55555, port: 8080)

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .home)
    }

    @Test("detects HOME when daemon is in terminal state (crashed)")
    func detectsHomeWhenCrashed() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try? supervisor.start()
        supervisor.onProcessExited(exitCode: 1, signal: nil)

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .home)
    }

    @Test("detects HOME when daemon has stalePid")
    func detectsHomeWhenStalePid() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.onStalePidDetected(pid: 99999)

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .home)
    }

    @Test("detects CLIENT when daemon is not configured")
    func detectsClientWhenNotConfigured() {
        let supervisor = DaemonSupervisor()
        // never configured — stays .notConfigured

        let role = ConnectionRole.detect(supervisor: supervisor)
        #expect(role == .client)
    }

    // MARK: - Preference-based detection

    @Test("explicit HOME preference returns HOME even when supervisor is notConfigured")
    func explicitHomePreferenceWinsOverNotConfigured() {
        let supervisor = DaemonSupervisor()
        // never configured

        let role = ConnectionRole.detect(supervisor: supervisor, preference: .home)
        #expect(role == .home)
    }

    @Test("explicit CLIENT preference returns CLIENT even when supervisor is configured and running")
    func explicitClientPreferenceWinsOverRunning() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try? supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)

        let role = ConnectionRole.detect(supervisor: supervisor, preference: .client)
        #expect(role == .client)
    }

    @Test("undetermined preference falls back to supervisor state (HOME when configured)")
    func undeterminedPreferenceFallsBackToHomeWhenConfigured() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        let role = ConnectionRole.detect(supervisor: supervisor, preference: .undetermined)
        #expect(role == .home)
    }

    @Test("undetermined preference falls back to supervisor state (CLIENT when notConfigured)")
    func undeterminedPreferenceFallsBackToClientWhenNotConfigured() {
        let supervisor = DaemonSupervisor()
        // never configured

        let role = ConnectionRole.detect(supervisor: supervisor, preference: .undetermined)
        #expect(role == .client)
    }

    @Test("nil preference falls back to supervisor state (HOME when configured)")
    func nilPreferenceFallsBackToHomeWhenConfigured() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        let role = ConnectionRole.detect(supervisor: supervisor, preference: nil)
        #expect(role == .home)
    }

    @Test("nil preference falls back to supervisor state (CLIENT when notConfigured)")
    func nilPreferenceFallsBackToClientWhenNotConfigured() {
        let supervisor = DaemonSupervisor()

        let role = ConnectionRole.detect(supervisor: supervisor, preference: nil)
        #expect(role == .client)
    }
}
