// TsnetSpike — Swift harness for the tsnet C-ABI bridge.
// THROWAWAY spike (THR-097).  Do NOT merge to main.
//
// This program links against the Go-built libtsnet.a and calls the
// C-ABI surface to verify:
//   1. The c-archive links into a Swift .app binary
//   2. All tsnet_* symbols are callable from Swift
//   3. Calling tsnet_init (without a real auth key) doesn't crash
//
// WireGuard runs entirely in-process.  No system tunnel (utun)
// interface is created, no NetworkExtension entitlement is required,
// and no VPN configuration profile is installed.

import Foundation
import CTsnet

// MARK: - Swift wrapper for cleaner calling

enum TsnetSpike {
    static func run() {
        print("=== THR-097 tsnet Swift bridge spike ===")
        print("Binary path: \(CommandLine.arguments[0])")
        print("")

        // 1. Verify tsnet_init is callable (will fail without real auth key)
        print("[1] tsnet_init (no auth key) ...")
        let rc = tsnet_init("", nil, "tsnet-spike-mac")
        print("    tsnet_init returned: \(rc)")
        if rc != 0 {
            if let err = tsnet_last_error() {
                let msg = String(cString: err)
                tsnet_free_string(err)
                print("    last_error: \(msg)")
            }
        }
        print("")

        // 2. Verify tsnet_start is callable (will fail without init)
        print("[2] tsnet_start (without init) ...")
        let rc2 = tsnet_start()
        print("    tsnet_start returned: \(rc2)")
        if rc2 != 0 {
            if let err = tsnet_last_error() {
                let msg = String(cString: err)
                tsnet_free_string(err)
                print("    last_error: \(msg)")
            }
        }
        print("")

        // 3. Verify tsnet_dial is callable (will fail without init)
        print("[3] tsnet_dial (without init) ...")
        let rc3 = tsnet_dial("tcp", "100.64.0.1:80", 1000)
        print("    tsnet_dial returned: \(rc3)")
        if rc3 != 0 {
            if let err = tsnet_last_error() {
                let msg = String(cString: err)
                tsnet_free_string(err)
                print("    last_error: \(msg)")
            }
        }
        print("")

        // 4. Verify tsnet_close is callable (idempotent)
        print("[4] tsnet_close ...")
        tsnet_close()
        print("    tsnet_close returned (void)")

        print("")
        print("=== SUCCESS: All tsnet_* symbols linked and callable ===")
        print("The c-archive (.a) links correctly into this Swift binary.")
        print("No crash, no undefined symbol errors, no VPN entitlement needed.")
    }
}

TsnetSpike.run()
