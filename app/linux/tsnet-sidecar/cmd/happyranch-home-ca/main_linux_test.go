package main

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"happyranch/linux-tsnet-sidecar/homeca"
)

func testSystem(t *testing.T) system {
	t.Helper()
	s := system{t.TempDir(), uint32(os.Getuid()), uint32(os.Getgid()), 0, time.Date(2026, 11, 18, 0, 0, 0, 0, time.UTC)}
	if err := os.Chmod(s.root, 0700); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"var", "var/lib"} {
		if err := os.Mkdir(filepath.Join(s.root, p), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(filepath.Join(s.root, p), 0755); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func path(s system, name string) string { return filepath.Join(s.root, custodyPath, name) }

func invoke(s system, args ...string) (int, string, string) {
	var out, err bytes.Buffer
	code := command(args, &out, &err, s)
	return code, out.String(), err.String()
}

func writeFixture(t *testing.T, s system, name string, data []byte) {
	t.Helper()
	if err := os.WriteFile(path(s, name), data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path(s, name), 0600); err != nil {
		t.Fatal(err)
	}
}

// Independent input fixture: the existing in-memory issuer owns crypto. This
// does not call ensure or reimplement its filesystem admission/publication.
func seed(t *testing.T, s system) {
	t.Helper()
	for _, p := range []string{"..", "."} {
		if err := os.MkdirAll(path(s, p), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(path(s, p), 0700); err != nil {
			t.Fatal(err)
		}
	}
	ca, key, err := homeca.NewCA(s.now, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf, leafKey, err := homeca.IssueLeaf(ca, key, s.now, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	for name, data := range map[string][]byte{
		"ca.pem":   pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca}),
		"ca.key":   pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key.DER()}),
		"leaf.pem": pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: leaf}),
		"leaf.key": pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: leafKey.DER()}),
	} {
		writeFixture(t, s, name, data)
	}
}

func read(t *testing.T, s system, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(path(s, name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// Include identity, bytes, metadata and symlink targets; never follow links or
// open special files. Access time is excluded because inspection may read.
func snapshot(t *testing.T, s system) map[string]string {
	t.Helper()
	result := map[string]string{}
	err := filepath.Walk(s.root, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		st := info.Sys().(*syscall.Stat_t)
		value := fmt.Sprintf("%d/%d/%d/%d/%d/%d/%v/%v", st.Dev, st.Ino, st.Mode, st.Uid, st.Gid, st.Nlink, st.Mtim, st.Ctim)
		if info.Mode().IsRegular() {
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			value += fmt.Sprintf("/%x", sha256.Sum256(b))
		} else if info.Mode()&os.ModeSymlink != 0 {
			target, err := os.Readlink(p)
			if err != nil {
				return err
			}
			value += "/" + target
		}
		result[p] = value
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func assertRefusal(t *testing.T, s system, verb string, want refusal) {
	t.Helper()
	before := snapshot(t, s)
	code, out, err := invoke(s, verb)
	if code != 1 || out != "" || err != string(want)+"\n" {
		t.Fatalf("refusal: code=%d, stdout length=%d, category=%q; want %q", code, len(out), err, want)
	}
	if !reflect.DeepEqual(before, snapshot(t, s)) {
		t.Fatal("refusal mutated custody")
	}
}

func TestEnsureCreatesIdentity(t *testing.T) {
	s := testSystem(t)
	code, out, err := invoke(s, "ensure")
	if code != 0 || err != "" {
		t.Fatalf("ensure code=%d category=%q", code, err)
	}
	var result status
	if json.Unmarshal([]byte(out), &result) != nil || strings.Count(out, "\n") != 1 || result.RenewalDue {
		t.Fatal("invalid single-line status")
	}
	for _, name := range names {
		st, err := os.Lstat(path(s, name))
		if err != nil || st.Mode() != 0600 || st.Sys().(*syscall.Stat_t).Uid != s.uid || st.Sys().(*syscall.Stat_t).Gid != s.gid {
			t.Fatalf("wrong custody metadata for %s", name)
		}
	}
	for _, name := range []string{".", ".."} {
		st, err := os.Lstat(path(s, name))
		if err != nil || st.Mode() != os.ModeDir|0700 || st.Sys().(*syscall.Stat_t).Uid != s.uid || st.Sys().(*syscall.Stat_t).Gid != s.gid {
			t.Fatal("wrong directory custody")
		}
	}
	ca, _ := pem.Decode(read(t, s, "ca.pem"))
	leaf, _ := pem.Decode(read(t, s, "leaf.pem"))
	if ca == nil || leaf == nil || homeca.Check(ca.Bytes, leaf.Bytes, s.now) != nil {
		t.Fatal("not an admitted home identity")
	}
	cert, _ := x509.ParseCertificate(leaf.Bytes)
	if !reflect.DeepEqual(cert.DNSNames, []string{"control.happyranch.invalid"}) || len(cert.IPAddresses)+len(cert.EmailAddresses)+len(cert.URIs) != 0 {
		t.Fatal("SAN not fixed DNS only")
	}
	for _, name := range []string{"ca", "leaf"} {
		if _, err := tls.X509KeyPair(read(t, s, name+".pem"), read(t, s, name+".key")); err != nil {
			t.Fatal("certificate/key mismatch")
		}
	}
	if result.CAFingerprint != homeca.Fingerprint(ca.Bytes) || result.CANotAfter == "" || result.LeafNotAfter == "" {
		t.Fatal("status identity missing")
	}
}

func TestEnsureIdempotent(t *testing.T) {
	s := testSystem(t)
	seed(t, s)
	before := snapshot(t, s)
	if code, _, err := invoke(s, "ensure"); code != 0 || err != "" {
		t.Fatalf("ensure code=%d category=%q", code, err)
	}
	if !reflect.DeepEqual(before, snapshot(t, s)) {
		t.Fatal("ensure changed an identity not due for renewal")
	}
}

func TestSameCARenewal(t *testing.T) {
	for _, action := range []string{"renew-leaf", "ensure-due", "ensure-missing-leaf", "ensure-expired-leaf"} {
		t.Run(action, func(t *testing.T) {
			s := testSystem(t)
			seed(t, s)
			ca, key := read(t, s, "ca.pem"), read(t, s, "ca.key")
			leaf, leafKey := read(t, s, "leaf.pem"), read(t, s, "leaf.key")
			verb := "ensure"
			switch action {
			case "renew-leaf":
				verb = action
			case "ensure-due":
				s.now = s.now.Add(280 * 24 * time.Hour)
			case "ensure-expired-leaf":
				s.now = s.now.Add(366 * 24 * time.Hour)
			case "ensure-missing-leaf":
				for _, name := range []string{"leaf.pem", "leaf.key"} {
					if err := os.Remove(path(s, name)); err != nil {
						t.Fatal(err)
					}
				}
			}
			if code, _, err := invoke(s, verb); code != 0 || err != "" {
				t.Fatalf("renewal code=%d category=%q", code, err)
			}
			if !bytes.Equal(ca, read(t, s, "ca.pem")) || !bytes.Equal(key, read(t, s, "ca.key")) {
				t.Fatal("renewal changed CA identity")
			}
			if bytes.Equal(leaf, read(t, s, "leaf.pem")) || bytes.Equal(leafKey, read(t, s, "leaf.key")) {
				t.Fatal("renewal did not rotate leaf and key")
			}
			cb, _ := pem.Decode(ca)
			lb, _ := pem.Decode(read(t, s, "leaf.pem"))
			if homeca.Check(cb.Bytes, lb.Bytes, s.now) != nil {
				t.Fatal("renewed leaf not admitted under exact CA")
			}
		})
	}
}

func TestCARefusals(t *testing.T) {
	for _, verb := range []string{"ensure", "renew-leaf"} {
		for _, kind := range []string{"invalid", "expired", "not-yet-valid", "near-expiry", "key-canary", "mismatched-key"} {
			t.Run(verb+"/"+kind, func(t *testing.T) {
				s := testSystem(t)
				seed(t, s)
				want := errCA
				switch kind {
				case "invalid":
					writeFixture(t, s, "ca.pem", []byte("synthetic-secret-canary"))
				case "expired":
					s.now = s.now.AddDate(10, 0, 0)
				case "not-yet-valid":
					s.now = s.now.Add(-time.Hour)
				case "near-expiry":
					s.now = s.now.AddDate(10, 0, 0).Add(-29 * 24 * time.Hour)
					want = errLifetime
				case "key-canary":
					writeFixture(t, s, "ca.key", []byte("synthetic-secret-canary"))
					want = errKey
				case "mismatched-key":
					writeFixture(t, s, "ca.key", read(t, s, "leaf.key"))
					want = errKey
				}
				assertRefusal(t, s, verb, want)
			})
		}
	}
}

func TestUnsafeCustody(t *testing.T) {
	for _, kind := range []string{"parent-symlink", "parent-mode", "file-symlink", "file-mode", "hardlink", "fifo", "directory", "extra", "pending", "oversized", "half-leaf", "half-ca"} {
		t.Run(kind, func(t *testing.T) {
			s := testSystem(t)
			seed(t, s)
			want := errCustody
			var err error
			switch kind {
			case "parent-symlink":
				parent := filepath.Dir(path(s, "."))
				err = os.Rename(parent, parent+"-moved")
				if err == nil {
					err = os.Symlink(parent+"-moved", parent)
				}
			case "parent-mode":
				err = os.Chmod(path(s, "."), 0755)
			case "file-symlink", "fifo", "directory":
				err = os.Remove(path(s, "leaf.key"))
				if err == nil {
					switch kind {
					case "file-symlink":
						err = os.Symlink("ca.key", path(s, "leaf.key"))
					case "fifo":
						err = syscall.Mkfifo(path(s, "leaf.key"), 0600)
					case "directory":
						err = os.Mkdir(path(s, "leaf.key"), 0600)
					}
				}
			case "file-mode":
				err = os.Chmod(path(s, "ca.key"), 0640)
			case "hardlink":
				err = os.Link(path(s, "ca.key"), filepath.Join(s.root, "linked-key"))
			case "extra", "pending":
				name := "unowned"
				if kind == "pending" {
					name = ".ca.key.pending"
				}
				writeFixture(t, s, name, []byte("synthetic-secret-canary"))
				want = errEntries
			case "oversized":
				writeFixture(t, s, "ca.key", bytes.Repeat([]byte("x"), maxFileBytes+1))
			case "half-ca", "half-leaf":
				name := "leaf.pem"
				if kind == "half-ca" {
					name = "ca.pem"
				}
				err = os.Remove(path(s, name))
				want = errIncomplete
			}
			if err != nil {
				t.Fatal(err)
			}
			for _, verb := range []string{"ensure", "renew-leaf", "status"} {
				assertRefusal(t, s, verb, want)
			}
		})
	}
}

func TestCrashBoundary(t *testing.T) {
	// The test binary alone has this environment hook. Run the actual first
	// atomic key publication (including real fsyncs), then exit without unwinding.
	if root := os.Getenv("HOME_CA_TEST_CRASH_ROOT"); root != "" {
		s := system{root, uint32(os.Getuid()), uint32(os.Getgid()), 0, time.Now()}
		dir, err := openCustody(s, true)
		if err != nil {
			t.Fatal(err)
		}
		_, key, err := homeca.NewCA(s.now, rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		name := os.Getenv("HOME_CA_TEST_CRASH_PAIR")
		if atomicWrite(dir, name+".key", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key.DER()}), s) != nil {
			t.Fatal("key publication failed")
		}
		os.Exit(77)
	}
	for _, pair := range []string{"ca", "leaf"} {
		t.Run(pair, func(t *testing.T) {
			s := testSystem(t)
			want := errIncomplete
			if pair == "leaf" {
				seed(t, s) // rotation crash: old certificate, newly durable key
				want = errKey
			}
			child := exec.Command(os.Args[0], "-test.run=^TestCrashBoundary$")
			child.Env = append(os.Environ(), "HOME_CA_TEST_CRASH_ROOT="+s.root, "HOME_CA_TEST_CRASH_PAIR="+pair)
			if err := child.Run(); err == nil || child.ProcessState.ExitCode() != 77 {
				t.Fatal("child did not reach durable key-first boundary")
			}
			for _, verb := range []string{"ensure", "renew-leaf"} {
				assertRefusal(t, s, verb, want)
			}
		})
	}
}

func TestStatusAndOutputSafety(t *testing.T) {
	s := testSystem(t)
	seed(t, s)
	// Status must not read or parse private-key contents at all.
	canary := "synthetic-secret-canary"
	caKey, leafKey := read(t, s, "ca.key"), read(t, s, "leaf.key")
	writeFixture(t, s, "ca.key", []byte(canary))
	writeFixture(t, s, "leaf.key", []byte(canary))
	for _, due := range []bool{false, true} {
		if due {
			s.now = s.now.Add(280 * 24 * time.Hour)
		}
		before := snapshot(t, s)
		code, out, err := invoke(s, "status")
		var fields map[string]any
		if code != 0 || err != "" || json.Unmarshal([]byte(out), &fields) != nil || len(fields) != 4 || fields["renewal_due"] != due {
			t.Fatal("status is not the closed secret-free projection")
		}
		if strings.Count(out, "\n") != 1 || !reflect.DeepEqual(before, snapshot(t, s)) {
			t.Fatal("status mutated files or emitted multiple lines")
		}
		for _, secret := range []string{canary, string(caKey), string(leafKey), "PRIVATE KEY", "BEGIN CERTIFICATE"} {
			if strings.Contains(out+err, secret) {
				t.Fatal("secret material in output")
			}
		}
	}
	assertRefusal(t, s, "ensure", errKey)
}

func TestUsageAndRoot(t *testing.T) {
	s := testSystem(t)
	for _, args := range [][]string{nil, {"unknown"}, {"ensure", "/tmp/redirect"}, {"status", "--root=/tmp"}} {
		before := snapshot(t, s)
		if code, out, err := invoke(s, args...); code != 2 || out != "" || err != "usage\n" || !reflect.DeepEqual(before, snapshot(t, s)) {
			t.Fatal("usage did not refuse before effects")
		}
	}
	s.euid = 1000
	for _, verb := range []string{"ensure", "renew-leaf"} {
		assertRefusal(t, s, verb, errRoot)
	}
	// Also exercise the actual process euid instead of relying only on the
	// synthetic non-root case. A root venue is neither required nor claimed.
	if os.Geteuid() != 0 {
		s.euid = os.Geteuid()
		assertRefusal(t, s, "ensure", errRoot)
	}
}
