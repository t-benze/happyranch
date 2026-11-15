import Foundation

/// Reads the all-producer update observation. Old daemons remain unknown/BUSY.
public enum UpdateSessionProbe {
    private struct Snapshot: Decodable {
        let status: String
        let update_busy: Busy
        struct Busy: Decodable {
            let busy: Bool
            let unknown: Bool
            let counts: Counts
        }
        struct Counts: Decodable {
            let host_active: Int
            let host_queued: Int
            let jobs_inflight: Int
            let jobs_nonterminal: Int
            let invocations_nonterminal: Int
        }
    }

    /// Evidence counts may overlap and saturate at 1000; only zero means idle.
    public static func runningSessionCount(in data: Data) -> Int? {
        guard let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data),
              snapshot.status == "ok", !snapshot.update_busy.unknown else { return nil }
        let counts = snapshot.update_busy.counts
        let values = [counts.host_active, counts.host_queued, counts.jobs_inflight,
                      counts.jobs_nonterminal, counts.invocations_nonterminal]
        guard values.allSatisfy({ $0 >= 0 && $0 <= 1000 }) else { return nil }
        let total = values.reduce(0, +)
        guard snapshot.update_busy.busy == (total > 0) else { return nil }
        return total
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
