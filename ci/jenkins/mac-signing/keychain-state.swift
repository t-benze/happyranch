// Public status of one exact keychain; no unlock, private-key or profile access.
import Foundation
import Security

guard CommandLine.arguments.count == 2 else { exit(2) }
let expected = NSHomeDirectory() + "/Library/Keychains/login.keychain-db"
guard CommandLine.arguments[1] == expected else { exit(2) }
var keychain: SecKeychain?
guard SecKeychainOpen(expected, &keychain) == errSecSuccess,
      let keychain = keychain else { exit(1) }
var status: SecKeychainStatus = 0
guard SecKeychainGetStatus(keychain, &status) == errSecSuccess,
      status & kSecUnlockStateStatus != 0,
      status & kSecReadPermStatus != 0 else { exit(1) }
print("exact-login-keychain-unlocked-readable")
