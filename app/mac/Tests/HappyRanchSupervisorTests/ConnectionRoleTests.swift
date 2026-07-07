import Testing
@testable import HappyRanchSupervisor

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
}
