package main

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"os/user"
	"reflect"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
	sidecar "happyranch/linux-tsnet-sidecar"
)

// This foreground boundary reads only the existing public config pair. Protected
// receipts, transactions and provenance remain exclusively with the root owner.
func admitHomeForeground(path string, cfg sidecar.Config) error {
	if cfg.Profile != sidecar.LinuxIdentityProfile {
		return nil
	}
	account, err := user.Lookup("happyranch")
	if err != nil || account.Uid == "0" || account.Uid != strconv.Itoa(os.Geteuid()) || path != "/etc/happyranch/sidecar.json" || cfg.HomeBinding == nil {
		return sidecar.ErrConfiguration
	}
	sc, err := readHomeConfig(path)
	if err != nil {
		return sidecar.ErrConfiguration
	}
	connector, err := readHomeConfig("/etc/happyranch/connector.json")
	if err != nil {
		return sidecar.ErrConfiguration
	}
	// Reparse the custodied bytes, never trust the earlier path-based loader's
	// snapshot across a root replacement. Enrollment handling remains unchanged.
	var current sidecar.Config
	if json.Unmarshal(sc, &current) != nil {
		return sidecar.ErrConfiguration
	}
	current.CredentialFile = cfg.CredentialFile
	if !reflect.DeepEqual(current, cfg) || !homePairMatches(sc, connector, cfg) {
		return sidecar.ErrConfiguration
	}
	return nil
}

func homePairMatches(sc, connector []byte, cfg sidecar.Config) bool {
	var s, c map[string]json.RawMessage
	if !uniqueHomeJSON(sc) || !uniqueHomeJSON(connector) || json.Unmarshal(sc, &s) != nil || json.Unmarshal(connector, &c) != nil {
		return false
	}
	keys := []string{"Profile", "StateDir", "RoleIdentity", "DERPPolicy", "ListenAddr", "ExcludedDaemonPorts", "ControlURL", "control_tls", "home_binding"}
	if len(s) != len(keys) || cfg.HomeBinding == nil || cfg.ControlTLS == nil || cfg.Validate() != nil || cfg.StateDir != "/var/lib/happyranch-tsnet-sidecar" || cfg.ListenAddr != ":443" || !reflect.DeepEqual(cfg.ExcludedDaemonPorts, []int{8765}) {
		return false
	}
	for _, k := range keys {
		if _, ok := s[k]; !ok {
			return false
		}
	}
	var home, id, control, profile string
	var managed map[string]json.RawMessage
	if json.Unmarshal(c["home_id"], &home) != nil || json.Unmarshal(c["connector_id"], &id) != nil || json.Unmarshal(c["managed"], &managed) != nil || json.Unmarshal(managed["control_plane_id"], &control) != nil || json.Unmarshal(managed["profile"], &profile) != nil {
		return false
	}
	b := cfg.HomeBinding
	return profile == sidecar.LinuxIdentityProfile && home == b.HomeID && id == b.ConnectorID && control == b.ControlPlaneID && cfg.RoleIdentity == "home-sidecar-"+home
}

func uniqueHomeJSON(data []byte) bool {
	d := json.NewDecoder(bytes.NewReader(data))
	var value func(int) bool
	value = func(depth int) bool {
		if depth > 32 {
			return false
		}
		t, err := d.Token()
		if err != nil {
			return false
		}
		delim, composite := t.(json.Delim)
		if !composite {
			return true
		}
		seen := map[string]bool{}
		for d.More() {
			if delim == '{' {
				key, err := d.Token()
				k, ok := key.(string)
				if err != nil || !ok || seen[k] {
					return false
				}
				seen[k] = true
			}
			if !value(depth + 1) {
				return false
			}
		}
		_, err = d.Token()
		return err == nil
	}
	if !value(0) {
		return false
	}
	_, err := d.Token()
	return err == io.EOF
}

func readHomeConfig(path string) ([]byte, error) {
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, sidecar.ErrConfiguration
	}
	type pin struct {
		parent, child int
		name          string
		before        unix.Stat_t
	}
	var pins []pin
	opened := []int{fd}
	defer func() {
		for _, f := range opened {
			unix.Close(f)
		}
	}()
	for _, part := range parts[:len(parts)-1] {
		var st, named, current unix.Stat_t
		if unix.Fstat(fd, &st) != nil || st.Uid != 0 || st.Mode&0o022 != 0 || unix.Fstatat(fd, part, &named, unix.AT_SYMLINK_NOFOLLOW) != nil {
			return nil, sidecar.ErrConfiguration
		}
		next, err := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if err != nil {
			return nil, sidecar.ErrConfiguration
		}
		opened = append(opened, next)
		if unix.Fstat(next, &current) != nil || !sameHomeConfigMetadata(named, current) {
			return nil, sidecar.ErrConfiguration
		}
		pins = append(pins, pin{fd, next, part, current})
		fd = next
	}
	var parent unix.Stat_t
	if unix.Fstat(fd, &parent) != nil || parent.Uid != 0 || parent.Mode&0o022 != 0 {
		return nil, sidecar.ErrConfiguration
	}
	name := parts[len(parts)-1]
	leaf, err := unix.Openat(fd, name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, sidecar.ErrConfiguration
	}
	f := os.NewFile(uintptr(leaf), name)
	defer f.Close()
	var before, after, named unix.Stat_t
	if unix.Fstat(leaf, &before) != nil || before.Mode&unix.S_IFMT != unix.S_IFREG || before.Uid != 0 || before.Nlink != 1 || before.Mode&0o022 != 0 || before.Size < 1 || before.Size > 65536 {
		return nil, sidecar.ErrConfiguration
	}
	data, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil || len(data) != int(before.Size) || unix.Fstat(leaf, &after) != nil || unix.Fstatat(fd, name, &named, unix.AT_SYMLINK_NOFOLLOW) != nil || !sameHomeConfigMetadata(before, after) || !sameHomeConfigMetadata(after, named) {
		return nil, sidecar.ErrConfiguration
	}
	for _, p := range pins {
		var named, current unix.Stat_t
		if unix.Fstatat(p.parent, p.name, &named, unix.AT_SYMLINK_NOFOLLOW) != nil || unix.Fstat(p.child, &current) != nil || !sameHomeConfigMetadata(p.before, named) || !sameHomeConfigMetadata(named, current) {
			return nil, sidecar.ErrConfiguration
		}
	}
	return data, nil
}

func sameHomeConfigMetadata(a, b unix.Stat_t) bool {
	return a.Dev == b.Dev && a.Ino == b.Ino && a.Mode == b.Mode && a.Uid == b.Uid && a.Gid == b.Gid && a.Nlink == b.Nlink && a.Size == b.Size && a.Mtim == b.Mtim && a.Ctim == b.Ctim
}
