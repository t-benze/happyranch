// happyranch-home-ca is an inert root-only materializer, never a service.
package main

import (
	"bytes"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"os"
	"syscall"
	"time"

	"happyranch/linux-tsnet-sidecar/homeca"
)

const custodyPath = "/var/lib/happyranch-remote-access/home-ca"
const maxFileBytes = 4096

// refusal deliberately retains no underlying I/O, PEM or private-key error.
type refusal string

func (e refusal) Error() string { return string(e) }

const (
	errUsage      refusal = "usage"
	errRoot       refusal = "root-required"
	errCustody    refusal = "unsafe-custody"
	errEntries    refusal = "unexpected-entry"
	errIncomplete refusal = "incomplete-state"
	errCA         refusal = "invalid-ca"
	errLifetime   refusal = "ca-lifetime"
	errKey        refusal = "invalid-key"
	errLeaf       refusal = "invalid-leaf"
	errIO         refusal = "io-failure"
	errBusy       refusal = "busy"
	errGeneration refusal = "generation-failure"
)

// The installed entry always supplies literal /, root:root, the actual euid
// and current time. Only same-package tests supply an isolated temporary root
// and observable test ownership; there is no flag/environment override.
type system struct {
	root     string
	uid, gid uint32
	euid     int
	now      time.Time
}

type status struct {
	CAFingerprint string `json:"ca_fingerprint"`
	CANotAfter    string `json:"ca_not_after"`
	LeafNotAfter  string `json:"leaf_not_after"`
	RenewalDue    bool   `json:"renewal_due"`
}

func main() {
	os.Exit(command(os.Args[1:], os.Stdout, os.Stderr, system{"/", 0, 0, os.Geteuid(), time.Now()}))
}

func command(args []string, stdout, stderr io.Writer, s system) int {
	if len(args) != 1 || (args[0] != "ensure" && args[0] != "renew-leaf" && args[0] != "status") {
		_, _ = io.WriteString(stderr, string(errUsage)+"\n")
		return 2
	}
	if args[0] != "status" && s.euid != 0 {
		_, _ = io.WriteString(stderr, string(errRoot)+"\n")
		return 1
	}
	result, err := materialize(args[0], s)
	if err != nil {
		category, ok := err.(refusal)
		if !ok {
			category = errIO
		}
		_, _ = io.WriteString(stderr, string(category)+"\n")
		return 1
	}
	if json.NewEncoder(stdout).Encode(result) != nil {
		_, _ = io.WriteString(stderr, string(errIO)+"\n")
		return 1
	}
	return 0
}

func directory(fd int, mode uint32, s system) error {
	var st syscall.Stat_t
	if syscall.Fstat(fd, &st) != nil || st.Mode != syscall.S_IFDIR|mode || st.Uid != s.uid || st.Gid != s.gid {
		return errCustody
	}
	return nil
}

func openCustody(s system, create bool) (*os.File, error) {
	fd, err := syscall.Open(s.root, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errCustody
	}
	// / and the distribution-owned /var and /var/lib must be root-owned
	// directories without group/world writes. Managed directories are exact 0700.
	var st syscall.Stat_t
	if syscall.Fstat(fd, &st) != nil || st.Uid != s.uid || st.Gid != s.gid || st.Mode&0022 != 0 {
		syscall.Close(fd)
		return nil, errCustody
	}
	for i, name := range []string{"var", "lib", "happyranch-remote-access", "home-ca"} {
		mode := uint32(0755)
		if i >= 2 {
			mode = 0700
		}
		next, openErr := syscall.Openat(fd, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if openErr == syscall.ENOENT && create && i >= 2 {
			mkdirErr := syscall.Mkdirat(fd, name, mode)
			if mkdirErr != nil && mkdirErr != syscall.EEXIST {
				syscall.Close(fd)
				return nil, errIO
			}
			next, openErr = syscall.Openat(fd, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
			if openErr == nil && mkdirErr == nil {
				if syscall.Fchown(next, int(s.uid), int(s.gid)) != nil || syscall.Fchmod(next, mode) != nil || syscall.Fsync(next) != nil || syscall.Fsync(fd) != nil {
					syscall.Close(next)
					syscall.Close(fd)
					return nil, errIO
				}
			}
		}
		syscall.Close(fd)
		if openErr != nil {
			return nil, errCustody
		}
		fd = next
		if err := directory(fd, mode, s); err != nil {
			syscall.Close(fd)
			return nil, err
		}
	}
	if syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		syscall.Close(fd)
		return nil, errBusy
	}
	return os.NewFile(uintptr(fd), custodyPath), nil
}

var names = [...]string{"ca.pem", "ca.key", "leaf.pem", "leaf.key"}

// inventory opens every leaf no-follow/nonblocking before inspecting it, so
// special files cannot block a root reader. Status validates key metadata only.
func inventory(dir *os.File, s system, readKeys bool) (map[string][]byte, error) {
	entries, err := dir.Readdirnames(len(names) + 1)
	if err != nil && err != io.EOF {
		return nil, errIO
	}
	files := make(map[string][]byte)
	for _, name := range entries {
		known := false
		for _, allowed := range names {
			known = known || name == allowed
		}
		if !known || len(entries) > len(names) {
			return nil, errEntries
		}
		fd, err := syscall.Openat(int(dir.Fd()), name, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
		if err != nil {
			return nil, errCustody
		}
		f := os.NewFile(uintptr(fd), name)
		var before, after syscall.Stat_t
		if syscall.Fstat(fd, &before) != nil || before.Mode != syscall.S_IFREG|0600 || before.Uid != s.uid || before.Gid != s.gid || before.Nlink != 1 || before.Size <= 0 || before.Size > maxFileBytes {
			f.Close()
			return nil, errCustody
		}
		var data []byte
		if readKeys || name == "ca.pem" || name == "leaf.pem" {
			data, err = io.ReadAll(io.LimitReader(f, maxFileBytes+1))
		}
		statErr := syscall.Fstat(fd, &after)
		f.Close()
		before.Atim = after.Atim // reads may legitimately update access time
		if err != nil || statErr != nil || before != after || len(data) > maxFileBytes {
			return nil, errCustody
		}
		files[name] = data
	}
	return files, nil
}

func decode(data []byte, kind string) ([]byte, error) {
	b, rest := pem.Decode(data)
	if b == nil || b.Type != kind || len(b.Headers) != 0 || len(rest) != 0 || !bytes.Equal(pem.EncodeToMemory(b), data) || len(b.Bytes) > maxFileBytes {
		return nil, errCustody
	}
	return b.Bytes, nil
}

func materialize(verb string, s system) (status, error) {
	dir, err := openCustody(s, verb != "status")
	if err != nil {
		return status{}, err
	}
	defer dir.Close() // also releases the directory flock
	files, err := inventory(dir, s, verb != "status")
	if err != nil {
		return status{}, err
	}
	_, caPresent := files["ca.pem"]
	_, caKeyPresent := files["ca.key"]
	_, leafPresent := files["leaf.pem"]
	_, leafKeyPresent := files["leaf.key"]
	if caPresent != caKeyPresent || leafPresent != leafKeyPresent || (!caPresent && leafPresent) {
		return status{}, errIncomplete
	}
	if !caPresent && verb != "ensure" {
		return status{}, errIncomplete
	}
	var caDER, leafDER []byte
	var caKey homeca.PrivateKey
	if caPresent {
		caDER, err = decode(files["ca.pem"], "CERTIFICATE")
		if err != nil {
			return status{}, errCA
		}
		// Check admits the CA before inspecting a leaf. ErrLeaf for nil means
		// that exact CA passed all of the core's signature/profile/time checks.
		if !errors.Is(homeca.Check(caDER, nil, s.now), homeca.ErrLeaf) {
			return status{}, errCA
		}
		if verb != "status" {
			keyDER, err := decode(files["ca.key"], "PRIVATE KEY")
			if err != nil {
				return status{}, errKey
			}
			caKey, err = homeca.ImportPrivateKey(keyDER)
			if err != nil {
				return status{}, errKey
			}
			if _, err := tls.X509KeyPair(files["ca.pem"], files["ca.key"]); err != nil {
				return status{}, errKey
			}
		}
	} else {
		caDER, caKey, err = homeca.NewCA(s.now, rand.Reader)
		if err != nil {
			return status{}, errGeneration
		}
	}
	ca, _ := x509.ParseCertificate(caDER) // generated or admitted by homeca
	if verb != "status" && ca.NotAfter.Before(s.now.Add(homeca.MinimumLeafLifetime)) {
		return status{}, errLifetime
	}
	if leafPresent {
		leafDER, err = decode(files["leaf.pem"], "CERTIFICATE")
		if err != nil {
			return status{}, errLeaf
		}
		if _, err := x509.ParseCertificate(leafDER); err != nil {
			return status{}, errLeaf
		}
		if verb != "status" {
			if _, err := tls.X509KeyPair(files["leaf.pem"], files["leaf.key"]); err != nil {
				return status{}, errKey
			}
		}
	}
	due := !leafPresent || homeca.LeafRenewalDue(leafDER, caDER, s.now)
	if verb != "status" && (due || verb == "renew-leaf") {
		var leafKey homeca.PrivateKey
		leafDER, leafKey, err = homeca.IssueLeaf(caDER, caKey, s.now, rand.Reader)
		if err != nil {
			return status{}, errGeneration
		}
		if homeca.Check(caDER, leafDER, s.now) != nil {
			return status{}, errGeneration
		}
		// Generate and validate everything before any publication. Key-first
		// with a directory fsync between members never publishes a usable new
		// certificate before its key. Half/mismatched pairs refuse on recovery.
		if !caPresent {
			if err := publishPair(dir, "ca", caDER, caKey, s); err != nil {
				return status{}, err
			}
		}
		if err := publishPair(dir, "leaf", leafDER, leafKey, s); err != nil {
			return status{}, err
		}
		due = homeca.LeafRenewalDue(leafDER, caDER, s.now)
	}
	result := status{CAFingerprint: homeca.Fingerprint(caDER), CANotAfter: ca.NotAfter.UTC().Format(time.RFC3339), RenewalDue: due}
	if len(leafDER) != 0 {
		leaf, _ := x509.ParseCertificate(leafDER)
		result.LeafNotAfter = leaf.NotAfter.UTC().Format(time.RFC3339)
	}
	return result, nil
}

func publishPair(dir *os.File, name string, cert []byte, key homeca.PrivateKey, s system) error {
	if err := atomicWrite(dir, name+".key", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: key.DER()}), s); err != nil {
		return err
	}
	return atomicWrite(dir, name+".pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert}), s)
}

func atomicWrite(dir *os.File, name string, data []byte, s system) error {
	fd := int(dir.Fd())
	pending := "." + name + ".pending"
	tmp, err := syscall.Openat(fd, pending, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return errIO
	}
	f := os.NewFile(uintptr(tmp), pending)
	defer f.Close()
	// Failure/crash residue is intentionally preserved, never auto-discarded.
	if len(data) > maxFileBytes || syscall.Fchown(tmp, int(s.uid), int(s.gid)) != nil || syscall.Fchmod(tmp, 0600) != nil {
		return errIO
	}
	if n, err := f.Write(data); err != nil || n != len(data) {
		return errIO
	}
	if f.Sync() != nil || syscall.Renameat(fd, pending, fd, name) != nil || dir.Sync() != nil {
		return errIO
	}
	return nil
}
