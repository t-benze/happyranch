import Foundation

/// Reads existing public host admission health across tasks, threads, dreams and jobs.
public enum UpdateSessionProbe {
    private struct Snapshot: Decodable {
        let status: String
        let host_sessions: Host
        struct Host: Decodable {
            let wired: Bool
            let backend: Backend
            let admission: Admission
            let residue: Residue
        }
        struct Backend: Decodable { let healthy: Bool }
        struct Admission: Decodable {
            let active: Int
            let queue_depth: Int
            let shutdown: Bool
        }
        struct Residue: Decodable {
            let admission_blocked: Bool
            let survivors_count: Int
        }
    }

    public static func runningSessionCount(in data: Data) -> Int? {
        guard let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data),
              snapshot.status == "ok", snapshot.host_sessions.wired,
              snapshot.host_sessions.backend.healthy,
              !snapshot.host_sessions.residue.admission_blocked,
              snapshot.host_sessions.residue.survivors_count == 0 else { return nil }
        let admission = snapshot.host_sessions.admission
        guard !admission.shutdown, admission.active >= 0, admission.queue_depth >= 0,
              // Queued admission must not look idle between two running sessions.
              admission.active > 0 || admission.queue_depth == 0 else { return nil }
        return admission.active
    }

    public static func count(homeDir: String) async -> Int? {
        do {
            let port = try PortReader().readPort(from: URL(fileURLWithPath: homeDir)
                .appendingPathComponent("daemon.port"))
            guard let url = URL(string: "http://127.0.0.1:\(port)/api/v1/health") else { return nil }
            let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForResource = 5
            let session = URLSession(configuration: configuration, delegate: NoUpdateRedirects(), delegateQueue: nil)
            defer { session.invalidateAndCancel() }
            let (data, response) = try await session.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return runningSessionCount(in: data)
        } catch {
            // Do not log network requests or credentials. Uncertainty is BUSY.
            return nil
        }
    }
}

private final class NoUpdateRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
