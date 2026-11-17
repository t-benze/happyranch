package sidecar

import (
	"bytes"
	"crypto/rand"
	"encoding/pem"
	"os"
	"path/filepath"
	"testing"
	"time"

	"happyranch/linux-tsnet-sidecar/homeca"
)

// Unlike TestHomeCAAdmission's hand-built fixture, this exercises the actual
// generator -> PEM -> protected-file admission contract, without root actions.
func TestHomeCAGeneratedAdmission(t *testing.T) {
	for _, expired := range []bool{false, true} {
		name := "current"
		if expired {
			name = "expired"
		}
		t.Run(name, func(t *testing.T) {
			now := time.Now()
			if expired {
				now = now.AddDate(-11, 0, 0)
			}
			der, _, err := homeca.NewCA(now, rand.Reader)
			if err != nil {
				t.Fatal(err)
			}
			dir := t.TempDir()
			if err := os.Chmod(dir, 0o700); err != nil {
				t.Fatal(err)
			}
			bundle, empty := filepath.Join(dir, "control-ca.pem"), filepath.Join(dir, "empty")
			if err := os.Mkdir(empty, 0o755); err != nil {
				t.Fatal(err)
			}
			data := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
			if err := os.WriteFile(bundle, data, 0o640); err != nil {
				t.Fatal(err)
			}
			// Defeat an ambient umask without weakening production ownership rules.
			if err := os.Chmod(bundle, 0o640); err != nil {
				t.Fatal(err)
			}
			if err := os.Chmod(empty, 0o755); err != nil {
				t.Fatal(err)
			}
			p := homeTrustPolicy{uid: uint32(os.Getuid()), caGID: uint32(os.Getgid()), directoryGID: uint32(os.Getgid())}
			admitted, err := p.admit(bundle, empty, homeca.Fingerprint(der))
			if expired {
				if err == nil || admitted != nil {
					t.Fatal("expired generated CA admitted")
				}
			} else if err != nil || admitted == nil || !bytes.Equal(admitted.Raw, der) {
				t.Fatal("generated CA not admitted unchanged")
			}
		})
	}
}
