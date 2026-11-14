package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"golang.org/x/sys/unix"
)

var errManagedState = errors.New("managed state refused")
var profileUUID = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

type profileRecord struct {
	ControlURL string `json:"controlURL"`
	Hostname   string `json:"hostname"`
	UUID       string `json:"uuid"`
	Phase      string `json:"phase"`
}
type managedProfile struct {
	path      string
	dir, lock *os.File
	record    profileRecord
}

// Walk every literal component without following links. Only missing components
// are created; existing permissions are never widened or repaired.
func openProfileDir(path string, create bool) (*os.File, error) {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path || filepath.Base(filepath.Dir(path)) != "ManagedTailnet" || !profileUUID.MatchString(filepath.Base(path)) {
		return nil, errManagedState
	}
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for i, part := range parts {
		next, e := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if e == unix.ENOENT && create {
			if e = unix.Mkdirat(fd, part, 0700); e == nil {
				e = unix.Fsync(fd)
			}
			if e == nil {
				next, e = unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
			}
		}
		unix.Close(fd)
		if e != nil {
			return nil, e
		}
		fd = next
		if i >= len(parts)-2 {
			var st unix.Stat_t
			if unix.Fstat(fd, &st) != nil || st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0700 {
				unix.Close(fd)
				return nil, errManagedState
			}
		}
	}
	return os.NewFile(uintptr(fd), path), nil
}
func privateFile(dir *os.File, name string, flags int) (*os.File, error) {
	fd, err := unix.Openat(int(dir.Fd()), name, flags|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0600)
	if err != nil {
		return nil, err
	}
	var st unix.Stat_t
	if unix.Fstat(fd, &st) != nil || st.Mode&unix.S_IFMT != unix.S_IFREG || st.Uid != uint32(os.Geteuid()) || st.Nlink != 1 || st.Mode&07777 != 0600 {
		unix.Close(fd)
		return nil, errManagedState
	}
	return os.NewFile(uintptr(fd), name), nil
}
func openManagedProfile(path, hostname, control string, fresh bool) (*managedProfile, error) {
	dir, err := openProfileDir(path, fresh)
	if err != nil {
		return nil, err
	}
	p := &managedProfile{path: path, dir: dir}
	success := false
	defer func() {
		if !success {
			p.close()
		}
	}()
	p.lock, err = privateFile(dir, ".lock", unix.O_RDWR|unix.O_CREAT)
	if err != nil {
		return nil, err
	}
	if err = unix.Flock(int(p.lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return nil, err
	}
	if err = p.validate(); err != nil {
		return nil, err
	}
	p.record = profileRecord{ControlURL: control, Hostname: hostname, UUID: filepath.Base(path), Phase: "draft"}
	if fresh {
		names, err := os.ReadDir(path)
		if err != nil {
			return nil, err
		}
		for _, entry := range names {
			if entry.Name() != ".lock" {
				return nil, errManagedState
			}
		}
		if err = p.phase("draft"); err != nil {
			return nil, err
		}
	} else {
		raw, err := p.read("profile.json")
		if err != nil {
			return nil, err
		}
		expected := p.record
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		if dec.Decode(&p.record) != nil || dec.Decode(new(any)) != io.EOF || p.record.ControlURL != expected.ControlURL || p.record.Hostname != expected.Hostname || p.record.UUID != expected.UUID {
			return nil, errManagedState
		}
		if p.record.Phase != "joined" && p.record.Phase != "submitting" {
			return nil, errManagedState
		}
		state, err := p.read("tailscaled.state")
		if err != nil || len(bytes.TrimSpace(state)) == 0 {
			return nil, errManagedState
		}
		marker, err := p.read("consumed")
		if err == nil {
			if string(marker) != p.marker() {
				return nil, errManagedState
			}
		} else if !errors.Is(err, unix.ENOENT) || p.record.Phase != "submitting" {
			return nil, errManagedState
		}
	}
	success = true
	return p, nil
}
func (p *managedProfile) close() error {
	var closeErr error
	if p.lock != nil {
		closeErr = p.lock.Close()
		p.lock = nil
	}
	if p.dir != nil {
		closeErr = errors.Join(closeErr, p.dir.Close())
		p.dir = nil
	}
	return closeErr
}
func (p *managedProfile) read(name string) ([]byte, error) {
	f, err := privateFile(p.dir, name, unix.O_RDONLY)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil || len(b) > 65536 {
		return nil, errManagedState
	}
	return b, nil
}
func (p *managedProfile) validate() error {
	dir, err := openProfileDir(p.path, false)
	if err != nil {
		return err
	}
	defer dir.Close()
	a, err := p.dir.Stat()
	if err != nil {
		return err
	}
	b, err := dir.Stat()
	if err != nil || !os.SameFile(a, b) {
		return errManagedState
	}
	return filepath.WalkDir(p.path, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		// Use native stat for stable cross-platform uid/mode/link fields.
		var native unix.Stat_t
		if unix.Lstat(path, &native) != nil || native.Uid != uint32(os.Geteuid()) {
			return errManagedState
		}
		if info.IsDir() {
			if native.Mode&07777 != 0700 || info.Mode()&os.ModeSymlink != 0 {
				return errManagedState
			}
		} else if !info.Mode().IsRegular() || native.Mode&07777 != 0600 || native.Nlink != 1 {
			return errManagedState
		}
		return nil
	})
}
func (p *managedProfile) atomic(name string, data []byte) error {
	// A stale pending file gates recovery; never overwrite an unknown writer.
	f, err := privateFile(p.dir, name+".pending", unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	// Refuse unsafe existing destinations before descriptor-relative replacement.
	old, err := privateFile(p.dir, name, unix.O_RDONLY)
	if err == nil {
		_ = old.Close()
	} else if !errors.Is(err, unix.ENOENT) {
		return err
	}
	if err = unix.Renameat(int(p.dir.Fd()), name+".pending", int(p.dir.Fd()), name); err != nil {
		return err
	}
	return unix.Fsync(int(p.dir.Fd()))
}
func (p *managedProfile) phase(phase string) error {
	record := p.record
	record.Phase = phase
	b, err := json.Marshal(record)
	if err != nil {
		return err
	}
	if err = p.atomic("profile.json", append(b, '\n')); err != nil {
		return err
	}
	p.record = record
	return nil
}
func (p *managedProfile) marker() string {
	sum := sha256.Sum256([]byte(p.record.Hostname + "\x00" + p.record.ControlURL))
	return hex.EncodeToString(sum[:]) + "\n"
}
func (p *managedProfile) commitConsumed() error {
	state, err := p.read("tailscaled.state")
	if err != nil || len(bytes.TrimSpace(state)) == 0 {
		return errManagedState
	}
	// Existing markers must match and are never silently repaired.
	b, err := p.read("consumed")
	if err == nil {
		if string(b) != p.marker() {
			return errManagedState
		}
	} else if errors.Is(err, unix.ENOENT) {
		if err = p.atomic("consumed", []byte(p.marker())); err != nil {
			return err
		}
	} else {
		return err
	}
	if err = p.phase("joined"); err != nil {
		return errManagedState
	}
	return nil
}
