import HappyRanchApp

/// Only the executable owns an entrypoint; tests link the app library.
@main
enum HappyRanchLauncher {
    @MainActor
    static func main() async {
        await AppEntry.main()
    }
}
