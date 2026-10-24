//go:build linux

package sidecar

// The managed lifecycle is a local Linux contract. These readers do not grant
// trust, credentials or permissions. Runtime writers own only their directory;
// the explicit installed helper is the only writer of the paired policy.
import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"golang.org/x/sys/unix"
	"tailscale.com/ipn"
	"tailscale.com/types/key"
)

const (
	ManagedMainDirectory          = "/var/lib/happyranch-connector"
	ManagedSidecarDirectory       = "/var/lib/happyranch-tsnet-sidecar"
	managedRecord                 = "lifecycle.json"
	managedLock                   = "lifecycle.lock"
	ManagedWindowNS         int64 = 600 * int64(time.Second)
	ManagedLeaseNS          int64 = 12 * int64(time.Second)
)

var ErrLifecycle = errors.New("managed_lifecycle_refused")
var ErrRateDeferred = errors.New("managed_lifecycle_deferred")

// These causes are emitted ONLY by observed record/identity boundaries. An
// unavailable manager/preflight, deadline or cancellation has no typed cause.
var errLifecycleIO = fmt.Errorf("%w: io_failure", ErrLifecycle)
var errLifecycleInvalid = fmt.Errorf("%w: invalid_state", ErrLifecycle)
var errLifecycleIdentity = fmt.Errorf("%w: identity_loss", ErrLifecycle)
var errLifecycleInhibited = errors.New("managed_lifecycle_inhibited")

func LifecycleFailureCause(err error) string {
	switch {
	case errors.Is(err, errLifecycleIO):
		return "io_failure"
	case errors.Is(err, errLifecycleInvalid):
		return "invalid_state"
	case errors.Is(err, errLifecycleIdentity):
		return "identity_loss"
	}
	return "none"
}

type LifecyclePolicy struct {
	InstallationID string `json:"installation_id"`
	Revision       int64  `json:"revision"`
	OperationID    string `json:"operation_id"`
	Enabled        bool   `json:"enabled"`
	Desired        string `json:"desired"`
	HardBlock      string `json:"hard_block"`
}
type LifecycleProcess struct {
	PID        int    `json:"pid"`
	StartTicks int64  `json:"start_ticks"`
	Invocation string `json:"invocation"`
}
type LifecycleHealth struct {
	Sequence     uint64 `json:"sequence"`
	ValidUntilNS int64  `json:"valid_until_ns"`
}
type LifecycleTeardown struct {
	BeganNS    int64 `json:"began_ns"`
	DeadlineNS int64 `json:"deadline_ns"`
}
type LifecycleRate struct {
	Boot        string  `json:"boot"`
	Spends      []int64 `json:"spends"`
	LastEndNS   *int64  `json:"last_end_ns"`
	NotBeforeNS *int64  `json:"not_before_ns"`
}
type LifecycleBootSource struct {
	OldBoot      string `json:"old_boot"`
	TransitionID string `json:"transition_id"`
}
type LifecycleMain struct {
	Version          int                  `json:"version"`
	Policy           LifecyclePolicy      `json:"policy"`
	RuntimeBoot      string               `json:"runtime_boot"`
	BootTransitionID string               `json:"boot_transition_id"`
	Phase            string               `json:"phase"`
	Generation       *string              `json:"generation"`
	Wrapper          *LifecycleProcess    `json:"wrapper"`
	Launcher         *LifecycleProcess    `json:"launcher"`
	Worker           *LifecycleProcess    `json:"worker"`
	Sidecar          *LifecycleProcess    `json:"sidecar"`
	Health           *LifecycleHealth     `json:"health"`
	Teardown         *LifecycleTeardown   `json:"teardown"`
	Clean            bool                 `json:"clean"`
	Block            string               `json:"block"`
	Rate             LifecycleRate        `json:"rate"`
	BootSource       *LifecycleBootSource `json:"boot_source"`
}
type LifecycleSidecar struct {
	Version          int                  `json:"version"`
	Policy           LifecyclePolicy      `json:"policy"`
	RuntimeBoot      string               `json:"runtime_boot"`
	BootTransitionID string               `json:"boot_transition_id"`
	Halt             bool                 `json:"halt"`
	Binding          *LifecycleProcess    `json:"binding"`
	Generation       *string              `json:"generation"`
	Outcome          string               `json:"outcome"`
	Failure          string               `json:"failure"`
	CloseProof       string               `json:"close_proof"`
	TerminalNS       *int64               `json:"terminal_ns"`
	BootSource       *LifecycleBootSource `json:"boot_source"`
}

func lifecycleID(s string) bool {
	if len(s) != 32 || s == strings.Repeat("0", 32) {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
func NewLifecycleID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", ErrLifecycle
	}
	s := hex.EncodeToString(b[:])
	if !lifecycleID(s) {
		return "", ErrLifecycle
	}
	return s, nil
}
func LifecycleNow() (int64, error) {
	var t unix.Timespec
	if unix.ClockGettime(unix.CLOCK_MONOTONIC, &t) != nil {
		return 0, ErrLifecycle
	}
	n := t.Nano()
	if n <= 0 {
		return 0, ErrLifecycle
	}
	return n, nil
}
func LifecycleBoot() (string, error) {
	f, err := os.Open("/proc/sys/kernel/random/boot_id")
	if err != nil {
		return "", ErrLifecycle
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 38))
	if err != nil || len(raw) != 37 || raw[36] != '\n' {
		return "", ErrLifecycle
	}
	s := strings.ReplaceAll(string(raw[:36]), "-", "")
	if !lifecycleID(s) {
		return "", ErrLifecycle
	}
	return s, nil
}
func lifecycleOneOf(s string, values ...string) bool {
	for _, v := range values {
		if s == v {
			return true
		}
	}
	return false
}
func lifecyclePolicyValid(p LifecyclePolicy) bool {
	return lifecycleID(p.InstallationID) && lifecycleID(p.OperationID) && p.Revision > 0 &&
		lifecycleOneOf(p.Desired, "run", "stopped", "retired") &&
		lifecycleOneOf(p.HardBlock, "none", "io_failure", "invalid_state", "identity_loss", "transaction_unknown") &&
		(p.Desired != "retired" || !p.Enabled)
}

// Only root's paired live enable writer can advance run policy while a
// generation survives. Stop/disable/restart inhibit first; returning to run
// requires stopped absence and therefore cannot retain this live generation.
// Callers MUST also prove the same boot/transition/generation and current pair.
func LifecycleRunPolicyFollows(admission, current LifecyclePolicy) bool {
	if current == admission {
		return true
	}
	return lifecyclePolicyValid(admission) && lifecyclePolicyValid(current) &&
		current.InstallationID == admission.InstallationID && admission.Desired == "run" && current.Desired == "run" &&
		admission.HardBlock == "none" && current.HardBlock == "none" && current.Enabled &&
		current.Revision > admission.Revision && current.OperationID != admission.OperationID
}
func lifecycleProcessValid(p *LifecycleProcess) bool {
	return p == nil || p.PID >= 2 && p.PID <= math.MaxInt32 && p.StartTicks > 0 && lifecycleID(p.Invocation)
}
func lifecycleOptionalID(p *string) bool { return p == nil || lifecycleID(*p) }
func lifecycleSourceValid(s *LifecycleBootSource, boot, transition string) bool {
	return s == nil || lifecycleID(s.OldBoot) && s.OldBoot != boot && lifecycleID(s.TransitionID) && s.TransitionID == transition
}
func lifecycleMainValid(m *LifecycleMain) bool {
	if m.Version != 2 || !lifecyclePolicyValid(m.Policy) || !lifecycleID(m.RuntimeBoot) || !lifecycleID(m.BootTransitionID) ||
		!lifecycleOptionalID(m.Generation) || !lifecycleOneOf(m.Phase, "boot_prepared", "idle", "live", "retiring", "blocked") ||
		!lifecycleOneOf(m.Block, "none", "unknown_cleanup", "join_failed", "manager_refused", "io_failure", "invalid_state", "identity_loss") ||
		!lifecycleSourceValid(m.BootSource, m.RuntimeBoot, m.BootTransitionID) {
		return false
	}
	for _, p := range []*LifecycleProcess{m.Wrapper, m.Launcher, m.Worker, m.Sidecar} {
		if !lifecycleProcessValid(p) {
			return false
		}
	}
	if m.Rate.Boot != m.RuntimeBoot || m.Rate.Spends == nil || len(m.Rate.Spends) > 3 {
		return false
	}
	var prev int64
	for _, n := range m.Rate.Spends {
		if n <= 0 || n < prev {
			return false
		}
		prev = n
	}
	if (m.Rate.LastEndNS == nil) != (m.Rate.NotBeforeNS == nil) {
		return false
	}
	if m.Rate.LastEndNS != nil && (*m.Rate.LastEndNS <= 0 || *m.Rate.LastEndNS > math.MaxInt64-int64(120*time.Second) || *m.Rate.NotBeforeNS < *m.Rate.LastEndNS ||
		*m.Rate.NotBeforeNS-*m.Rate.LastEndNS > int64(120*time.Second)) {
		return false
	}
	if m.Health != nil && (m.Health.Sequence == 0 || m.Health.ValidUntilNS <= 0 || m.Phase != "live" || m.Worker == nil) {
		return false
	}
	if m.Teardown != nil && (m.Teardown.BeganNS <= 0 || m.Teardown.BeganNS > math.MaxInt64-int64(30*time.Second) || m.Teardown.DeadlineNS != m.Teardown.BeganNS+int64(30*time.Second) || m.Phase != "retiring") {
		return false
	}
	if m.Clean && (m.Phase != "idle" || m.Generation != nil || m.Wrapper != nil || m.Launcher != nil || m.Worker != nil || m.Sidecar != nil || m.Health != nil || m.Teardown != nil || m.Block != "none") {
		return false
	}
	if m.Phase == "idle" && !m.Clean {
		return false
	}
	if m.Phase == "boot_prepared" && (m.Clean || m.Generation != nil || m.Launcher != nil || m.Worker != nil || m.Sidecar != nil || m.Health != nil || m.Teardown != nil || m.Block != "none" || m.BootSource == nil || m.Wrapper == nil) {
		return false
	}
	if m.Phase == "blocked" && (m.Clean || m.Block == "none" || m.Health != nil) {
		return false
	}
	if m.Phase == "live" && (m.Clean || m.Generation == nil || m.Wrapper == nil || m.Block != "none") {
		return false
	}
	if m.Phase == "retiring" && (m.Clean || m.Generation == nil || m.Wrapper == nil || m.Health != nil || m.Teardown == nil) {
		return false
	}
	for _, p := range []*LifecycleProcess{m.Launcher, m.Worker} {
		if p != nil && (m.Wrapper == nil || p.Invocation != m.Wrapper.Invocation) {
			return false
		}
	}
	if m.Worker != nil && m.Launcher == nil || m.Teardown != nil && m.Generation == nil || m.Generation == nil && (m.Launcher != nil || m.Worker != nil || m.Sidecar != nil) {
		return false
	}
	if m.Health != nil && (m.Generation == nil || m.Launcher == nil || m.Sidecar == nil || m.Health.ValidUntilNS <= ManagedLeaseNS) {
		return false
	}
	roles := []*LifecycleProcess{m.Wrapper, m.Launcher, m.Worker, m.Sidecar}
	for i, a := range roles {
		for j, b := range roles {
			if i < j && a != nil && b != nil && (a.PID == b.PID || a.Invocation == b.Invocation && j == 3) {
				if !(i == 1 && j == 2 && *a == *b) {
					return false
				}
			}
		}
	}
	if m.Rate.LastEndNS != nil {
		d := *m.Rate.NotBeforeNS - *m.Rate.LastEndNS
		if d != int64(30*time.Second) && d != int64(60*time.Second) && d != int64(120*time.Second) {
			return false
		}
	}
	for i, n := range m.Rate.Spends {
		if i > 0 && n == m.Rate.Spends[i-1] &&
			(m.BootSource == nil || n != m.Rate.Spends[0] || m.Rate.LastEndNS == nil || n > *m.Rate.LastEndNS) {
			return false
		}
	}
	return true
}
func lifecycleSidecarValid(s *LifecycleSidecar) bool {
	if s.Version != 2 || !lifecyclePolicyValid(s.Policy) || !lifecycleID(s.RuntimeBoot) || !lifecycleID(s.BootTransitionID) ||
		!lifecycleOptionalID(s.Generation) || !lifecycleProcessValid(s.Binding) ||
		!lifecycleOneOf(s.Outcome, "unstarted", "waiting", "admitted", "closed", "unknown") ||
		!lifecycleOneOf(s.Failure, "none", "io_failure", "invalid_state", "identity_loss") ||
		!lifecycleOneOf(s.CloseProof, "none", "cooperative", "kernel_absence") ||
		!lifecycleSourceValid(s.BootSource, s.RuntimeBoot, s.BootTransitionID) || s.TerminalNS != nil && *s.TerminalNS <= 0 {
		return false
	}
	if s.Outcome == "admitted" && (s.Generation == nil || s.Binding == nil || s.TerminalNS != nil || s.CloseProof != "none") {
		return false
	}
	if s.Outcome == "closed" && (s.CloseProof == "none" || s.TerminalNS == nil) {
		return false
	}
	if s.Outcome != "closed" && s.CloseProof != "none" {
		return false
	}
	if s.Outcome == "unstarted" && (s.Binding != nil || s.Generation != nil || s.TerminalNS != nil) {
		return false
	}
	if s.Outcome == "waiting" && (s.Binding == nil || s.TerminalNS != nil || s.CloseProof != "none") {
		return false
	}
	if s.Generation != nil && s.Binding == nil || s.TerminalNS != nil && !lifecycleOneOf(s.Outcome, "closed", "unknown") {
		return false
	}
	if lifecycleOneOf(s.Outcome, "closed", "unknown") && (s.Binding == nil || s.TerminalNS == nil) {
		return false
	}
	if s.Failure != "none" && (s.Outcome != "unknown" || s.CloseProof != "none") {
		return false
	}
	return true
}

// Validate current record relationships without mistaking a stale lease for
// fresh health. Expiry is a consumer predicate; future clocks are invalid.
// Old runtime clocks are range-checked by the independent record validators,
// never compared with this boot's monotonic clock.
func (p *LifecyclePair) currentRecords(boot string, now int64) bool {
	m, s := &p.Main, &p.Sidecar
	if now <= 0 || !lifecycleMainValid(m) || !lifecycleSidecarValid(s) || m.Policy != s.Policy || m.RuntimeBoot != boot || s.RuntimeBoot != boot ||
		m.BootTransitionID != s.BootTransitionID || (m.BootSource == nil) != (s.BootSource == nil) {
		return false
	}
	if m.BootSource != nil && *m.BootSource != *s.BootSource {
		return false
	}
	for _, n := range m.Rate.Spends {
		if n > now {
			return false
		}
	}
	if m.Rate.LastEndNS != nil && *m.Rate.LastEndNS > now || m.Teardown != nil && m.Teardown.BeganNS > now ||
		m.Health != nil && (m.Health.ValidUntilNS > now && m.Health.ValidUntilNS-now > ManagedLeaseNS) || s.TerminalNS != nil && *s.TerminalNS > now {
		return false
	}
	// RetireClean may follow exact kernel peer absence without a sidecar
	// writer: its old admitted generation is historical here. A new waiter
	// still requires independently proven departure and the full entry census.
	if m.Clean {
		return s.Outcome != "waiting" || s.Generation == nil
	}
	if m.Phase == "boot_prepared" {
		return s.Outcome == "waiting" && s.Generation == nil && !s.Halt && s.Failure == "none"
	}
	if m.Generation != nil {
		if m.Phase == "live" && s.Generation != nil && m.Health == nil && m.Policy.Desired == "run" && !s.Halt && lifecycleOneOf(s.Outcome, "waiting", "admitted") {
			return false
		}
		if m.Sidecar == nil || s.Binding == nil || *m.Sidecar != *s.Binding || s.Generation != nil && *m.Generation != *s.Generation {
			return false
		}
		if s.Outcome == "unstarted" || s.Outcome == "admitted" && s.Generation == nil {
			return false
		}
	}
	return true
}

// A locked pair is short-lived. Never keep it across a manager job, Accept,
// Wait, scanner join, or handler progress. Same-UID hostile rewriting is outside
// the contract; ordinary inode/type/owner/link/mode drift still refuses.
type LifecyclePair struct {
	Main     LifecycleMain
	Sidecar  LifecycleSidecar
	lock     *os.File
	mainDir  *os.File
	sideDir  *os.File
	deadline time.Time
}

func lifecycleDirectory(path string) (*os.File, error) {
	// Check each component without following links, then retain the final fd.
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, errLifecycleIO
	}
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for index, part := range parts {
		next, e := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		unix.Close(fd)
		if e != nil {
			if e == unix.ENOENT || e == unix.ENOTDIR || e == unix.ELOOP {
				return nil, errLifecycleInvalid
			}
			return nil, errLifecycleIO
		}
		fd = next
		var st unix.Stat_t
		finalState := index == len(parts)-1 && lifecycleOneOf(path, ManagedMainDirectory, ManagedSidecarDirectory)
		if unix.Fstat(fd, &st) != nil {
			unix.Close(fd)
			return nil, errLifecycleIO
		}
		if st.Mode&0022 != 0 || finalState && (st.Uid != lifecycleServiceUID() || st.Mode&07777 != 0700) || !finalState && st.Uid != 0 {
			unix.Close(fd)
			return nil, errLifecycleInvalid
		}
	}
	return os.NewFile(uintptr(fd), path), nil
}
func lifecycleReadAt(dir *os.File, name string, dst any) error {
	var parent unix.Stat_t
	if unix.Fstat(int(dir.Fd()), &parent) != nil {
		return errLifecycleIO
	}
	fd, err := unix.Openat(int(dir.Fd()), name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		if err == unix.ENOENT || err == unix.ELOOP || err == unix.ENOTDIR {
			return errLifecycleInvalid
		}
		return errLifecycleIO
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	var st unix.Stat_t
	if unix.Fstat(fd, &st) != nil {
		return errLifecycleIO
	}
	if st.Mode&unix.S_IFMT != unix.S_IFREG || st.Mode&07777 != 0600 || st.Uid != parent.Uid || st.Nlink != 1 || st.Size <= 0 || st.Size > 4096 {
		return errLifecycleInvalid
	}
	raw, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil {
		return errLifecycleIO
	}
	if len(raw) > 4096 || !utf8.Valid(raw) || len(raw) == 0 || raw[len(raw)-1] != '\n' {
		return errLifecycleInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(dst) != nil {
		return errLifecycleInvalid
	}
	canonical, err := json.Marshal(dst)
	if err != nil || !bytes.Equal(raw, append(canonical, '\n')) {
		return errLifecycleInvalid
	}
	return nil
}
func OpenLifecycle(ctx context.Context) (*LifecyclePair, error) {
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || !time.Now().Before(deadline) {
		return nil, ErrLifecycle
	}
	p := &LifecyclePair{deadline: deadline}
	var err error
	p.mainDir, err = lifecycleDirectory(ManagedMainDirectory)
	if err != nil {
		return nil, err
	}
	p.sideDir, err = lifecycleDirectory(ManagedSidecarDirectory)
	if err != nil {
		p.Close()
		return nil, err
	}
	fd, err := unix.Openat(int(p.mainDir.Fd()), managedLock, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		p.Close()
		if err == unix.ENOENT || err == unix.ENOTDIR || err == unix.ELOOP {
			return nil, errLifecycleInvalid
		}
		return nil, errLifecycleIO
	}
	p.lock = os.NewFile(uintptr(fd), managedLock)
	var st, parent unix.Stat_t
	if unix.Fstat(fd, &st) != nil || unix.Fstat(int(p.mainDir.Fd()), &parent) != nil {
		p.Close()
		return nil, errLifecycleIO
	}
	if st.Mode&unix.S_IFMT != unix.S_IFREG || st.Mode&07777 != 0600 || st.Nlink != 1 || st.Uid != parent.Uid || st.Size != 0 {
		p.Close()
		return nil, errLifecycleInvalid
	}
	for {
		err = unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			break
		}
		if err != unix.EWOULDBLOCK && err != unix.EAGAIN {
			p.Close()
			return nil, errLifecycleIO
		}
		select {
		case <-ctx.Done():
			p.Close()
			return nil, ErrLifecycle
		case <-time.After(10 * time.Millisecond):
		}
	}
	if err = p.Refresh(); err != nil {
		p.Close()
		return nil, err
	}
	return p, nil
}
func (p *LifecyclePair) Close() {
	// Closing this duplicate never explicitly unlocks an inherited holder's
	// open-file description. Package ownership must join before its last close.
	if p.lock != nil {
		p.lock.Close()
		p.lock = nil
	}
	if p.mainDir != nil {
		p.mainDir.Close()
		p.mainDir = nil
	}
	if p.sideDir != nil {
		p.sideDir.Close()
		p.sideDir = nil
	}
}
func (p *LifecyclePair) Refresh() error {
	if !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	if err := lifecycleReadAt(p.mainDir, managedRecord, &p.Main); err != nil {
		return err
	}
	if err := lifecycleReadAt(p.sideDir, managedRecord, &p.Sidecar); err != nil {
		return err
	}
	if !lifecycleMainValid(&p.Main) || !lifecycleSidecarValid(&p.Sidecar) {
		return errLifecycleInvalid
	}
	for _, dir := range []*os.File{p.mainDir, p.sideDir} {
		fd, err := unix.Openat(int(dir.Fd()), ".", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
		if err != nil {
			return errLifecycleIO
		}
		f := os.NewFile(uintptr(fd), ".")
		entries, err := f.Readdirnames(128)
		f.Close()
		if err != nil && err != io.EOF {
			return errLifecycleIO
		}
		if len(entries) == 128 {
			return errLifecycleInvalid
		}
		for _, name := range entries {
			if strings.HasPrefix(name, ".lifecycle-") {
				return errLifecycleInvalid
			}
		}
	}
	if !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	return nil
}
func (p *LifecyclePair) publish(dir *os.File, value any) error {
	if !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	raw, err := json.Marshal(value)
	if err != nil || len(raw)+1 > 4096 {
		return ErrLifecycle
	}
	raw = append(raw, '\n')
	id, err := NewLifecycleID()
	if err != nil {
		return err
	}
	name := ".lifecycle-" + id
	fd, err := unix.Openat(int(dir.Fd()), name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
	if err != nil {
		return errLifecycleIO
	}
	f := os.NewFile(uintptr(fd), name)
	var parent unix.Stat_t
	if unix.Fstat(int(dir.Fd()), &parent) != nil {
		f.Close()
		return errLifecycleIO
	}
	if os.Geteuid() == 0 && f.Chown(int(parent.Uid), int(parent.Gid)) != nil {
		f.Close()
		return errLifecycleIO
	}
	n, err := f.Write(raw)
	if err != nil || n != len(raw) || f.Sync() != nil {
		f.Close()
		return errLifecycleIO
	}
	if f.Close() != nil {
		return errLifecycleIO
	}
	if !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	if unix.Renameat(int(dir.Fd()), name, int(dir.Fd()), managedRecord) != nil || dir.Sync() != nil {
		return errLifecycleIO
	}
	if !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	return nil
}
func (p *LifecyclePair) WriteMain() error {
	if !lifecycleMainValid(&p.Main) {
		return errLifecycleInvalid
	}
	return p.publish(p.mainDir, &p.Main)
}
func (p *LifecyclePair) WriteSidecar() error {
	if !lifecycleSidecarValid(&p.Sidecar) {
		return errLifecycleInvalid
	}
	return p.publish(p.sideDir, &p.Sidecar)
}
func (p *LifecyclePair) Eligible(boot string) error {
	now, err := LifecycleNow()
	if err != nil {
		return err
	}
	if !p.currentRecords(boot, now) {
		return errLifecycleInvalid
	}
	if p.Main.Policy.Desired != "run" || p.Main.Policy.HardBlock != "none" || p.Sidecar.Halt || p.Sidecar.Failure != "none" || p.Sidecar.Outcome == "unknown" || p.Main.Block != "none" {
		return ErrLifecycle
	}
	return nil
}

// Intent is distinct from eligibility failure. This proves only a coherent
// current inhibition; the wrapper must still bind its OWN roles and finish all
// physical withdrawal/Wait/scanner/join/final-boundary work.
func (p *LifecyclePair) CurrentInhibition(boot string, now int64) bool {
	return p.currentRecords(boot, now) && p.Main.Policy.Desired == "stopped" && p.Sidecar.Halt && p.Main.Health == nil &&
		p.Main.Policy.HardBlock == "none" && p.Main.Block == "none" && p.Sidecar.Failure == "none" &&
		lifecycleOneOf(p.Main.Phase, "live", "retiring") && lifecycleOneOf(p.Sidecar.Outcome, "waiting", "admitted", "closed")
}

// Spend mutates only the wrapper-owned ring. Caller must have independently
// admitted installed authority and actual empty predecessor before calling.
func (p *LifecyclePair) Spend(now int64, owner LifecycleProcess, generation string) error {
	if p.Eligible(p.Main.RuntimeBoot) != nil || !p.Main.Clean || !lifecycleID(generation) || !lifecycleProcessValid(&owner) || owner.PID < 2 {
		return ErrLifecycle
	}
	claim, finish := context.WithDeadline(context.Background(), p.deadline)
	defer finish()
	boot, e := LifecycleBoot()
	stamp, clockErr := LifecycleNow()
	if e != nil || clockErr != nil || now <= 0 || now > stamp || boot != p.Main.RuntimeBoot || lifecycleBootCensus(claim, p, owner, boot, true) != nil {
		return ErrLifecycle
	}
	// The census may consume time or cross exact rate expiry. Account at the
	// final current clock, never at the caller's earlier pre-census stamp.
	stamp, clockErr = LifecycleNow()
	if clockErr != nil || stamp < now || !time.Now().Before(p.deadline) {
		return ErrLifecycle
	}
	now = stamp
	rate := &p.Main.Rate
	kept := make([]int64, 0, 3)
	for _, n := range rate.Spends {
		if n > now {
			return ErrLifecycle
		}
		if now-n < ManagedWindowNS {
			kept = append(kept, n)
		}
	}
	if rate.NotBeforeNS != nil && now < *rate.NotBeforeNS || len(kept) == 3 {
		return ErrRateDeferred
	}
	rate.Spends = append(kept, now)
	// Equal carried charges still need their source/end provenance after the
	// third spend. Keep the ALREADY consumed backoff unchanged until that
	// prefix expires; neither charge clocks nor its bound are renewed here.
	if len(kept) < 2 || kept[0] != kept[1] {
		rate.LastEndNS, rate.NotBeforeNS = nil, nil
	}
	p.Main.Phase, p.Main.Clean, p.Main.Generation, p.Main.Wrapper = "live", false, &generation, &owner
	p.Main.Sidecar = p.Sidecar.Binding
	return p.WriteMain() // never refund a visible spend after missing acknowledgement
}
func (p *LifecyclePair) RetireClean(now int64, owner LifecycleProcess, generation string) error {
	m, s := &p.Main, &p.Sidecar
	boot, err := LifecycleBoot()
	if err != nil || !time.Now().Before(p.deadline) || !lifecycleMainValid(m) || !lifecycleSidecarValid(s) ||
		m.Policy != s.Policy || m.RuntimeBoot != boot || s.RuntimeBoot != boot || m.BootTransitionID != s.BootTransitionID ||
		s.Failure != "none" || m.Policy.HardBlock != "none" || m.Wrapper == nil || *m.Wrapper != owner ||
		m.Generation == nil || *m.Generation != generation || m.Phase != "retiring" || m.Clean || m.Block != "none" ||
		m.Launcher == nil || m.Worker == nil || m.Sidecar == nil || s.Binding == nil || *m.Sidecar != *s.Binding ||
		s.Generation != nil && *s.Generation != generation || m.Teardown == nil || now < m.Teardown.BeganNS || now >= m.Teardown.DeadlineNS ||
		s.TerminalNS != nil && *s.TerminalNS > now || now <= 0 || now > math.MaxInt64-int64(120*time.Second) {
		return ErrLifecycle
	}
	stamp, clockErr := LifecycleNow()
	if clockErr != nil || now > stamp || !p.currentRecords(boot, stamp) {
		return ErrLifecycle
	}
	kept := make([]int64, 0, 3)
	for _, spend := range m.Rate.Spends {
		if spend > now {
			return ErrLifecycle
		}
		if now-spend < ManagedWindowNS {
			kept = append(kept, spend)
		}
	}
	n := len(kept)
	m.Rate.Spends = kept
	delay := int64(30 * time.Second)
	if n >= 2 {
		delay *= 2
	}
	if n == 3 {
		delay *= 2
	}
	end, bound := now, now+delay
	m.Rate.LastEndNS, m.Rate.NotBeforeNS = &end, &bound
	m.Phase, m.Clean, m.Generation, m.Wrapper, m.Launcher, m.Worker, m.Sidecar, m.Health, m.Teardown = "idle", true, nil, nil, nil, nil, nil, nil, nil
	return p.WriteMain()
}

// Qualification accounts for healthy-AS-OBSERVED running after acknowledged
// composite READY. It cannot certify an uncommunicated sidecar gate loss.
// Receipts, never proof completion, measure gaps and subsequent elapsed time.
// No status query can construct qualification or reset the durable ring.
type LifecycleQualification struct {
	began, last int64
	count       uint64
	sequence    uint64
	reached     bool
}

func (q *LifecycleQualification) Observe(receiptNS, readyNS int64, sequence uint64, qualified bool) bool {
	if !qualified || receiptNS <= 0 || readyNS <= 0 || sequence == 0 {
		*q = LifecycleQualification{}
		return false
	}
	if q.sequence != 0 && sequence <= q.sequence {
		return false
	} // duplicates/old sequences are inert
	if q.last != 0 && (receiptNS <= q.last || receiptNS-q.last > ManagedLeaseNS || q.sequence == ^uint64(0) || sequence != q.sequence+1) {
		*q = LifecycleQualification{}
		return false
	}
	if q.began == 0 {
		q.began = receiptNS
		if readyNS > q.began {
			q.began = readyNS
		}
	}
	q.last, q.sequence = receiptNS, sequence
	if q.count < 26 {
		q.count++
	} // bounded, with one threshold per uninterrupted interval
	if !q.reached && receiptNS >= q.began && receiptNS-q.began >= int64(300*time.Second) && q.count >= 26 {
		q.reached = true
		return true
	}
	return false
}

// PinLifecycleProcess acquires a proc directory and pidfd before reading the
// tuple. All subsequent reads use that held directory; there is no numeric PID
// signal fallback. Manager identity and cgroup checks belong to the caller.
type LifecyclePin struct {
	proc        *os.File
	pidfd       int
	Identity    LifecycleProcess
	expectedUID uint32
}

func PinLifecycleProcess(identity LifecycleProcess) (*LifecyclePin, error) {
	if !lifecycleProcessValid(&identity) {
		return nil, ErrLifecycle
	}
	fd, err := unix.Open("/proc/"+strconv.Itoa(identity.PID), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		if err == unix.ENOENT {
			return nil, errLifecycleIdentity
		}
		return nil, ErrLifecycle
	}
	p := &LifecyclePin{proc: os.NewFile(uintptr(fd), "proc"), pidfd: -1, Identity: identity, expectedUID: lifecycleServiceUID()}
	p.pidfd, err = unix.PidfdOpen(identity.PID, 0)
	if err != nil {
		p.Close()
		return nil, ErrLifecycle
	}
	if p.Validate() != nil {
		p.Close()
		return nil, errLifecycleIdentity
	}
	return p, nil
}
func (p *LifecyclePin) read(name string, cap int64) ([]byte, error) {
	fd, err := unix.Openat(int(p.proc.Fd()), name, unix.O_RDONLY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrLifecycle
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, cap+1))
	if err != nil || int64(len(raw)) > cap {
		return nil, ErrLifecycle
	}
	return raw, nil
}
func (p *LifecyclePin) Validate() error {
	if unix.PidfdSendSignal(p.pidfd, 0, nil, 0) != nil {
		return ErrLifecycle
	}
	var st unix.Stat_t
	if unix.Fstat(int(p.proc.Fd()), &st) != nil || st.Uid != p.expectedUID {
		return ErrLifecycle
	}
	raw, err := p.read("stat", 4096)
	if err != nil {
		return err
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return ErrLifecycle
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 || fields[0] == "Z" || fields[0] == "X" {
		return ErrLifecycle
	}
	ticks, err := strconv.ParseInt(fields[19], 10, 64)
	if err != nil || ticks != p.Identity.StartTicks {
		return ErrLifecycle
	}
	return nil
}
func (p *LifecyclePin) Signal(ctx context.Context, sig unix.Signal) error {
	if startupContextError(ctx) != nil || p.Validate() != nil {
		return ErrLifecycle
	}
	// Identity work can consume the caller's ORIGINAL phase. Check absolute
	// time as well as cancellation at the send boundary, even if its timer
	// delivery is pending. This does not preempt an admitted kernel call.
	if startupContextError(ctx) != nil {
		return ErrLifecycle
	}
	if unix.PidfdSendSignal(p.pidfd, sig, nil, 0) != nil {
		return ErrLifecycle
	}
	if startupContextError(ctx) != nil {
		return ErrLifecycle
	}
	return nil
}
func (p *LifecyclePin) Dead() bool {
	poll := []unix.PollFd{{Fd: int32(p.pidfd), Events: unix.POLLIN}}
	n, err := unix.Poll(poll, 0)
	return err == nil && n == 1 && poll[0].Revents&unix.POLLIN != 0
}
func (p *LifecyclePin) Close() {
	if p.proc != nil {
		p.proc.Close()
		p.proc = nil
	}
	if p.pidfd >= 0 {
		unix.Close(p.pidfd)
		p.pidfd = -1
	}
}

// CurrentLifecycleProcess reads only this process's kernel identity. The
// invocation is still checked independently against the manager at each use.
func CurrentLifecycleProcess() (LifecycleProcess, error) {
	invocation := os.Getenv("INVOCATION_ID")
	if !lifecycleID(invocation) {
		return LifecycleProcess{}, ErrLifecycle
	}
	raw, err := os.ReadFile("/proc/self/stat")
	if err != nil || len(raw) > 4096 {
		return LifecycleProcess{}, ErrLifecycle
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return LifecycleProcess{}, ErrLifecycle
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 {
		return LifecycleProcess{}, ErrLifecycle
	}
	ticks, err := strconv.ParseInt(fields[19], 10, 64)
	if err != nil || ticks <= 0 {
		return LifecycleProcess{}, ErrLifecycle
	}
	return LifecycleProcess{PID: os.Getpid(), StartTicks: ticks, Invocation: invocation}, nil
}

// ManagedIdentityPreflight is read-only and runs before an engine can create or
// repair its state. The upstream FileStore constructor is deliberately NOT used:
// v1.78.0 creates a file for absent state and can change directory permissions.
func ManagedIdentityPreflight(c Config) error {
	marker := filepath.Join(c.StateDir, consumedMarker)
	if _, err := os.Lstat(marker); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return ErrLifecycle
	}
	if noSymlinkPath(marker) != nil {
		return ErrLifecycle
	}
	st, err := os.Lstat(marker)
	if err != nil || !st.Mode().IsRegular() || st.Mode().Perm() != 0600 || !ownedByCurrentUser(st) {
		return ErrLifecycle
	}
	// A consumed input must be absent. consumeInput remains the authoritative
	// replay refusal; preflight does not remove or recreate either file.
	if _, err = os.Lstat(c.CredentialFile); !os.IsNotExist(err) {
		return ErrLifecycle
	}
	path := filepath.Join(c.StateDir, "tailscaled.state")
	if noSymlinkPath(path) != nil {
		return ErrLifecycle
	}
	f, err := os.Open(path)
	if err != nil {
		return ErrLifecycle
	}
	defer f.Close()
	st, err = f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Mode().Perm() != 0600 || !ownedByCurrentUser(st) || st.Size() <= 0 || st.Size() > 1<<20 {
		return ErrLifecycle
	}
	raw, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil || len(raw) > 1<<20 {
		return ErrLifecycle
	}
	defer clear(raw)
	// Read the same snapshot/profile keys used by pinned v1.78.0. Reuse its
	// Prefs/key decoders, without constructing the mutating FileStore/backend.
	var snapshot map[ipn.StateKey][]byte
	if json.Unmarshal(raw, &snapshot) != nil || snapshot == nil {
		return ErrLifecycle
	}
	defer func() {
		for _, value := range snapshot {
			clear(value)
		}
	}()
	selected := ipn.StateKey(snapshot[ipn.CurrentProfileStateKey])
	if selected == "" {
		return ErrLifecycle
	}
	var profiles map[ipn.ProfileID]*ipn.LoginProfile
	if json.Unmarshal(snapshot[ipn.KnownProfilesStateKey], &profiles) != nil {
		return ErrLifecycle
	}
	var profile *ipn.LoginProfile
	for id, candidate := range profiles {
		if candidate == nil || candidate.ID != id {
			return ErrLifecycle
		}
		if candidate.Key == selected {
			if profile != nil {
				return ErrLifecycle
			}
			profile = candidate
		}
	}
	if profile == nil || profile.ControlURL != c.ControlURL || profile.NodeID == "" {
		return ErrLifecycle
	}
	var machine key.MachinePrivate
	if machine.UnmarshalText(snapshot[ipn.MachineKeyStateKey]) != nil || machine.IsZero() {
		return ErrLifecycle
	}
	prefs := ipn.NewPrefs()
	if ipn.PrefsFromBytes(snapshot[selected], prefs) != nil || prefs.Persist == nil ||
		prefs.Persist.PrivateNodeKey.IsZero() || prefs.Persist.NodeID != profile.NodeID ||
		prefs.ControlURL != c.ControlURL || prefs.Hostname != c.RoleIdentity || !prefs.WantRunning {
		return ErrLifecycle
	}
	return nil
}

// All commands here have fixed executable/operation/units. Output is bounded
// during production, not truncated into an apparently successful observation.
type lifecycleOutput struct {
	b        bytes.Buffer
	overflow bool
}

func (w *lifecycleOutput) Write(b []byte) (int, error) {
	if w.b.Len()+len(b) > 4096 {
		w.overflow = true
		return 0, ErrLifecycle
	}
	return w.b.Write(b)
}

var lifecycleProperties = []string{"ActiveState", "SubState", "Result", "MainPID", "ControlPID", "InvocationID", "ExecMainCode", "ExecMainStatus", "ControlGroup", "Job"}

func LifecycleUnit(ctx context.Context, unit string, timing ...string) (map[string]string, error) {
	names := append([]string{}, lifecycleProperties...)
	if unit == "happyranch-managed.target" {
		names = []string{"ActiveState", "SubState", "Job"}
	}
	if len(timing) > 0 {
		if unit != "happyranch-tsnet-sidecar.service" || len(timing) != 1 || timing[0] != "InactiveExitTimestampMonotonic" {
			return nil, ErrLifecycle
		}
		names = append(names, timing[0])
	}
	if !lifecycleOneOf(unit, "happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target") {
		return nil, ErrLifecycle
	}
	query, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	args := []string{"show", unit}
	for _, key := range names {
		args = append(args, "--property="+key)
	}
	cmd := exec.CommandContext(query, "/usr/bin/systemctl", args...)
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C"}
	var output lifecycleOutput
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	cmd.WaitDelay = time.Second
	if cmd.Run() != nil || query.Err() != nil || output.overflow {
		return nil, ErrLifecycle
	}
	raw := output.b.String()
	if !utf8.ValidString(raw) || !strings.HasSuffix(raw, "\n") {
		return nil, ErrLifecycle
	}
	result := map[string]string{}
	for _, line := range strings.Split(strings.TrimSuffix(raw, "\n"), "\n") {
		key, value, ok := strings.Cut(line, "=")
		if !ok || !lifecycleOneOf(key, names...) {
			return nil, ErrLifecycle
		}
		if _, ok := result[key]; ok {
			return nil, ErrLifecycle
		}
		for _, c := range line {
			if c < 32 || c == 127 {
				return nil, ErrLifecycle
			}
		}
		result[key] = value
	}
	if len(result) != len(names) || ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	return result, nil
}

// v255 source contract: show Job is empty or a canonical decimal job ID;
// list-jobs --plain emits exactly ID UNIT TYPE STATE. No localized headers,
// ellipsized names, extra columns or missing job rows are admitted. This is a
// fixed three-unit read, not discovery/adoption of arbitrary manager jobs.
func lifecycleJobs(ctx context.Context) (map[string]string, error) {
	query, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	cmd := exec.CommandContext(query, "/usr/bin/systemctl", "list-jobs", "--no-legend", "--no-pager", "--plain",
		"happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target")
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C"}
	var output lifecycleOutput
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	if cmd.Run() != nil || query.Err() != nil || ctx.Err() != nil || output.overflow {
		return nil, ErrLifecycle
	}
	raw := output.b.String()
	if !utf8.ValidString(raw) || raw != "" && !strings.HasSuffix(raw, "\n") {
		return nil, ErrLifecycle
	}
	jobs := map[string]string{}
	ids := map[string]bool{}
	for _, row := range strings.Split(strings.TrimSuffix(raw, "\n"), "\n") {
		if row == "" {
			if raw != "" {
				return nil, ErrLifecycle
			}
			continue
		}
		for _, c := range row {
			if c < 32 || c == 127 {
				return nil, ErrLifecycle
			}
		}
		fields := strings.Fields(row)
		if len(fields) != 4 {
			return nil, ErrLifecycle
		}
		id, e := strconv.ParseUint(fields[0], 10, 32)
		if e != nil || id == 0 || strconv.FormatUint(id, 10) != fields[0] || ids[fields[0]] ||
			!lifecycleOneOf(fields[1], "happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target") ||
			jobs[fields[1]] != "" || !lifecycleOneOf(fields[3], "waiting", "running") {
			return nil, ErrLifecycle
		}
		ids[fields[0]] = true
		jobs[fields[1]] = strings.Join([]string{fields[0], fields[2], fields[3]}, " ")
	}
	return jobs, nil
}
func lifecycleManagerCurrent(ctx context.Context, initial bool) (map[string]map[string]string, error) {
	before, e := lifecycleJobs(ctx)
	if e != nil {
		return nil, e
	}
	units := map[string]map[string]string{}
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target"} {
		props, e := LifecycleUnit(ctx, unit)
		if e != nil {
			return nil, e
		}
		units[unit] = props
		if props["ActiveState"] == "deactivating" {
			return nil, errLifecycleInhibited
		}
		job := strings.Fields(before[unit])
		if len(job) != 0 && lifecycleOneOf(job[1], "stop", "restart", "try-restart", "reload-or-restart") {
			return nil, errLifecycleInhibited
		}
		if len(job) == 0 {
			if props["Job"] != "" {
				return nil, ErrLifecycle
			}
		} else {
			if !initial || job[1] != "start" || props["Job"] != job[0] ||
				!(props["ActiveState"] == "activating" || props["ActiveState"] == "inactive" && props["SubState"] == "dead") {
				return nil, ErrLifecycle
			}
		}
		if unit == "happyranch-managed.target" {
			if !(props["ActiveState"] == "active" && props["SubState"] == "active" ||
				props["ActiveState"] == "inactive" && props["SubState"] == "dead" || props["ActiveState"] == "activating" && len(job) != 0) {
				return nil, ErrLifecycle
			}
		}
	}
	after, e := lifecycleJobs(ctx)
	if e != nil || len(before) != len(after) {
		return nil, ErrLifecycle
	}
	for unit, job := range before {
		if after[unit] != job {
			return nil, ErrLifecycle
		}
	}
	if ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	return units, nil
}

// Observation only. Runtime owners use a current raw stop as inhibition, not
// as durable configured intent or proof of cleanup. Query loss stays unknown.
func LifecycleStopRequested(ctx context.Context) (bool, error) {
	_, e := lifecycleManagerCurrent(ctx, true)
	if errors.Is(e, errLifecycleInhibited) {
		return true, nil
	}
	return false, e
}
func lifecycleFixedAbsent(ctx context.Context) bool {
	units, e := lifecycleManagerCurrent(ctx, false)
	if e != nil {
		return false
	}
	target := units["happyranch-managed.target"]
	if target["ActiveState"] != "inactive" || target["SubState"] != "dead" || !LifecycleAbsent(ctx, "happyranch-connector.service", nil) ||
		!LifecycleAbsent(ctx, "happyranch-tsnet-sidecar.service", nil) {
		return false
	}
	final, e := lifecycleManagerCurrent(ctx, false)
	return e == nil && final["happyranch-managed.target"]["ActiveState"] == "inactive" && final["happyranch-managed.target"]["SubState"] == "dead" && ctx.Err() == nil
}

func lifecycleCgroup(unit string) string { return "/system.slice/" + unit }
func lifecycleCgroupMembers(unit string) ([]int, error) {
	file, err := os.Open("/sys/fs/cgroup" + lifecycleCgroup(unit) + "/cgroup.procs")
	if os.IsNotExist(err) {
		return []int{}, nil
	}
	if err != nil {
		return nil, ErrLifecycle
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil || len(raw) > 4096 {
		return nil, ErrLifecycle
	}
	fields := strings.Fields(string(raw))
	if len(fields) > 8 {
		return nil, ErrLifecycle
	}
	result := make([]int, 0, len(fields))
	seen := map[int]bool{}
	for _, field := range fields {
		n, err := strconv.Atoi(field)
		if err != nil || n < 2 || n > math.MaxInt32 || strconv.Itoa(n) != field || seen[n] {
			return nil, ErrLifecycle
		}
		seen[n] = true
		result = append(result, n)
	}
	return result, nil
}
func ValidateLifecyclePin(ctx context.Context, pin *LifecyclePin, unit string, main bool) error {
	props, err := LifecycleUnit(ctx, unit)
	if err != nil || props["InvocationID"] != pin.Identity.Invocation || props["ControlGroup"] != lifecycleCgroup(unit) {
		if err != nil {
			return err
		}
		return errLifecycleIdentity
	}
	if main && props["MainPID"] != strconv.Itoa(pin.Identity.PID) {
		return errLifecycleIdentity
	}
	if !lifecycleOneOf(props["ActiveState"], "active", "activating", "deactivating") {
		return ErrLifecycle
	}
	group, err := pin.read("cgroup", 4096)
	if err != nil || string(group) != "0::"+lifecycleCgroup(unit)+"\n" || pin.Validate() != nil {
		return errLifecycleIdentity
	}
	exe, err := unix.Openat(int(pin.proc.Fd()), "exe", unix.O_PATH|unix.O_CLOEXEC, 0)
	if err != nil {
		return ErrLifecycle
	}
	defer unix.Close(exe)
	path := "/opt/happyranch/bin/happyranch-tsnet-sidecar"
	if !main {
		path = "/opt/happyranch/bin/happyranch-connector"
	}
	expected, err := unix.Open(path, unix.O_PATH|unix.O_CLOEXEC, 0)
	if err != nil {
		return ErrLifecycle
	}
	defer unix.Close(expected)
	var a, b unix.Stat_t
	if unix.Fstat(exe, &a) != nil || unix.Fstat(expected, &b) != nil || a.Mode&unix.S_IFMT != unix.S_IFREG || a.Dev != b.Dev || a.Ino != b.Ino {
		return ErrLifecycle
	}
	again, err := LifecycleUnit(ctx, unit)
	if err != nil || again["InvocationID"] != props["InvocationID"] || again["MainPID"] != props["MainPID"] || again["ControlGroup"] != props["ControlGroup"] {
		return ErrLifecycle
	}
	return nil
}
func LifecycleAbsent(ctx context.Context, unit string, pin *LifecyclePin) bool {
	if !lifecycleOneOf(unit, "happyranch-connector.service", "happyranch-tsnet-sidecar.service") || pin != nil && !pin.Dead() {
		return false
	}
	props, err := LifecycleUnit(ctx, unit)
	if err != nil || props["MainPID"] != "0" || props["ControlPID"] != "0" || props["Job"] != "" ||
		!(props["ActiveState"] == "inactive" && props["SubState"] == "dead" || props["ActiveState"] == "failed" && props["SubState"] == "failed") {
		return false
	}
	members, err := lifecycleCgroupMembers(unit)
	if err != nil || len(members) != 0 {
		return false
	}
	again, e := LifecycleUnit(ctx, unit)
	if e != nil || ctx.Err() != nil {
		return false
	}
	for _, key := range lifecycleProperties {
		if again[key] != props[key] {
			return false
		}
	}
	return true
}

// RunLifecyclePreflight reuses the existing installed Python readers. It never
// launches a worker, imports a test, repairs a transaction or passes an arbitrary
// command. The foreground worker repeats its credential capability after spend.
func RunLifecyclePreflight(ctx context.Context) error {
	deadline, ok := ctx.Deadline()
	now, err := LifecycleNow()
	if !ok || err != nil || ctx.Err() != nil {
		return ErrLifecycle
	}
	cmd := exec.CommandContext(ctx, "/opt/happyranch/bin/happyranch-connector", "managed-private", "eligibility")
	// Kernel observation precedes the remaining-Go-time sample: time spent
	// preparing this request can only shorten the translated absolute bound.
	remaining := time.Until(deadline)
	if remaining <= 0 || int64(remaining) > math.MaxInt64-now {
		return ErrLifecycle
	}
	cmd.Env = append(os.Environ(), "HAPPYRANCH_LIFECYCLE_DEADLINE_NS="+strconv.FormatInt(now+int64(remaining), 10))
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	if ctx.Err() != nil || !time.Now().Before(deadline) {
		return ErrLifecycle
	}
	if cmd.Run() != nil || ctx.Err() != nil || !time.Now().Before(deadline) {
		return ErrLifecycle
	}
	return nil
}
func lifecyclePolicyEligible(p *LifecyclePair) bool {
	return p.Main.Policy == p.Sidecar.Policy && p.Main.Policy.Desired == "run" && p.Main.Policy.HardBlock == "none" &&
		p.Sidecar.Failure == "none" && !lifecycleOneOf(p.Main.Block, "io_failure", "invalid_state", "identity_loss", "manager_refused", "join_failed")
}
func lifecycleCarry(rate LifecycleRate, boot string, now int64) LifecycleRate {
	result := LifecycleRate{Boot: boot, Spends: make([]int64, len(rate.Spends))}
	for i := range result.Spends {
		result.Spends[i] = now
	}
	if len(result.Spends) > 0 {
		end := now
		delay := int64(30*time.Second) << uint(len(result.Spends)-1)
		until := now + delay
		result.LastEndNS, result.NotBeforeNS = &end, &until
	}
	return result
}

// A linked pair is either one complete runtime, or exactly one no-listener
// prepared half whose old_boot points at the untouched half. Transition IDs
// cannot be recycled from the source. This also recognizes an interrupted
// previous boot before starting a genuinely new one; it certifies no old joins.
func lifecycleBootLinks(p *LifecyclePair) bool {
	m, s := &p.Main, &p.Sidecar
	if m.RuntimeBoot == s.RuntimeBoot {
		return m.BootTransitionID == s.BootTransitionID && (m.BootSource == nil) == (s.BootSource == nil) &&
			(m.BootSource == nil || *m.BootSource == *s.BootSource)
	}
	return m.Phase == "boot_prepared" && m.BootSource != nil && m.BootSource.OldBoot == s.RuntimeBoot && m.BootTransitionID != s.BootTransitionID ||
		s.Outcome == "waiting" && s.Generation == nil && s.BootSource != nil && s.BootSource.OldBoot == m.RuntimeBoot && s.BootTransitionID != m.BootTransitionID
}
func lifecycleDeparted(identity *LifecycleProcess) bool {
	if identity == nil {
		return true
	}
	fd, e := unix.Open("/proc/"+strconv.Itoa(identity.PID), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if e == unix.ENOENT {
		return true
	}
	if e != nil {
		return false
	}
	pin := &LifecyclePin{proc: os.NewFile(uintptr(fd), "departed-preparer"), pidfd: -1}
	defer pin.Close()
	raw, e := pin.read("stat", 4096)
	if e != nil {
		return false
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return false
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 {
		return false
	}
	ticks, e := strconv.ParseInt(fields[19], 10, 64)
	// Positive nonidentity is not adoption of the numeric PID. The complete
	// fixed cgroup census below must still exclude every current descendant.
	return e == nil && ticks > 0 && ticks != identity.StartTicks
}
func lifecycleBootService(ctx context.Context, unit string, props map[string]string, identity *LifecycleProcess) bool {
	members, e := lifecycleCgroupMembers(unit)
	if e != nil {
		return false
	}
	code, ce := strconv.Atoi(props["ExecMainCode"])
	status, se := strconv.Atoi(props["ExecMainStatus"])
	if ce != nil || se != nil || code < 0 || code > 3 || status < 0 || status > 255 || strconv.Itoa(code) != props["ExecMainCode"] || strconv.Itoa(status) != props["ExecMainStatus"] {
		return false
	}
	retry := unit == "happyranch-connector.service" && props["MainPID"] == "0" && code == 1 && status == 1 && props["Result"] == "exit-code"
	if !retry && (!lifecycleOneOf(props["Result"], "success", "signal") || code == 1 && status != 0) {
		return false
	}
	allowed := map[int]bool{}
	pins := []*LifecyclePin{}
	defer func() {
		for _, pin := range pins {
			pin.Close()
		}
	}()
	mainCurrent := func() bool { return true }
	helpersCurrent := func() bool { return true }
	if props["MainPID"] != "0" {
		pid, e := strconv.Atoi(props["MainPID"])
		if e != nil || pid < 2 || strconv.Itoa(pid) != props["MainPID"] {
			return false
		}
		pin, e := IdentifyLifecycleProcess(pid, props["InvocationID"])
		if e != nil {
			return false
		}
		pins = append(pins, pin)
		if identity != nil && pin.Identity != *identity || ValidateLifecyclePin(ctx, pin, unit, true) != nil {
			return false
		}
		command, e := pin.read("cmdline", 2048)
		if e != nil {
			return false
		}
		expected := "/opt/happyranch/bin/happyranch-tsnet-sidecar\x00--config\x00/etc/happyranch/sidecar.json\x00"
		if unit == "happyranch-connector.service" {
			expected = "/opt/happyranch/bin/happyranch-tsnet-sidecar\x00supervise-connector\x00/opt/happyranch/bin/happyranch-connector\x00run\x00--managed\x00--config\x00/etc/happyranch/connector.json\x00"
		}
		if string(command) != expected || !lifecycleOneOf(props["SubState"], "start", "start-post", "running") {
			return false
		}
		mainCurrent = func() bool {
			if ValidateLifecyclePin(ctx, pin, unit, true) != nil {
				return false
			}
			current, e := pin.read("cmdline", 2048)
			return e == nil && string(current) == expected && ctx.Err() == nil
		}
		allowed[pid] = true
	} else if identity != nil {
		return false
	}
	control := props["ControlPID"]
	if control != "0" {
		if unit != "happyranch-tsnet-sidecar.service" || props["ActiveState"] != "activating" || !lifecycleOneOf(props["SubState"], "start-pre", "start-post") ||
			!lifecycleID(props["InvocationID"]) || props["ControlGroup"] != lifecycleCgroup(unit) {
			return false
		}
		controlPID, e := strconv.Atoi(control)
		if e != nil || controlPID < 2 || strconv.Itoa(controlPID) != control {
			return false
		}
		helper := map[int]*LifecyclePin{}
		parents := map[int]int{}
		connector := map[int]bool{}
		// A fixed connector launcher may execute its body itself, or have ONE
		// onefile worker. The optional Go probe belongs to that actual body.
		// No sibling, arbitrary descendant or holder/environment grants a role.
		inspect := func(pid int, pin *LifecyclePin) (int, bool, error) {
			fd := int(pin.proc.Fd())
			var st unix.Stat_t
			if unix.Fstat(fd, &st) != nil || st.Uid != pin.expectedUID || st.Uid != 0 && st.Uid != lifecycleServiceUID() {
				return 0, false, ErrLifecycle
			}
			raw, e := pin.read("stat", 4096)
			if e != nil {
				return 0, false, e
			}
			end := bytes.LastIndexByte(raw, ')')
			if end < 0 {
				return 0, false, ErrLifecycle
			}
			fields := strings.Fields(string(raw[end+1:]))
			if len(fields) < 20 {
				return 0, false, ErrLifecycle
			}
			ticks, e := strconv.ParseInt(fields[19], 10, 64)
			if e != nil || ticks <= 0 || ticks != pin.Identity.StartTicks || pin.Validate() != nil {
				return 0, false, ErrLifecycle
			}
			parent, e := strconv.Atoi(fields[1])
			if e != nil {
				return 0, false, ErrLifecycle
			}
			group, e := pin.read("cgroup", 4096)
			if e != nil || string(group) != "0::"+lifecycleCgroup(unit)+"\n" {
				return 0, false, ErrLifecycle
			}
			command, e := pin.read("cmdline", 2048)
			if e != nil {
				return 0, false, e
			}
			args := []string{"/opt/happyranch/bin/happyranch-tsnet-sidecar", "managed-run-check"}
			isConnector := pid == controlPID || string(command) != strings.Join(args, "\x00")+"\x00"
			if isConnector {
				if props["SubState"] == "start-post" {
					if st.Uid != 0 {
						return 0, false, ErrLifecycle
					}
					args = []string{"/opt/happyranch/bin/happyranch-connector", "retire-enrollment-source", "--source", "/etc/happyranch/enrollment.key", "--marker", "/var/lib/happyranch-tsnet-sidecar/credential.consumed", "--dropin", "/etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"}
				} else if st.Uid == 0 {
					args = []string{"/opt/happyranch/bin/happyranch-connector", "reconcile-enrollment-retirement", "--source", "/etc/happyranch/enrollment.key", "--marker", "/var/lib/happyranch-tsnet-sidecar/credential.consumed", "--dropin", "/etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"}
				} else {
					args = []string{"/opt/happyranch/bin/happyranch-connector", "credential-capability", "--name", "enrollment.key", "--unit", unit, "--consumed-marker", "/var/lib/happyranch-tsnet-sidecar/credential.consumed"}
				}
			}
			if string(command) != strings.Join(args, "\x00")+"\x00" {
				return 0, false, ErrLifecycle
			}
			actual, e := unix.Openat(fd, "exe", unix.O_PATH|unix.O_CLOEXEC, 0)
			if e != nil {
				return 0, false, e
			}
			expected, e := unix.Open(args[0], unix.O_PATH|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
			var left, right unix.Stat_t
			same := e == nil && unix.Fstat(actual, &left) == nil && unix.Fstat(expected, &right) == nil && left.Mode&unix.S_IFMT == unix.S_IFREG && left.Ino == right.Ino && left.Dev == right.Dev
			unix.Close(actual)
			if expected >= 0 {
				unix.Close(expected)
			}
			if !same {
				return 0, false, ErrLifecycle
			}
			return parent, isConnector, nil
		}
		for _, pid := range members {
			if allowed[pid] {
				continue
			}
			fd, e := unix.Open("/proc/"+strconv.Itoa(pid), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
			if e != nil {
				return false
			}
			pin := &LifecyclePin{proc: os.NewFile(uintptr(fd), "boot-helper"), pidfd: -1, Identity: LifecycleProcess{PID: pid, Invocation: props["InvocationID"]}}
			pins = append(pins, pin)
			pin.pidfd, e = unix.PidfdOpen(pid, 0)
			if e != nil {
				return false
			}
			var st unix.Stat_t
			if unix.Fstat(fd, &st) != nil {
				return false
			}
			pin.expectedUID = st.Uid
			raw, e := pin.read("stat", 4096)
			if e != nil {
				return false
			}
			end := bytes.LastIndexByte(raw, ')')
			if end < 0 {
				return false
			}
			fields := strings.Fields(string(raw[end+1:]))
			if len(fields) < 20 {
				return false
			}
			pin.Identity.StartTicks, e = strconv.ParseInt(fields[19], 10, 64)
			if e != nil {
				return false
			}
			parent, role, e := inspect(pid, pin)
			if e != nil {
				return false
			}
			helper[pid] = pin
			parents[pid] = parent
			connector[pid] = role
			allowed[pid] = true
		}
		root := helper[controlPID]
		if root == nil || !connector[controlPID] || parents[controlPID] != 1 {
			return false
		}
		body := controlPID
		probe := 0
		for pid, pin := range helper {
			if pin.expectedUID != root.expectedUID {
				return false
			}
			if pid == controlPID {
				continue
			}
			if connector[pid] {
				if body != controlPID || parents[pid] != controlPID {
					return false
				}
				body = pid
			} else {
				if probe != 0 {
					return false
				}
				probe = pid
			}
		}
		if probe != 0 && parents[probe] != body {
			return false
		}
		// Revalidate the retained parent pins and executable/argv/UID/cgroup.
		// The final complete membership fence below applies to EVERY path.
		helpersCurrent = func() bool {
			for pid, pin := range helper {
				parent, role, e := inspect(pid, pin)
				if e != nil || parent != parents[pid] || role != connector[pid] {
					return false
				}
			}
			return ctx.Err() == nil
		}
		if !helpersCurrent() {
			return false
		}

	}
	for _, pid := range members {
		if !allowed[pid] {
			return false
		}
	}
	if len(members) != len(allowed) {
		return false
	}
	if len(members) == 0 && !(props["ActiveState"] == "inactive" && props["SubState"] == "dead" || props["ActiveState"] == "failed" && props["SubState"] == "failed" || props["ActiveState"] == "activating" && props["SubState"] == "start-pre") {
		return false
	}
	if !mainCurrent() || !helpersCurrent() {
		return false
	}
	for _, pin := range pins {
		if pin.Validate() != nil {
			return false
		}
	}
	// MainPID-only and empty/no-helper routes need the SAME complete reread.
	// This is a bounded observation, not atomic protection after its fence.
	current, e := lifecycleCgroupMembers(unit)
	if e != nil || len(current) != len(members) {
		return false
	}
	for _, pid := range current {
		if !allowed[pid] {
			return false
		}
	}
	again, e := LifecycleUnit(ctx, unit)
	if e != nil {
		return false
	}
	for _, key := range lifecycleProperties {
		if again[key] != props[key] {
			return false
		}
	}
	return ctx.Err() == nil
}
func lifecycleBootCensus(ctx context.Context, p *LifecyclePair, self LifecycleProcess, boot string, main bool) error {
	if !lifecyclePolicyEligible(p) || !lifecycleBootLinks(p) || RunLifecyclePreflight(ctx) != nil {
		return ErrLifecycle
	}
	stamp, e := LifecycleNow()
	if e != nil {
		return e
	}
	if p.Main.RuntimeBoot == boot {
		for _, n := range p.Main.Rate.Spends {
			if n > stamp {
				return errLifecycleInvalid
			}
		}
		if p.Main.Rate.LastEndNS != nil && *p.Main.Rate.LastEndNS > stamp || p.Main.Health != nil || p.Main.Teardown != nil || p.Main.Generation != nil ||
			p.Main.Phase != "boot_prepared" && !p.Main.Clean {
			return ErrLifecycle
		}
	}
	if p.Sidecar.RuntimeBoot == boot {
		if p.Sidecar.Halt || p.Sidecar.Outcome == "unknown" || p.Sidecar.Outcome == "waiting" && p.Sidecar.Generation != nil {
			return ErrLifecycle
		}
		if p.Sidecar.Outcome == "admitted" && (!p.Main.Clean || !lifecycleDeparted(p.Sidecar.Binding)) {
			return ErrLifecycle
		}
		if p.Sidecar.TerminalNS != nil && *p.Sidecar.TerminalNS > stamp {
			return errLifecycleInvalid
		}
	}
	units, e := lifecycleManagerCurrent(ctx, true)
	if e != nil {
		return e
	}
	var wrapper, binding *LifecycleProcess
	if p.Main.RuntimeBoot == boot && p.Main.Phase == "boot_prepared" {
		wrapper = p.Main.Wrapper
		if !main && lifecycleDeparted(wrapper) {
			wrapper = nil
		}
	}
	if p.Sidecar.RuntimeBoot == boot && p.Sidecar.Outcome == "waiting" {
		binding = p.Sidecar.Binding
		if main && lifecycleDeparted(binding) {
			binding = nil
		}
	}
	if main {
		if wrapper != nil && *wrapper != self && !lifecycleDeparted(wrapper) {
			return errLifecycleIdentity
		}
		wrapper = &self
	} else {
		if binding != nil && *binding != self && !lifecycleDeparted(binding) {
			return errLifecycleIdentity
		}
		binding = &self
	}
	if !lifecycleBootService(ctx, "happyranch-connector.service", units["happyranch-connector.service"], wrapper) ||
		!lifecycleBootService(ctx, "happyranch-tsnet-sidecar.service", units["happyranch-tsnet-sidecar.service"], binding) {
		return ErrLifecycle
	}
	if _, e = lifecycleManagerCurrent(ctx, true); e != nil {
		return e
	}
	if ctx.Err() != nil {
		return ErrLifecycle
	}
	return nil
}

// Independent writers preserve the untouched source and use exactly its link.
func lifecycleBootMain(ctx context.Context, p *LifecyclePair, self LifecycleProcess, boot string) error {
	if !p.Main.Policy.Enabled || lifecycleBootIdentity() != nil || lifecycleBootCensus(ctx, p, self, boot, true) != nil {
		return ErrLifecycle
	}
	if p.Main.RuntimeBoot == boot {
		if p.Main.Phase != "boot_prepared" || p.Main.BootSource == nil {
			return ErrLifecycle
		}
		p.Main.Wrapper = &self
		return p.WriteMain()
	}
	old := p.Sidecar.RuntimeBoot
	transition := ""
	if p.Sidecar.RuntimeBoot == boot {
		if p.Sidecar.BootSource == nil || p.Sidecar.BootSource.OldBoot != p.Main.RuntimeBoot || p.Sidecar.Outcome != "waiting" || p.Sidecar.Generation != nil {
			return ErrLifecycle
		}
		old = p.Sidecar.BootSource.OldBoot
		transition = p.Sidecar.BootTransitionID
	} else {
		var e error
		transition, e = NewLifecycleID()
		if e != nil {
			return e
		}
	}
	now, e := LifecycleNow()
	if e != nil || now > math.MaxInt64-int64(120*time.Second) {
		return ErrLifecycle
	}
	p.Main = LifecycleMain{Version: 2, Policy: p.Main.Policy, RuntimeBoot: boot, BootTransitionID: transition, Phase: "boot_prepared", Block: "none", Wrapper: &self,
		Rate: lifecycleCarry(p.Main.Rate, boot, now), BootSource: &LifecycleBootSource{OldBoot: old, TransitionID: transition}}
	return p.WriteMain()
}
func PrepareLifecycleBoot(ctx context.Context) error {
	boot, e := LifecycleBoot()
	if e != nil {
		return e
	}
	self, e := CurrentLifecycleProcess()
	if e != nil {
		return e
	}
	p, e := OpenLifecycle(ctx)
	if e != nil {
		return e
	}
	if p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot && p.Main.Phase != "boot_prepared" {
		if p.Eligible(boot) != nil || !p.Main.Clean || lifecycleBootCensus(ctx, p, self, boot, true) != nil {
			p.Close()
			return ErrLifecycle
		}
		p.Close()
		return nil
	}
	e = lifecycleBootMain(ctx, p, self, boot)
	p.Close()
	if e != nil {
		return e
	}
	for {
		p, e = OpenLifecycle(ctx)
		if e != nil {
			return e
		}
		if p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot {
			if !lifecycleBootLinks(p) || p.Main.Phase != "boot_prepared" || p.Main.Wrapper == nil || *p.Main.Wrapper != self || p.Eligible(boot) != nil {
				p.Close()
				return ErrLifecycle
			}
			if p.Sidecar.Outcome == "waiting" && p.Sidecar.Generation == nil {
				// Final publication repeats authority, exact linked sources,
				// current roles/jobs and deadline; no earlier census is a grant.
				if lifecycleBootCensus(ctx, p, self, boot, true) != nil || p.Refresh() != nil || !lifecycleBootLinks(p) || p.Eligible(boot) != nil ||
					p.Main.Wrapper == nil || *p.Main.Wrapper != self || p.Main.Phase != "boot_prepared" || ctx.Err() != nil {
					p.Close()
					return ErrLifecycle
				}
				p.Main.Phase, p.Main.Clean, p.Main.Wrapper = "idle", true, nil
				e = p.WriteMain()
				p.Close()
				return e
			}
		} else if !lifecycleBootLinks(p) || !lifecyclePolicyEligible(p) {
			p.Close()
			return ErrLifecycle
		}
		p.Close()
		select {
		case <-ctx.Done():
			return ErrLifecycle
		case <-time.After(20 * time.Millisecond):
		}
	}
}

// EmitLifecycleDiagnostic accepts only a closed event. The caller retains its
// own deduplication and 16/8192 invocation budget; no private values are emitted.
func EmitLifecycleDiagnostic(output io.Writer, phase, reason, origin, outcome string, code *int, revision *int64, generation *string, sequence uint64, count int) error {
	phases := map[string][]string{
		"context":    {"invocation_started", "publication_observed", "boot_validated", "boot_refused"},
		"decision":   {"intentional_stop", "sidecar_loss", "child_loss", "health_invalid", "startup_expired", "freshness_expired", "watchdog_failed", "state_invalid", "state_io_failed", "transaction_pending", "lock_unavailable", "identity_lost"},
		"withdrawal": {"admission_absent", "admission_unknown"}, "cleanup": {"join_failed", "cleanup_blocked"},
		"readmission": {"fresh_listener_recovered", "healthy_reset"}, "terminal": {"intentional_stop", "restart_requested", "cleanup_blocked", "manager_refused", "rate_deferred", "rate_window_exhausted"},
	}
	if !lifecycleOneOf(reason, phases[phase]...) || !lifecycleOneOf(origin, "intentional_request", "crash_observed", "unknown") ||
		!lifecycleOneOf(outcome, "pending", "stopped", "restart_requested", "fresh_listener_recovered", "deferred", "blocked") ||
		code != nil && (*code != 0 && *code != 1 && *code != 78 || phase != "terminal") || revision != nil && *revision <= 0 ||
		!lifecycleOptionalID(generation) || generation == nil && sequence != 0 || count < 0 || count > 3 {
		return ErrLifecycle
	}
	if generation != nil && revision == nil {
		return ErrLifecycle
	}
	if reason == "publication_observed" && (origin != "unknown" || outcome != "pending" || code != nil) {
		return ErrLifecycle
	}
	if reason == "boot_validated" && (outcome != "pending" || code != nil) {
		return ErrLifecycle
	}
	if lifecycleOneOf(reason, "rate_deferred", "rate_window_exhausted") && (outcome != "deferred" || code == nil || *code != 1) {
		return ErrLifecycle
	}
	if reason == "intentional_stop" && phase == "terminal" && (outcome != "stopped" || origin != "intentional_request" || code == nil || *code != 0) {
		return ErrLifecycle
	}
	if reason == "restart_requested" && (outcome != "restart_requested" || code == nil || *code != 1) {
		return ErrLifecycle
	}
	if lifecycleOneOf(reason, "cleanup_blocked", "manager_refused") && phase == "terminal" && (outcome != "blocked" || code == nil || *code != 78) {
		return ErrLifecycle
	}
	if reason == "fresh_listener_recovered" && outcome != "fresh_listener_recovered" {
		return ErrLifecycle
	}
	if reason == "healthy_reset" && (outcome != "pending" || count != 0) {
		return ErrLifecycle
	}
	raw, err := json.Marshal(map[string]any{"version": 2, "phase": phase, "reason": reason, "origin": origin, "outcome": outcome, "exit": code, "policy_revision": revision, "generation": generation, "sequence": sequence, "rate_count": count})
	if err != nil || len(raw)+len("connector_recovery_v2 ")+1 > 512 {
		return ErrLifecycle
	}
	_, err = fmt.Fprintf(output, "connector_recovery_v2 %s\n", raw)
	return err
}

type lifecycleRequest struct {
	Version              int     `json:"version"`
	Operation            string  `json:"operation"`
	ExpectedInstallation *string `json:"expected_installation"`
	ExpectedRevision     *int64  `json:"expected_revision"`
	OperationID          string  `json:"operation_id"`
	DeadlineNS           int64   `json:"deadline_ns"`
}

func lifecycleManager(ctx context.Context, operation string) error {
	if !lifecycleOneOf(operation, "start", "stop", "enable", "disable") {
		return ErrLifecycle
	}
	cmd := exec.CommandContext(ctx, "/usr/bin/systemctl", operation, "happyranch-managed.target")
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C"}
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	cmd.WaitDelay = time.Second
	if cmd.Run() != nil || ctx.Err() != nil {
		return ErrLifecycle
	}
	return nil
}
func (p *LifecyclePair) stopped() bool {
	return p.Main.Policy == p.Sidecar.Policy && p.Main.Policy.Desired != "run" && p.Main.Clean && p.Main.Phase == "idle" &&
		p.Sidecar.Halt && p.Sidecar.Failure == "none" && lifecycleOneOf(p.Sidecar.Outcome, "closed", "unstarted")
}

// Explicit reconcile alone may replace a COMPLETED old stopped pair. Historical
// monotonic values are validated structurally, never compared with this boot's
// clock; no old process, lease, lock ownership or cooperative join is adopted.
func lifecycleReconcileSource(p *LifecyclePair, boot string, now int64) bool {
	if p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot {
		return p.currentRecords(boot, now)
	}
	return lifecycleMainValid(&p.Main) && lifecycleSidecarValid(&p.Sidecar) &&
		p.Main.RuntimeBoot != boot && p.Main.RuntimeBoot == p.Sidecar.RuntimeBoot && lifecycleBootLinks(p) &&
		p.stopped() && p.Main.Policy.HardBlock == "none" && p.Main.Block == "none"
}

func (p *LifecyclePair) policyChange(desired string, enabled bool, id string) error {
	if p.Main.Policy != p.Sidecar.Policy || p.Main.Policy.Revision == math.MaxInt64 {
		return ErrLifecycle
	}
	policy := p.Main.Policy
	if policy.OperationID == id {
		if policy.Desired != desired || policy.Enabled != enabled {
			return ErrLifecycle
		}
		return nil
	}
	policy.Revision++
	policy.OperationID = id
	policy.Desired = desired
	policy.Enabled = enabled
	p.Main.Policy, p.Sidecar.Policy = policy, policy
	if desired != "run" {
		p.Sidecar.Halt = true
		p.Main.Health = nil
		// Inhibition's sidecar half is first; a torn pair never enables.
		if p.WriteSidecar() != nil || p.WriteMain() != nil {
			return ErrLifecycle
		}
	} else {
		p.Sidecar.Halt = false
		// For enabling, the sidecar unhalt is LAST.
		if p.WriteMain() != nil || p.WriteSidecar() != nil {
			return ErrLifecycle
		}
	}
	return nil
}

// Read-only starting/deferred projection and current run-policy helper. A
// structurally valid record is insufficient when its named role is now absent
// or copied. Early launch may be unavailable until its actual producer has
// published the next role; the observer never adopts an unknown child.
func lifecycleObservedStarting(ctx context.Context, p *LifecyclePair, boot string) bool {
	now, e := LifecycleNow()
	if e != nil || !p.currentRecords(boot, now) {
		return false
	}
	units, e := lifecycleManagerCurrent(ctx, true)
	if e != nil {
		return false
	}
	m, s := &p.Main, &p.Sidecar
	if m.Clean || m.Phase == "boot_prepared" {
		var binding *LifecycleProcess
		if s.Outcome == "waiting" {
			binding = s.Binding
		} else if s.Binding != nil && !lifecycleDeparted(s.Binding) {
			return false
		}
		return lifecycleBootService(ctx, "happyranch-connector.service", units["happyranch-connector.service"], m.Wrapper) &&
			lifecycleBootService(ctx, "happyranch-tsnet-sidecar.service", units["happyranch-tsnet-sidecar.service"], binding) && ctx.Err() == nil
	}
	if m.Phase != "live" || m.Wrapper == nil || s.Binding == nil {
		return false
	}
	for _, role := range []struct {
		identity *LifecycleProcess
		main     bool
	}{{m.Wrapper, true}, {m.Launcher, false}, {m.Worker, false}} {
		if role.identity == nil {
			continue
		}
		pin, e := PinLifecycleProcess(*role.identity)
		if e != nil {
			return false
		}
		e = ValidateLifecyclePin(ctx, pin, "happyranch-connector.service", role.main)
		pin.Close()
		if e != nil {
			return false
		}
	}
	if !lifecycleBootService(ctx, "happyranch-tsnet-sidecar.service", units["happyranch-tsnet-sidecar.service"], s.Binding) {
		return false
	}
	if m.Worker != nil {
		worker, e := PinLifecycleProcess(*m.Worker)
		if e != nil {
			return false
		}
		defer worker.Close()
		return ValidateLifecycleWorker(ctx, worker, m.Launcher.PID) == nil && ctx.Err() == nil
	}
	members, e := lifecycleCgroupMembers("happyranch-connector.service")
	if e != nil {
		return false
	}
	for _, pid := range members {
		if pid != m.Wrapper.PID && (m.Launcher == nil || pid != m.Launcher.PID) {
			return false
		}
	}
	return (len(members) == 1 && m.Launcher == nil || len(members) == 2 && m.Launcher != nil) && ctx.Err() == nil
}

func lifecycleState(p *LifecyclePair, boot string, now int64) string {
	if p.Main.Policy != p.Sidecar.Policy || p.Main.Policy.HardBlock != "none" || p.Main.Block != "none" || p.Sidecar.Failure != "none" {
		return "blocked"
	}
	if !lifecycleBootLinks(p) {
		return "blocked"
	}
	if p.Main.RuntimeBoot != boot || p.Sidecar.RuntimeBoot != boot {
		if p.Main.RuntimeBoot == boot {
			for _, n := range p.Main.Rate.Spends {
				if n > now {
					return "blocked"
				}
			}
			if p.Main.Rate.LastEndNS != nil && *p.Main.Rate.LastEndNS > now {
				return "blocked"
			}
		}
		if p.Sidecar.RuntimeBoot == boot && p.Sidecar.TerminalNS != nil && *p.Sidecar.TerminalNS > now {
			return "blocked"
		}
		if p.Main.Policy.Enabled && lifecyclePolicyEligible(p) && (p.Main.RuntimeBoot == boot && p.Main.Phase == "boot_prepared" || p.Sidecar.RuntimeBoot == boot && p.Sidecar.Outcome == "waiting" && p.Sidecar.Generation == nil) {
			return "boot_prepared"
		}
		return "unavailable"
	}
	if !p.currentRecords(boot, now) {
		return "blocked"
	}
	if p.Sidecar.Outcome == "unknown" {
		return "blocked"
	}
	if p.stopped() {
		return "stopped"
	}
	if p.Main.Policy.Desired != "run" || p.Sidecar.Halt {
		return "retiring"
	}
	if p.Main.Phase == "boot_prepared" {
		return "boot_prepared"
	}
	if p.Main.Phase == "retiring" {
		return "retiring"
	}
	if p.Main.Clean {
		count := 0
		for _, n := range p.Main.Rate.Spends {
			if now-n < ManagedWindowNS {
				count++
			}
		}
		if count == 3 {
			return "rate_exhausted_until"
		}
		if p.Main.Rate.NotBeforeNS != nil && now < *p.Main.Rate.NotBeforeNS {
			return "deferred"
		}
		return "starting"
	}
	if p.Main.Phase == "live" && p.Main.Health != nil && p.Main.Health.ValidUntilNS > now && p.Sidecar.Outcome == "admitted" {
		return "ready"
	}
	if p.Main.Phase == "live" {
		return "starting"
	}
	return "blocked"
}

// HandleLifecycleRequest is the fixed installed executable dispatch. Public
// syntax lives in the existing Python CLI; stdin is closed and bounded here.
func HandleLifecycleRequest(input io.Reader, output io.Writer) int {
	entry := time.Now()
	raw, err := io.ReadAll(io.LimitReader(input, 4097))
	if err != nil || len(raw) > 4096 || !utf8.Valid(raw) {
		return 78
	}
	var request lifecycleRequest
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || request.Version != 2 || !lifecycleID(request.OperationID) ||
		!lifecycleOneOf(request.Operation, "start", "stop", "restart", "enable", "disable", "status", "reconcile") {
		return 78
	}
	if request.ExpectedInstallation != nil && !lifecycleID(*request.ExpectedInstallation) || request.ExpectedRevision != nil && *request.ExpectedRevision <= 0 {
		return 78
	}
	var shape map[string]json.RawMessage
	if json.Unmarshal(raw, &shape) != nil || len(shape) != 6 {
		return 78
	}
	// Canonical sorted equality catches duplicate keys as well as trailing data.
	canonical, err := json.Marshal(shape)
	if err != nil || !bytes.Equal(raw, canonical) {
		return 78
	}
	sampled := time.Now()
	now, err := LifecycleNow()
	if err != nil || request.DeadlineNS <= now || request.DeadlineNS-now > int64(45*time.Second) {
		return 78
	}
	deadline := sampled.Add(time.Duration(request.DeadlineNS - now))
	if limit := entry.Add(45 * time.Second); limit.Before(deadline) {
		deadline = limit
	}
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	acquire := ctx
	var cancelAcquire context.CancelFunc
	if lifecycleOneOf(request.Operation, "stop", "disable", "restart") {
		acquire, cancelAcquire = context.WithDeadline(ctx, entry.Add(10*time.Second))
		defer cancelAcquire()
	}
	p, err := OpenLifecycle(acquire)
	if err != nil {
		return 78
	}
	boot, err := LifecycleBoot()
	if err != nil {
		p.Close()
		return 78
	}
	now, err = LifecycleNow()
	if err != nil {
		p.Close()
		return 78
	}
	selectedPolicy := p.Main.Policy
	reply := func(expected string) int {
		// Every positive result, including same-operation observation, needs
		// fresh installed authority and current ownership under the original
		// bound. An old operation ID cannot replay a successful stop/start.
		current, e := OpenLifecycle(ctx)
		if e != nil {
			return 78
		}
		defer current.Close()
		currentBoot, e := LifecycleBoot()
		if e != nil || currentBoot != boot || RunLifecyclePreflight(ctx) != nil {
			return 78
		}
		if current.Main.Policy.InstallationID != selectedPolicy.InstallationID || request.Operation != "status" && (current.Main.Policy != selectedPolicy || current.Main.Policy.OperationID != request.OperationID) {
			return 78
		}
		if current.Main.Policy != current.Sidecar.Policy {
			return 78
		}
		if request.Operation == "status" && (request.ExpectedInstallation != nil && *request.ExpectedInstallation != current.Main.Policy.InstallationID ||
			request.ExpectedRevision != nil && *request.ExpectedRevision != current.Main.Policy.Revision) {
			return 78
		}
		observed, e := LifecycleNow()
		if e != nil {
			return 78
		}
		state := lifecycleState(current, boot, observed)
		if state == "ready" && !LifecycleReady(ctx, current, true) {
			state = "unavailable"
		}
		if lifecycleOneOf(state, "starting", "deferred", "rate_exhausted_until") && !lifecycleObservedStarting(ctx, current, boot) {
			state = "unavailable"
		}
		if state == "boot_prepared" {
			if _, e = lifecycleManagerCurrent(ctx, true); e != nil {
				state = "unavailable"
			}
		}
		if state == "stopped" && !lifecycleStoppedCurrent(ctx, current) {
			state = "unavailable"
		}
		if expected == "stopped" && state != "stopped" {
			return 78
		}
		if request.Operation != "status" && lifecycleOneOf(state, "blocked", "unavailable") {
			return 78
		}
		raw, e := json.Marshal(map[string]any{"version": 2, "state": state, "operation_id": request.OperationID})
		if e != nil || ctx.Err() != nil || !time.Now().Before(deadline) {
			return 78
		}
		n, e := fmt.Fprintln(output, string(raw))
		if e != nil || n != len(raw)+1 || ctx.Err() != nil || !time.Now().Before(deadline) {
			return 78
		}
		return 0
	}
	if request.ExpectedInstallation != nil && *request.ExpectedInstallation != p.Main.Policy.InstallationID || request.ExpectedRevision != nil && *request.ExpectedRevision != p.Main.Policy.Revision {
		p.Close()
		return 78
	}
	if request.Operation == "status" {
		p.Close()
		return reply("")
	}
	if os.Geteuid() != 0 || p.Main.Policy != p.Sidecar.Policy {
		p.Close()
		return 78
	}
	if request.ExpectedInstallation != nil && *request.ExpectedInstallation != p.Main.Policy.InstallationID ||
		request.ExpectedRevision != nil && *request.ExpectedRevision != p.Main.Policy.Revision {
		p.Close()
		return 78
	}
	if request.Operation == "reconcile" && (request.ExpectedInstallation == nil || request.ExpectedRevision == nil) {
		p.Close()
		return 78
	}
	if p.Main.Policy.OperationID == request.OperationID {
		// The policy operation does not persist a generation receipt. Replaying
		// a run operation could acknowledge a later retry under that same policy.
		// Only a currently proven stopped operation can be observed as complete;
		// status remains the explicit read-only current-generation projection.
		if !lifecycleOneOf(request.Operation, "stop", "disable", "reconcile") {
			p.Close()
			return 78
		}
		policy := p.Main.Policy
		if lifecycleOneOf(request.Operation, "stop", "disable", "reconcile") && policy.Desired != "stopped" ||
			lifecycleOneOf(request.Operation, "start", "restart", "enable") && (policy.Desired != "run" || policy.HardBlock != "none") ||
			request.Operation == "disable" && policy.Enabled || request.Operation == "enable" && !policy.Enabled {
			p.Close()
			return 78
		}
		state := lifecycleState(p, boot, now)
		late := acquire.Err() != nil
		p.Close()
		if late || state != "stopped" {
			return 78
		}
		return reply("stopped")
	}
	enabled := p.Main.Policy.Enabled
	if request.Operation == "enable" && p.Main.Policy.Desired == "run" && p.Eligible(boot) == nil {
		if RunLifecyclePreflight(ctx) != nil || !lifecycleObservedStarting(ctx, p, boot) || p.policyChange("run", true, request.OperationID) != nil {
			p.Close()
			return 78
		}
		selectedPolicy = p.Main.Policy
		p.Close()
		if lifecycleManager(ctx, "enable") != nil {
			return 78
		}
		return reply("starting")
	}
	if lifecycleOneOf(request.Operation, "stop", "disable", "restart") {
		if request.Operation == "disable" {
			enabled = false
		}
		// A is bounded by entry+10s and releases before all manager progress.
		a, cancelA := context.WithDeadline(ctx, entry.Add(10*time.Second))
		deadline, _ := a.Deadline()
		p.deadline = deadline
		inhibitID := request.OperationID
		if request.Operation == "restart" {
			inhibitID = lifecycleInhibitID(request.OperationID)
		}
		err = p.policyChange("stopped", enabled, inhibitID)
		selectedPolicy = p.Main.Policy
		late := a.Err() != nil || !time.Now().Before(deadline)
		cancelA()
		p.Close()
		if err != nil || late {
			return 78
		}
		manager, cancelManager := context.WithTimeout(ctx, 35*time.Second)
		err = lifecycleManager(manager, "stop")
		cancelManager()
		if err != nil {
			return 78
		}
		p, err = OpenLifecycle(ctx)
		if err != nil {
			return 78
		}
		absent := LifecycleAbsent(ctx, "happyranch-connector.service", nil) && LifecycleAbsent(ctx, "happyranch-tsnet-sidecar.service", nil)
		clean := lifecycleStoppedCurrent(ctx, p) && p.Main.Policy == selectedPolicy && p.Main.Policy.OperationID == inhibitID && RunLifecyclePreflight(ctx) == nil
		p.Close()
		if !absent || !clean || ctx.Err() != nil {
			return 78
		}
		if request.Operation == "disable" {
			if lifecycleManager(ctx, "disable") != nil {
				return 78
			}
		}
		if request.Operation != "restart" {
			return reply("stopped")
		}
		p, err = OpenLifecycle(ctx)
		if err != nil {
			return 78
		}
		if p.Main.Policy != selectedPolicy {
			p.Close()
			return 78
		}
	}
	if request.Operation == "reconcile" {
		if request.ExpectedInstallation == nil || request.ExpectedRevision == nil || p.Main.Policy.HardBlock != "none" ||
			p.Main.Policy.Desired != "stopped" || p.Sidecar.Failure != "none" || !lifecycleOneOf(p.Main.Block, "none", "unknown_cleanup") ||
			RunLifecyclePreflight(ctx) != nil ||
			!lifecycleBootLinks(p) || !lifecycleReconcileSource(p, boot, now) || !lifecycleFixedAbsent(ctx) {
			p.Close()
			return 78
		}
		// A new stopped boundary is not a claim of predecessor joins. The
		// historical failure remains external evidence; typed hard blocks do
		// not clear here. No rate debt or identity is reset.
		now, err = LifecycleNow()
		if err != nil || now > math.MaxInt64-int64(120*time.Second) || p.Refresh() != nil || p.Main.Policy != selectedPolicy ||
			!lifecycleReconcileSource(p, boot, now) || RunLifecyclePreflight(ctx) != nil || !lifecycleFixedAbsent(ctx) || ctx.Err() != nil {
			p.Close()
			return 78
		}
		// Resample AFTER the slow proofs. Conservatively carried debt begins
		// here, so proof time cannot silently age this boot's replacement ring.
		now, err = LifecycleNow()
		if err != nil || now > math.MaxInt64-int64(120*time.Second) || ctx.Err() != nil {
			p.Close()
			return 78
		}
		transition, err := NewLifecycleID()
		if err != nil {
			p.Close()
			return 78
		}
		policy := p.Main.Policy
		if policy.Revision == math.MaxInt64 {
			p.Close()
			return 78
		}
		policy.Revision++
		policy.OperationID = request.OperationID
		rate := p.Main.Rate
		var source *LifecycleBootSource
		if p.Main.RuntimeBoot != boot {
			source = &LifecycleBootSource{OldBoot: p.Main.RuntimeBoot, TransitionID: transition}
			rate = lifecycleCarry(p.Main.Rate, boot, now)
			// Preserve a larger recorded backoff even when its spends aged
			// out before the old stopped boundary; crossboot cannot erase it.
			if p.Main.Rate.LastEndNS != nil {
				delay := *p.Main.Rate.NotBeforeNS - *p.Main.Rate.LastEndNS
				if rate.NotBeforeNS == nil || *rate.NotBeforeNS-now < delay {
					end, until := now, now+delay
					rate.LastEndNS, rate.NotBeforeNS = &end, &until
				}
			}
		} else {
			source = p.Main.BootSource
		}
		p.Main = LifecycleMain{Version: 2, Policy: policy, RuntimeBoot: boot, BootTransitionID: transition,
			Phase: "idle", Clean: true, Block: "none", Rate: rate, BootSource: source}
		if source != nil {
			source = &LifecycleBootSource{OldBoot: source.OldBoot, TransitionID: transition}
			p.Main.BootSource = source
		}
		p.Sidecar = LifecycleSidecar{Version: 2, Policy: policy, RuntimeBoot: boot, BootTransitionID: transition,
			Halt: true, Outcome: "unstarted", Failure: "none", CloseProof: "none", BootSource: source}
		if p.WriteSidecar() != nil || p.WriteMain() != nil {
			p.Close()
			return 78
		}
		selectedPolicy = p.Main.Policy
		p.Close()
		return reply("stopped")
	}
	// Start/enable may change intent only at a valid stopped boundary. Neither
	// explicit start nor restart resets the rate ring or a typed failure.
	if !lifecycleStoppedCurrent(ctx, p) || p.Main.Policy.Desired == "retired" || p.Main.Policy.HardBlock != "none" {
		p.Close()
		return 78
	}
	if request.Operation == "enable" {
		enabled = true
	}
	if RunLifecyclePreflight(ctx) != nil {
		p.Close()
		return 78
	}
	if p.policyChange("run", enabled, request.OperationID) != nil {
		p.Close()
		return 78
	}
	selectedPolicy = p.Main.Policy
	p.Close()
	if request.Operation == "enable" && lifecycleManager(ctx, "enable") != nil {
		return 78
	}
	if lifecycleManager(ctx, "start") != nil {
		return 78
	}
	p, err = OpenLifecycle(ctx)
	if err != nil {
		return 78
	}
	now, err = LifecycleNow()
	if err != nil {
		p.Close()
		return 78
	}
	state := lifecycleState(p, boot, now)
	p.Close()
	return reply(state)
}

// ManagedSidecarGuard is optional; only main's fixed installed managed entry
// constructs it. Nonmanaged Sidecar callers retain their existing behavior.
type ManagedSidecarGuard struct {
	ctx                 context.Context
	identity            LifecycleProcess
	boot                string
	generation          string
	cancel              context.CancelFunc
	veto                atomic.Bool
	failed              atomic.Bool
	failureCause        atomic.Uint32
	halted              atomic.Bool
	startupDeadline     time.Time
	unstarted           bool
	unstartedDeadline   time.Time
	unstartedPolicy     LifecyclePolicy
	unstartedTransition string
}

func NewManagedSidecarGuard(ctx context.Context, lifetime context.Context, cancelLifetime context.CancelFunc) (guard *ManagedSidecarGuard, result error) {
	if RunLifecyclePreflight(ctx) != nil {
		return nil, ErrLifecycle
	}
	self, e := CurrentLifecycleProcess()
	if e != nil {
		return nil, e
	}
	boot, e := LifecycleBoot()
	if e != nil {
		return nil, e
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		return nil, ErrLifecycle
	}
	g := &ManagedSidecarGuard{ctx: lifetime, identity: self, boot: boot, cancel: cancelLifetime, startupDeadline: deadline}
	owns := false
	defer func() {
		if result != nil && owns {
			guard = g
			if !g.unstarted {
				g.observe(ctx, result)
			}
		}
	}()
	wait, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	g.unstartedDeadline, _ = wait.Deadline()
	for {
		p, e := OpenLifecycle(wait)
		if e != nil {
			return nil, e
		}
		if p.Sidecar.Binding != nil && *p.Sidecar.Binding == self && p.Sidecar.RuntimeBoot == boot {
			owns = true
		}
		if owns && p.Main.Policy == p.Sidecar.Policy && p.Main.Policy.HardBlock == "none" && p.Main.Block == "none" && p.Sidecar.Failure == "none" &&
			(p.Main.Policy.Desired != "run" || p.Sidecar.Halt) {
			p.Close()
			return nil, g.inhibited()
		}
		if !lifecyclePolicyEligible(p) || !lifecycleBootLinks(p) {
			p.Close()
			return nil, ErrLifecycle
		}
		current := p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot
		if !current {
			if !p.Main.Policy.Enabled || lifecycleBootIdentity() != nil || lifecycleBootCensus(wait, p, self, boot, false) != nil {
				p.Close()
				return nil, ErrLifecycle
			}
			if p.Sidecar.RuntimeBoot != boot {
				old := p.Main.RuntimeBoot
				transition := ""
				if p.Main.RuntimeBoot == boot {
					if p.Main.Phase != "boot_prepared" || p.Main.BootSource == nil || p.Main.BootSource.OldBoot != p.Sidecar.RuntimeBoot {
						p.Close()
						return nil, ErrLifecycle
					}
					old = p.Main.BootSource.OldBoot
					transition = p.Main.BootTransitionID
				} else {
					transition, e = NewLifecycleID()
					if e != nil {
						p.Close()
						return nil, e
					}
				}
				p.Sidecar = LifecycleSidecar{Version: 2, Policy: p.Sidecar.Policy, RuntimeBoot: boot, BootTransitionID: transition, Binding: &self,
					Outcome: "waiting", Failure: "none", CloseProof: "none", BootSource: &LifecycleBootSource{OldBoot: old, TransitionID: transition}}
				owns = true
				if e = p.WriteSidecar(); e != nil {
					p.Close()
					return nil, e
				}
			} else {
				// Resume only the same empty linked transition. Do not wait
				// for the other writer while retaining a departed binding.
				if p.Sidecar.Outcome != "waiting" || p.Sidecar.Generation != nil || p.Sidecar.BootSource == nil ||
					p.Sidecar.Binding != nil && *p.Sidecar.Binding != self && !lifecycleDeparted(p.Sidecar.Binding) {
					p.Close()
					return nil, ErrLifecycle
				}
				p.Sidecar.Binding = &self
				owns = true
				if e = p.WriteSidecar(); e != nil {
					p.Close()
					return nil, e
				}
			}
			p.Close()
			select {
			case <-wait.Done():
				return nil, ErrLifecycle
			case <-time.After(20 * time.Millisecond):
			}
			continue
		}
		if p.Eligible(boot) != nil {
			p.Close()
			return nil, ErrLifecycle
		}
		if p.Sidecar.Binding != nil && *p.Sidecar.Binding == self {
			owns = true
		}
		if p.Main.Phase == "live" {
			if p.Main.Sidecar == nil || *p.Main.Sidecar != self || p.Sidecar.Binding == nil || *p.Sidecar.Binding != self || p.Main.Generation == nil || p.Sidecar.Outcome != "waiting" {
				p.Close()
				return nil, ErrLifecycle
			}
			if p.Main.Health != nil {
				now, e := LifecycleNow()
				if e != nil || p.Main.Health.ValidUntilNS <= now {
					p.Close()
					return nil, ErrLifecycle
				}
				g.generation = *p.Main.Generation
				p.Sidecar.Generation = p.Main.Generation
				if e = g.admitted(wait, p); e != nil {
					p.Close()
					return nil, e
				}
				// Keep the original worker lease through binding publication and
				// the positive constructor result, under this SAME locked pair.
				lease := p.Main.Health.ValidUntilNS
				if e = startupContextError(wait); e != nil {
					p.Close()
					return nil, e
				}
				now, e = LifecycleNow()
				if contextErr := startupContextError(wait); contextErr != nil {
					p.Close()
					return nil, contextErr
				}
				if e != nil {
					p.Close()
					return nil, e
				}
				if !p.currentRecords(g.boot, now) {
					p.Close()
					return nil, errLifecycleInvalid
				}
				if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
					p.Close()
					return nil, ErrLifecycle
				}
				owns = true
				e = p.WriteSidecar()
				if e != nil {
					p.Close()
					return nil, e
				}
				if e = startupContextError(wait); e != nil {
					p.Close()
					return nil, e
				}
				now, e = LifecycleNow()
				if contextErr := startupContextError(wait); contextErr != nil {
					p.Close()
					return nil, contextErr
				}
				if e != nil {
					p.Close()
					return nil, e
				}
				if !p.currentRecords(g.boot, now) {
					p.Close()
					return nil, errLifecycleInvalid
				}
				if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
					p.Close()
					return nil, ErrLifecycle
				}
				p.Close()
				return g, nil
			}
			p.Close()
			select {
			case <-wait.Done():
				return nil, ErrLifecycle
			case <-time.After(20 * time.Millisecond):
			}
			continue
		}
		if !p.Main.Clean && p.Main.Phase != "boot_prepared" || p.Sidecar.Outcome != "waiting" && !lifecycleOneOf(p.Sidecar.Outcome, "unstarted", "closed") && !(p.Main.Clean && p.Sidecar.Outcome == "admitted" && lifecycleDeparted(p.Sidecar.Binding)) ||
			p.Sidecar.Binding != nil && *p.Sidecar.Binding != self && !lifecycleDeparted(p.Sidecar.Binding) ||
			lifecycleBootCensus(wait, p, self, boot, false) != nil {
			p.Close()
			return nil, ErrLifecycle
		}
		p.Sidecar.Binding = &self
		p.Sidecar.Generation = nil
		p.Sidecar.Outcome = "waiting"
		p.Sidecar.CloseProof = "none"
		p.Sidecar.TerminalNS = nil
		owns = true
		e = p.WriteSidecar()
		if e != nil {
			p.Close()
			return nil, e
		}
		now, clockErr := LifecycleNow()
		if clockErr != nil {
			p.Close()
			return nil, clockErr
		}
		count := 0
		for _, spend := range p.Main.Rate.Spends {
			if now-spend < ManagedWindowNS {
				count++
			}
		}
		if p.Main.Clean && (count == 3 || p.Main.Rate.NotBeforeNS != nil && now < *p.Main.Rate.NotBeforeNS) {
			// Certify proactively INSIDE the original no-claim10s, rather than
			// treating the expired timer as evidence or renewing its window.
			g.unstartedPolicy = p.Main.Policy
			g.unstartedTransition = p.Main.BootTransitionID
			if !g.unstartedCurrent(wait, p) {
				p.Close()
				return nil, ErrLifecycle
			}
			g.unstarted = true
			g.veto.Store(true)
			g.cancel()
			p.Close()
			return g, ErrLifecycle
		}
		p.Close()
		select {
		case <-wait.Done():
			return nil, ErrLifecycle
		case <-time.After(20 * time.Millisecond):
		}
	}
}

// This disposition exists only before main has called Sidecar.Start. It is
// not a cleanup receipt: main must still Stop the SAME object and join, then
// Terminal repeats this proof within the ORIGINAL waiter deadline.
func (g *ManagedSidecarGuard) UnstartedDeadline() time.Time {
	if g.unstarted {
		return g.unstartedDeadline
	}
	return time.Time{}
}
func (g *ManagedSidecarGuard) unstartedCurrent(ctx context.Context, p *LifecyclePair) bool {
	bound, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || !time.Now().Before(g.unstartedDeadline) || bound.After(g.unstartedDeadline) {
		return false
	}
	matches := func() bool {
		boot, e := LifecycleBoot()
		now, clockErr := LifecycleNow()
		return e == nil && clockErr == nil && boot == g.boot && p.currentRecords(boot, now) && p.Eligible(boot) == nil &&
			LifecycleRunPolicyFollows(g.unstartedPolicy, p.Main.Policy) && p.Main.Clean && p.Main.Phase == "idle" &&
			p.Main.Generation == nil && p.Main.Wrapper == nil && p.Main.Launcher == nil && p.Main.Worker == nil && p.Main.Sidecar == nil &&
			p.Main.Health == nil && p.Main.Teardown == nil && p.Main.BootTransitionID == g.unstartedTransition &&
			p.Sidecar.Binding != nil && *p.Sidecar.Binding == g.identity && p.Sidecar.Generation == nil &&
			lifecycleOneOf(p.Sidecar.Outcome, "waiting", "closed") && !p.Sidecar.Halt && p.Sidecar.Failure == "none" && !g.failed.Load()
	}
	if !matches() || lifecycleBootCensus(ctx, p, g.identity, g.boot, false) != nil || p.Refresh() != nil {
		return false
	}
	return matches() && ctx.Err() == nil && time.Now().Before(g.unstartedDeadline)
}

// A failure has two independent facts: local refusal/withdrawal and an observed
// existing typed cause. Unknown failure never manufactures an I/O diagnosis.
func (g *ManagedSidecarGuard) FailureObserved() bool { return g.failed.Load() }
func (g *ManagedSidecarGuard) observe(ctx context.Context, err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, errLifecycleInhibited) {
		return g.inhibited()
	}
	cause := LifecycleFailureCause(err)
	var code uint32
	switch cause {
	case "io_failure":
		code = 1
	case "invalid_state":
		code = 2
	case "identity_loss":
		code = 3
	}
	if code != 0 {
		g.failureCause.CompareAndSwap(0, code)
		g.failed.Store(true)
	} else if g.ctx.Err() == nil && !errors.Is(err, context.Canceled) {
		g.failed.Store(true)
	}
	g.veto.Store(true)
	g.cancel()
	return ErrLifecycle
}
func (g *ManagedSidecarGuard) inhibited() error {
	g.halted.Store(true)
	g.veto.Store(true)
	g.cancel()
	return ErrLifecycle
}
func (g *ManagedSidecarGuard) admitted(ctx context.Context, p *LifecyclePair) error {
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil {
		return ErrLifecycle
	}
	if e := startupContextError(ctx); e != nil {
		return e
	}
	now, e := LifecycleNow()
	if e != nil {
		return e
	}
	if !p.currentRecords(g.boot, now) {
		return errLifecycleInvalid
	}
	if p.Sidecar.Failure != "none" {
		switch p.Sidecar.Failure {
		case "io_failure":
			return errLifecycleIO
		case "invalid_state":
			return errLifecycleInvalid
		case "identity_loss":
			return errLifecycleIdentity
		}
	}
	if p.Main.Policy.Desired != "run" || p.Sidecar.Halt {
		return g.inhibited()
	}
	if p.Eligible(g.boot) != nil || p.Main.Phase != "live" || p.Main.Generation == nil || *p.Main.Generation != g.generation ||
		p.Sidecar.Binding == nil || *p.Sidecar.Binding != g.identity || p.Sidecar.Generation == nil || *p.Sidecar.Generation != g.generation ||
		p.Sidecar.TerminalNS != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS <= now || p.Main.Wrapper == nil || p.Main.Worker == nil || p.Main.Launcher == nil {
		return ErrLifecycle
	}
	lease := p.Main.Health.ValidUntilNS
	if RunLifecyclePreflight(ctx) != nil {
		return ErrLifecycle
	}
	units, e := lifecycleManagerCurrent(ctx, true)
	if e != nil {
		return e
	}
	if !lifecycleBootService(ctx, "happyranch-tsnet-sidecar.service", units["happyranch-tsnet-sidecar.service"], &g.identity) {
		return ErrLifecycle
	}
	for _, role := range []struct {
		identity LifecycleProcess
		unit     string
		main     bool
	}{
		{*p.Main.Wrapper, "happyranch-connector.service", true},
		{g.identity, "happyranch-tsnet-sidecar.service", true},
	} {
		pin, e := PinLifecycleProcess(role.identity)
		if e != nil {
			return e
		}
		e = ValidateLifecyclePin(ctx, pin, role.unit, role.main)
		pin.Close()
		if e != nil {
			return e
		}
	}
	launcher, e := PinLifecycleProcess(*p.Main.Launcher)
	if e != nil {
		return e
	}
	defer launcher.Close()
	if e = ValidateLifecyclePin(ctx, launcher, "happyranch-connector.service", false); e != nil {
		return e
	}
	worker, e := PinLifecycleProcess(*p.Main.Worker)
	if e != nil {
		return e
	}
	defer worker.Close()
	if e = ValidateLifecycleWorker(ctx, worker, p.Main.Launcher.PID); e != nil {
		return e
	}
	if _, e = lifecycleManagerCurrent(ctx, true); e != nil {
		return e
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil {
		return ErrLifecycle
	}
	// Slow authority work consumes BOTH original bounds. Query availability
	// never renews the worker lease; equality is expiry, even with a live ctx.
	if e = startupContextError(ctx); e != nil {
		return e
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return contextErr
	}
	if e != nil {
		return e
	}
	if !p.currentRecords(g.boot, now) {
		return errLifecycleInvalid
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return ErrLifecycle
	}
	return nil
}
func (g *ManagedSidecarGuard) Admission(ctx context.Context, action func() error) error {
	if g.veto.Load() || g.failed.Load() {
		return ErrLifecycle
	}
	if e := startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	p, e := OpenLifecycle(ctx)
	if e != nil {
		return g.observe(ctx, e)
	}
	defer p.Close()
	if e = g.admitted(ctx, p); e != nil {
		return g.observe(ctx, e)
	}
	lease := p.Main.Health.ValidUntilNS
	var now int64
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	if e = action(); e != nil {
		return g.observe(ctx, e)
	}
	if e = g.admitted(ctx, p); e != nil {
		return g.observe(ctx, e)
	}
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	p.Sidecar.Outcome = "admitted"
	e = p.WriteSidecar()
	if e != nil {
		return g.observe(ctx, e)
	}
	// A timely admitted write can finish late; refusal cannot recall the write.
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	return nil
}
func (g *ManagedSidecarGuard) Check(ctx context.Context) error {
	if g.veto.Load() || g.failed.Load() {
		return ErrLifecycle
	}
	if e := startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	p, e := OpenLifecycle(ctx)
	if e != nil {
		return g.observe(ctx, e)
	}
	defer p.Close()
	if e = g.admitted(ctx, p); e != nil {
		return g.observe(ctx, e)
	}
	lease := p.Main.Health.ValidUntilNS
	var now int64
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	return nil
}
func (g *ManagedSidecarGuard) Terminal(ctx context.Context, halt bool, closed bool, failed bool) error {
	g.veto.Store(true)
	if failed {
		g.failed.Store(true)
	}
	p, e := OpenLifecycle(ctx)
	if e != nil {
		return g.observe(ctx, e)
	}
	defer p.Close()
	// Explicit inhibition retains the existing halted/physical-close path.
	// It is never acknowledged as a harmless rate-only waiter.
	if halt || g.halted.Load() || p.Main.Policy.Desired != "run" || p.Sidecar.Halt {
		g.unstarted = false
	}
	if g.unstarted && !closed {
		if failed || !g.unstartedCurrent(ctx, p) {
			g.failed.Store(true)
			return ErrLifecycle
		}
		return nil // no premature unknown/close publication before actual Stop
	}
	now, e := LifecycleNow()
	if e != nil {
		return e
	}
	if p.Sidecar.RuntimeBoot != g.boot || p.Sidecar.Binding == nil || *p.Sidecar.Binding != g.identity ||
		p.Main.RuntimeBoot != g.boot || p.Main.BootTransitionID != p.Sidecar.BootTransitionID ||
		g.generation != "" && (p.Sidecar.Generation == nil || *p.Sidecar.Generation != g.generation) {
		return errLifecycleIdentity
	}
	coherent := p.currentRecords(g.boot, now) && p.Main.Policy.HardBlock == "none" && p.Main.Block == "none"
	if g.unstarted && !g.unstartedCurrent(ctx, p) {
		coherent = false
	}
	if closed {
		// Positive cooperative close repeats current authority and own role;
		// an unavailable query never becomes a typed I/O diagnosis. Failure
		// fencing below still attempts only the already bound owned record.
		if RunLifecyclePreflight(ctx) != nil {
			coherent = false
		}
		own, e := PinLifecycleProcess(g.identity)
		if e != nil {
			coherent = false
		} else {
			if ValidateLifecyclePin(ctx, own, "happyranch-tsnet-sidecar.service", true) != nil {
				coherent = false
			}
			own.Close()
		}
		members, e := lifecycleCgroupMembers("happyranch-tsnet-sidecar.service")
		props, queryErr := LifecycleUnit(ctx, "happyranch-tsnet-sidecar.service")
		if e != nil || queryErr != nil || len(members) != 1 || members[0] != g.identity.PID || props["ControlPID"] != "0" || ctx.Err() != nil {
			coherent = false
		}
	}
	if g.unstarted && !g.unstartedCurrent(ctx, p) {
		coherent = false
	}
	if p.Sidecar.Failure != "none" {
		g.failed.Store(true)
	}
	halt = halt || g.halted.Load()
	if halt {
		p.Sidecar.Halt = true
	}
	if p.Sidecar.TerminalNS == nil {
		p.Sidecar.TerminalNS = &now
	}
	// Preserve the first available persisted cause, including one published by
	// an earlier writer. A failed Boolean is runtime uncertainty, not io_failure.
	if p.Sidecar.Failure == "none" {
		switch g.failureCause.Load() {
		case 1:
			p.Sidecar.Failure = "io_failure"
		case 2:
			p.Sidecar.Failure = "invalid_state"
		case 3:
			p.Sidecar.Failure = "identity_loss"
		}
	}
	if closed && !coherent {
		g.failed.Store(true)
	}
	if closed && coherent && !g.failed.Load() && p.Sidecar.Failure == "none" {
		p.Sidecar.Outcome = "closed"
		p.Sidecar.CloseProof = "cooperative"
	} else {
		p.Sidecar.Outcome = "unknown"
		p.Sidecar.CloseProof = "none"
	}
	e = p.WriteSidecar()
	if e != nil {
		g.observe(ctx, e)
	}
	owner := p.Main.Wrapper
	p.Close()
	// Failure of the fence does not skip the already authenticated peer attempt,
	// and neither successful signaling nor a later read proves durable refusal.
	if (halt || g.failed.Load()) && owner != nil {
		peer, err := PinLifecycleProcess(*owner)
		if err != nil {
			return ErrLifecycle
		}
		defer peer.Close()
		if ValidateLifecyclePin(ctx, peer, "happyranch-connector.service", true) != nil {
			return ErrLifecycle
		}
		sig := unix.SIGUSR2
		if halt {
			sig = unix.SIGTERM
		}
		if peer.Signal(ctx, sig) != nil {
			return ErrLifecycle
		}
	}
	if e != nil || g.failed.Load() {
		return ErrLifecycle
	}
	return nil
}
func (s *Sidecar) SetManagedGuard(g *ManagedSidecarGuard) { s.managed = g }
func (e *TSNetEngine) managedHealthy(ctx context.Context, c Config) bool {
	if e.server == nil {
		return false
	}
	status, err := e.server.Status(ctx)
	return err == nil && ctx.Err() == nil && status != nil && status.BackendState == "Running" && expectedPeerVisible(status, c.ExpectedPeers)
}

func IdentifyLifecycleProcess(pid int, invocation string) (*LifecyclePin, error) {
	if pid < 2 || pid > math.MaxInt32 || !lifecycleID(invocation) {
		return nil, ErrLifecycle
	}
	fd, err := unix.Open("/proc/"+strconv.Itoa(pid), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrLifecycle
	}
	pin := &LifecyclePin{proc: os.NewFile(uintptr(fd), "proc"), pidfd: -1, Identity: LifecycleProcess{PID: pid, Invocation: invocation}, expectedUID: lifecycleServiceUID()}
	pin.pidfd, err = unix.PidfdOpen(pid, 0)
	if err != nil {
		pin.Close()
		return nil, ErrLifecycle
	}
	raw, err := pin.read("stat", 4096)
	if err != nil {
		pin.Close()
		return nil, err
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		pin.Close()
		return nil, ErrLifecycle
	}
	fields := strings.Fields(string(raw[end+1:]))
	if len(fields) < 20 {
		pin.Close()
		return nil, ErrLifecycle
	}
	pin.Identity.StartTicks, err = strconv.ParseInt(fields[19], 10, 64)
	if err != nil || pin.Validate() != nil {
		pin.Close()
		return nil, ErrLifecycle
	}
	return pin, nil
}
func ValidateLifecycleWorker(ctx context.Context, worker *LifecyclePin, launcherPID int) error {
	if ValidateLifecyclePin(ctx, worker, "happyranch-connector.service", false) != nil {
		return ErrLifecycle
	}
	members, err := lifecycleCgroupMembers("happyranch-connector.service")
	if err != nil || len(members) > 8 {
		return ErrLifecycle
	}
	unit, err := LifecycleUnit(ctx, "happyranch-connector.service")
	if err != nil {
		return err
	}
	wrapperPID, err := strconv.Atoi(unit["MainPID"])
	if err != nil || wrapperPID < 2 {
		return ErrLifecycle
	}
	allowed := map[int]bool{os.Getpid(): true, wrapperPID: true, launcherPID: true, worker.Identity.PID: true}
	current := worker
	owned := []*LifecyclePin{}
	defer func() {
		for _, pin := range owned {
			pin.Close()
		}
	}()
	for depth := 0; depth < 3; depth++ {
		if current.Identity.PID == launcherPID {
			for _, pid := range members {
				if !allowed[pid] {
					return ErrLifecycle
				}
			}
			return nil
		}
		raw, err := current.read("stat", 4096)
		if err != nil {
			return err
		}
		end := bytes.LastIndexByte(raw, ')')
		if end < 0 {
			return ErrLifecycle
		}
		fields := strings.Fields(string(raw[end+1:]))
		if len(fields) < 2 {
			return ErrLifecycle
		}
		parent, err := strconv.Atoi(fields[1])
		if err != nil || parent < 2 || parent == wrapperPID || allowed[parent] && parent != launcherPID {
			return ErrLifecycle
		}
		next, err := IdentifyLifecycleProcess(parent, worker.Identity.Invocation)
		if err != nil {
			return err
		}
		owned = append(owned, next)
		allowed[parent] = true
		current = next
		if ValidateLifecyclePin(ctx, current, "happyranch-connector.service", false) != nil {
			return ErrLifecycle
		}
	}
	return ErrLifecycle
}

func CheckManagedWorker() int {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	pair, err := OpenLifecycle(ctx)
	if err != nil {
		return 78
	}
	defer pair.Close()
	boot, err := LifecycleBoot()
	if RunLifecyclePreflight(ctx) != nil {
		return 78
	}
	if _, e := lifecycleManagerCurrent(ctx, true); e != nil {
		return 78
	}
	if err != nil || pair.Eligible(boot) != nil || pair.Main.Phase != "live" || pair.Main.Generation == nil ||
		*pair.Main.Generation != os.Getenv("HAPPYRANCH_CHILD_HEALTH_GENERATION") || pair.Main.Launcher == nil || pair.Main.Wrapper == nil {
		return 78
	}
	launcher, e := PinLifecycleProcess(*pair.Main.Launcher)
	if e != nil {
		return 78
	}
	defer launcher.Close()
	if ValidateLifecyclePin(ctx, launcher, "happyranch-connector.service", false) != nil {
		return 78
	}
	worker, err := IdentifyLifecycleProcess(os.Getppid(), pair.Main.Wrapper.Invocation)
	if err != nil {
		return 78
	}
	defer worker.Close()
	if ValidateLifecycleWorker(ctx, worker, pair.Main.Launcher.PID) != nil {
		return 78
	}
	return 0
}

// The private package entry accepts only the two existing package operations.
// Fixed inherited descriptors are internal transports, never CLI FD/path/exec
// options: incoming package=3, incoming connector=4. The caller has verified the
// same closed package that supplies this executable. No runtime service uses it.
type lifecyclePackageRequest struct {
	Version     int    `json:"version"`
	Operation   string `json:"operation"`
	OperationID string `json:"operation_id"`
	DeadlineNS  int64  `json:"deadline_ns"`
}

func lifecycleEntryContext(input io.Reader, maximum time.Duration) (context.Context, context.CancelFunc, lifecyclePackageRequest, error) {
	entry := time.Now()
	raw, err := io.ReadAll(io.LimitReader(input, 4097))
	var request lifecyclePackageRequest
	if err != nil || len(raw) > 4096 {
		return nil, nil, request, ErrLifecycle
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var shape map[string]json.RawMessage
	if decoder.Decode(&request) != nil || json.Unmarshal(raw, &shape) != nil || len(shape) != 4 || request.Version != 2 || !lifecycleID(request.OperationID) {
		return nil, nil, request, ErrLifecycle
	}
	canonical, e := json.Marshal(shape)
	if e != nil || !bytes.Equal(raw, canonical) {
		return nil, nil, request, ErrLifecycle
	}
	sampled := time.Now()
	now, e := LifecycleNow()
	if e != nil || request.DeadlineNS <= now || request.DeadlineNS-now > int64(maximum) {
		return nil, nil, request, ErrLifecycle
	}
	deadline := sampled.Add(time.Duration(request.DeadlineNS - now))
	if limit := entry.Add(maximum); limit.Before(deadline) {
		deadline = limit
	}
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	return ctx, cancel, request, nil
}
func lifecycleStoppedCurrent(ctx context.Context, p *LifecyclePair) bool {
	boot, err := LifecycleBoot()
	now, clockErr := LifecycleNow()
	return err == nil && clockErr == nil && p.currentRecords(boot, now) && p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot && p.Main.BootTransitionID == p.Sidecar.BootTransitionID &&
		p.stopped() && p.Main.Policy.HardBlock == "none" && p.Main.Block == "none" &&
		lifecycleFixedAbsent(ctx) && ctx.Err() == nil
}
func lifecycleInheritedPair(ctx context.Context) (*LifecyclePair, error) {
	inherited := os.NewFile(3, "fixed-lifecycle-holder")
	var actual, named unix.Stat_t
	if inherited == nil || unix.Fstat(3, &actual) != nil || unix.Lstat(ManagedMainDirectory+"/"+managedLock, &named) != nil ||
		actual.Dev != named.Dev || actual.Ino != named.Ino || actual.Mode&unix.S_IFMT != unix.S_IFREG || actual.Mode&07777 != 0600 || actual.Nlink != 1 || actual.Size != 0 || actual.Uid != named.Uid {
		return nil, ErrLifecycle
	}
	// A distinct open description MUST be excluded, while the passed original
	// MUST retain the existing exclusive flock. Never LOCK_UN the original.
	separate, err := unix.Open(ManagedMainDirectory+"/"+managedLock, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrLifecycle
	}
	e := unix.Flock(separate, unix.LOCK_EX|unix.LOCK_NB)
	unix.Close(separate)
	if e != unix.EWOULDBLOCK && e != unix.EAGAIN {
		return nil, ErrLifecycle
	}
	if unix.Flock(3, unix.LOCK_EX|unix.LOCK_NB) != nil {
		return nil, ErrLifecycle
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		return nil, ErrLifecycle
	}
	p := &LifecyclePair{lock: inherited, deadline: deadline}
	p.mainDir, err = lifecycleDirectory(ManagedMainDirectory)
	if err != nil {
		p.Close()
		return nil, err
	}
	p.sideDir, err = lifecycleDirectory(ManagedSidecarDirectory)
	if err != nil {
		p.Close()
		return nil, err
	}
	if p.Refresh() != nil {
		p.Close()
		return nil, ErrLifecycle
	}
	return p, nil
}

// The thin fixed helper checks a borrowed lock; its parent retains the SAME
// OFD throughout its unchanged credential body and reload/error unwind.
func HandleLifecycleGuard(input io.Reader, kind string) int {
	maximum := 120 * time.Second
	if kind == "stopped" {
		maximum = 60 * time.Second
	}
	if kind == "package" {
		maximum = 180 * time.Second
	}
	ctx, cancel, request, err := lifecycleEntryContext(input, maximum)
	if err != nil {
		return 78
	}
	defer cancel()
	if request.Operation != kind {
		return 78
	}
	if kind == "run" {
		if _, e := lifecycleManagerCurrent(ctx, true); e != nil {
			return 78
		}
		startup, ok := lifecycleRunHelper(ctx)
		if !ok {
			return 78
		}
		sampled := time.Now()
		now, e := LifecycleNow()
		if e != nil || startup <= now {
			return 78
		}
		limited, stop := context.WithDeadline(ctx, sampled.Add(time.Duration(startup-now)))
		defer stop()
		ctx = limited
	}
	p, err := lifecycleInheritedPair(ctx)
	if err != nil {
		return 78
	}
	defer p.Close()
	if kind == "package" || kind == "stopped" {
		if os.Geteuid() != 0 || (kind == "package" && !lifecyclePackageCurrent(ctx, p, true) || kind == "stopped" && !lifecycleStoppedCurrent(ctx, p)) {
			return 78
		}
	} else if kind == "run" {
		boot, e := LifecycleBoot()
		if e != nil || !lifecyclePolicyEligible(p) || !lifecycleBootLinks(p) {
			return 78
		}
		if p.Main.RuntimeBoot == boot && p.Sidecar.RuntimeBoot == boot {
			if p.Eligible(boot) != nil {
				return 78
			}
		} else {
			if !p.Main.Policy.Enabled {
				return 78
			}
			now, e := LifecycleNow()
			if e != nil {
				return 78
			}
			if p.Main.RuntimeBoot == boot || p.Sidecar.RuntimeBoot == boot {
				if lifecycleState(p, boot, now) != "boot_prepared" {
					return 78
				}
			}
		}
		if p.Sidecar.RuntimeBoot == boot && p.Sidecar.Halt {
			return 78
		}
		if _, e := lifecycleManagerCurrent(ctx, true); e != nil {
			return 78
		}
	} else {
		return 78
	}
	if ctx.Err() != nil {
		return 78
	}
	return 0
}
func BootstrapLifecycle(ctx context.Context) (*LifecyclePair, error) {
	if os.Geteuid() != 0 || ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	for _, path := range []string{"/opt/happyranch", "/etc/systemd/system/happyranch-connector.service", "/etc/systemd/system/happyranch-tsnet-sidecar.service", "/etc/systemd/system/happyranch-managed.target", "/etc/systemd/system/happyranch-tsnet-sidecar.service.d", "/etc/systemd/system/happyranch-connector.service.d", "/etc/systemd/system/happyranch-managed.target.d"} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			return nil, ErrLifecycle
		}
	}
	entries, err := os.ReadDir("/")
	if err != nil {
		return nil, ErrLifecycle
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".happyranch-") {
			return nil, ErrLifecycle
		}
	}
	// A fresh explicit bootstrap has no installed/loaded image. Actual PID1
	// absence, jobs and cgroups are independent of those empty disk paths.
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target"} {
		if !lifecyclePackageManagerAbsent(ctx, unit, true) {
			return nil, ErrLifecycle
		}
		if unit != "happyranch-managed.target" && !LifecycleAbsent(ctx, unit, nil) {
			return nil, ErrLifecycle
		}
	}
	account, err := user.Lookup("happyranch")
	if err != nil {
		return nil, ErrLifecycle
	}
	uid, err := strconv.Atoi(account.Uid)
	if err != nil || uid <= 0 {
		return nil, ErrLifecycle
	}
	gid, err := strconv.Atoi(account.Gid)
	if err != nil || gid <= 0 {
		return nil, ErrLifecycle
	}
	parent, err := lifecycleDirectory("/var/lib")
	if err != nil {
		return nil, err
	}
	defer parent.Close()
	for _, path := range []string{ManagedMainDirectory, ManagedSidecarDirectory} {
		if _, e := os.Lstat(path); os.IsNotExist(e) {
			continue
		}
		dir, e := lifecycleDirectory(path)
		if e != nil {
			return nil, ErrLifecycle
		}
		names, e := dir.Readdirnames(128)
		dir.Close()
		if e != nil && e != io.EOF || len(names) == 128 {
			return nil, ErrLifecycle
		}
		for _, name := range names {
			if name == managedRecord || name == managedLock || strings.HasPrefix(name, ".lifecycle-") {
				return nil, ErrLifecycle
			}
		}
	}
	// Allocate the future stable inode anonymously on the SAME filesystem.
	// Acquire its OFD before any control-directory/policy mutation, then link
	// that exact held inode once at the fixed lock name. No second lock inode,
	// renamed lock, public path/FD option or temporary namespace is introduced.
	fd, err := unix.Openat(int(parent.Fd()), ".", unix.O_TMPFILE|unix.O_RDWR|unix.O_CLOEXEC, 0600)
	if err != nil {
		return nil, ErrLifecycle
	}
	p := &LifecyclePair{lock: os.NewFile(uintptr(fd), managedLock)}
	success := false
	defer func() {
		if !success {
			p.Close()
		}
	}()
	if unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB) != nil || p.lock.Chown(uid, gid) != nil || ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	// Establish the fixed discoverable inode immediately after acquiring its
	// parent. An exclusive mkdir loser does no mutation; an existing parent
	// receives no owner/mode repair. No policy or sidecar-directory mutation
	// precedes linking the held inode.
	st, e := os.Lstat(ManagedMainDirectory)
	if os.IsNotExist(e) {
		if unix.Mkdirat(int(parent.Fd()), filepath.Base(ManagedMainDirectory), 0700) != nil || os.Chown(ManagedMainDirectory, uid, gid) != nil {
			return nil, ErrLifecycle
		}
	} else if e != nil || !st.IsDir() || st.Mode().Perm() != 0700 {
		return nil, ErrLifecycle
	}
	p.mainDir, err = lifecycleDirectory(ManagedMainDirectory)
	if err != nil {
		return nil, err
	}
	for _, name := range []string{managedRecord, managedLock} {
		if _, e := os.Lstat(filepath.Join(ManagedMainDirectory, name)); !os.IsNotExist(e) {
			return nil, ErrLifecycle
		}
	}
	if ctx.Err() != nil || unix.Linkat(fd, "", int(p.mainDir.Fd()), managedLock, unix.AT_EMPTY_PATH) != nil || p.mainDir.Sync() != nil || parent.Sync() != nil {
		return nil, ErrLifecycle
	}
	st, e = os.Lstat(ManagedSidecarDirectory)
	if os.IsNotExist(e) {
		if ctx.Err() != nil || unix.Mkdirat(int(parent.Fd()), filepath.Base(ManagedSidecarDirectory), 0700) != nil || os.Chown(ManagedSidecarDirectory, uid, gid) != nil {
			return nil, ErrLifecycle
		}
	} else if e != nil || !st.IsDir() || st.Mode().Perm() != 0700 {
		return nil, ErrLifecycle
	}
	p.sideDir, err = lifecycleDirectory(ManagedSidecarDirectory)
	if err != nil {
		return nil, err
	}
	for _, dir := range []*os.File{p.mainDir, p.sideDir} {
		names, e := dir.Readdirnames(128)
		if e != nil && e != io.EOF || len(names) == 128 {
			return nil, ErrLifecycle
		}
		for _, name := range names {
			if name == managedRecord || name == managedLock && dir == p.sideDir || strings.HasPrefix(name, ".lifecycle-") {
				return nil, ErrLifecycle
			}
		}
	}
	if parent.Sync() != nil || ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	installation, err := NewLifecycleID()
	if err != nil {
		return nil, err
	}
	operation, err := NewLifecycleID()
	if err != nil {
		return nil, err
	}
	transition, err := NewLifecycleID()
	if err != nil {
		return nil, err
	}
	boot, err := LifecycleBoot()
	if err != nil {
		return nil, err
	}
	policy := LifecyclePolicy{InstallationID: installation, Revision: 1, OperationID: operation, Desired: "stopped", HardBlock: "none"}
	p.deadline, _ = ctx.Deadline()
	p.Main = LifecycleMain{Version: 2, Policy: policy, RuntimeBoot: boot, BootTransitionID: transition, Phase: "idle", Clean: true, Block: "none", Rate: LifecycleRate{Boot: boot, Spends: []int64{}}}
	p.Sidecar = LifecycleSidecar{Version: 2, Policy: policy, RuntimeBoot: boot, BootTransitionID: transition, Halt: true, Outcome: "unstarted", Failure: "none", CloseProof: "none"}
	if p.WriteSidecar() != nil || p.WriteMain() != nil || ctx.Err() != nil {
		return nil, ErrLifecycle
	}
	success = true
	return p, nil
}

// Targets do not expose service-only PID/invocation properties. The package
// absence reader requests the exact common property set for all three units.
func lifecyclePackageManagerAbsent(ctx context.Context, unit string, fresh bool) bool {
	if !lifecycleOneOf(unit, "happyranch-connector.service", "happyranch-tsnet-sidecar.service", "happyranch-managed.target") {
		return false
	}
	names := []string{"LoadState", "FragmentPath", "DropInPaths", "ActiveState", "SubState", "Job"}
	query, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	args := []string{"show", unit}
	for _, name := range names {
		args = append(args, "--property="+name)
	}
	cmd := exec.CommandContext(query, "/usr/bin/systemctl", args...)
	cmd.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C"}
	var output lifecycleOutput
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	if cmd.Run() != nil || query.Err() != nil || output.overflow {
		return false
	}
	raw := output.b.String()
	if !utf8.ValidString(raw) || !strings.HasSuffix(raw, "\n") {
		return false
	}
	values := map[string]string{}
	for _, line := range strings.Split(strings.TrimSuffix(raw, "\n"), "\n") {
		key, value, ok := strings.Cut(line, "=")
		if !ok || !lifecycleOneOf(key, names...) {
			return false
		}
		if _, duplicate := values[key]; duplicate {
			return false
		}
		for _, c := range line {
			if c < 32 || c == 127 {
				return false
			}
		}
		values[key] = value
	}
	if len(values) != len(names) || values["ActiveState"] != "inactive" || values["SubState"] != "dead" || values["Job"] != "" || values["DropInPaths"] != "" {
		return false
	}
	if fresh {
		return values["LoadState"] == "not-found" && values["FragmentPath"] == "" && ctx.Err() == nil
	}
	return (values["LoadState"] == "not-found" && values["FragmentPath"] == "" || values["LoadState"] == "loaded" && values["FragmentPath"] == "/etc/systemd/system/"+unit) && ctx.Err() == nil
}

// Package A/B use the SAME current pair and boot. Closed records are never a
// substitute for actual loaded configuration or fixed manager absence.
func lifecyclePackageCurrent(ctx context.Context, p *LifecyclePair, stopped bool) bool {
	boot, e := LifecycleBoot()
	now, clockErr := LifecycleNow()
	if e != nil || clockErr != nil || ctx.Err() != nil || p.Main.Policy != p.Sidecar.Policy || p.Main.Policy.HardBlock != "none" ||
		p.Main.RuntimeBoot != boot || p.Sidecar.RuntimeBoot != boot || p.Main.Rate.Boot != boot || p.Main.BootTransitionID != p.Sidecar.BootTransitionID ||
		p.Main.Block != "none" || p.Sidecar.Failure != "none" || p.Main.Phase == "boot_prepared" ||
		(p.Main.BootSource == nil) != (p.Sidecar.BootSource == nil) {
		return false
	}
	if p.Main.BootSource != nil && *p.Main.BootSource != *p.Sidecar.BootSource {
		return false
	}
	for _, n := range p.Main.Rate.Spends {
		if n > now {
			return false
		}
	}
	if p.Main.Rate.LastEndNS != nil && *p.Main.Rate.LastEndNS > now || p.Sidecar.TerminalNS != nil && *p.Sidecar.TerminalNS > now {
		return false
	}
	if !stopped {
		// A may inhibit a live current owner, but cannot take ownership from a
		// stale tuple/copied invocation. B below requires actual absence.
		if p.Main.Wrapper != nil {
			pin, e := PinLifecycleProcess(*p.Main.Wrapper)
			if e != nil {
				return false
			}
			e = ValidateLifecyclePin(ctx, pin, "happyranch-connector.service", true)
			pin.Close()
			if e != nil {
				return false
			}
		} else {
			props, e := LifecycleUnit(ctx, "happyranch-connector.service")
			if e != nil || props["MainPID"] != "0" || props["ControlPID"] != "0" {
				return false
			}
		}
		if p.Sidecar.Binding != nil && !lifecycleOneOf(p.Sidecar.Outcome, "closed", "unstarted") {
			pin, e := PinLifecycleProcess(*p.Sidecar.Binding)
			if e != nil {
				return false
			}
			e = ValidateLifecyclePin(ctx, pin, "happyranch-tsnet-sidecar.service", true)
			pin.Close()
			if e != nil {
				return false
			}
		}
		if p.Main.Sidecar != nil && (p.Sidecar.Binding == nil || *p.Main.Sidecar != *p.Sidecar.Binding) {
			return false
		}
		if p.Sidecar.Outcome == "admitted" && (p.Main.Generation == nil || p.Sidecar.Generation == nil || *p.Main.Generation != *p.Sidecar.Generation) {
			return false
		}
		for _, identity := range []*LifecycleProcess{p.Main.Launcher, p.Main.Worker} {
			if identity != nil {
				pin, e := PinLifecycleProcess(*identity)
				if e != nil {
					return false
				}
				e = ValidateLifecyclePin(ctx, pin, "happyranch-connector.service", false)
				pin.Close()
				if e != nil {
					return false
				}
			}
		}
		return ctx.Err() == nil
	}
	if !lifecycleStoppedCurrent(ctx, p) {
		return false
	}
	return lifecyclePackageManagerAbsent(ctx, "happyranch-managed.target", false)

}

func lifecyclePackageChild(ctx context.Context, p *LifecyclePair, incoming, connector, executable *os.File, request lifecyclePackageRequest, admission bool, output io.Writer) error {
	// Always execute the authenticated, retained input image, including when
	// recovery removed/replaced the active installed pathname.
	cmd := exec.CommandContext(ctx, "/proc/self/fd/5", "managed-private", request.Operation)
	cmd.ExtraFiles = []*os.File{p.lock, incoming, connector, executable}
	cmd.Env = append(os.Environ(), "HAPPYRANCH_LIFECYCLE_DEADLINE_NS="+strconv.FormatInt(request.DeadlineNS, 10))
	body := ""
	if admission {
		body = "package-admission\n"
	}
	cmd.Stdin = strings.NewReader(body)
	cmd.Stdout = output
	cmd.Stderr = io.Discard
	// Run joins the actual child and its owned standard-stream copies before
	// returning. There is no detached success or extra cleanup allowance.
	if cmd.Run() != nil || ctx.Err() != nil {
		return ErrLifecycle
	}
	return nil
}
func HandleLifecyclePackage(input io.Reader, output io.Writer) int {
	entry := time.Now()
	ctx, cancel, request, err := lifecycleEntryContext(input, 180*time.Second)
	if err != nil {
		return 78
	}
	defer cancel()
	if os.Geteuid() != 0 || !lifecycleOneOf(request.Operation, "package-install", "package-uninstall") {
		return 78
	}
	// A includes input authentication, bootstrap/acquisition and source/loaded
	// admission, never ten new seconds after a blocked OpenLifecycle.
	a, cancelA := context.WithDeadline(ctx, entry.Add(10*time.Second))
	defer cancelA()
	incoming := os.NewFile(3, "fixed-incoming-package")
	connector := os.NewFile(4, "fixed-incoming-connector")
	if request.Operation == "package-install" && lifecycleIncoming(incoming, connector) != nil {
		return 78
	}
	if a.Err() != nil {
		return 78
	}
	executable, err := os.Open("/proc/self/exe")
	if err != nil {
		return 78
	}
	defer executable.Close()
	var p *LifecyclePair
	if _, e := os.Lstat(ManagedMainDirectory + "/" + managedRecord); os.IsNotExist(e) {
		if request.Operation != "package-install" {
			return 78
		}
		p, err = BootstrapLifecycle(a)
	} else {
		p, err = OpenLifecycle(a)
	}
	if err != nil {
		return 78
	}
	defer func() {
		if p != nil {
			p.Close()
		}
	}()
	if !lifecyclePackageCurrent(a, p, false) || lifecyclePackageChild(a, p, incoming, connector, executable, request, true, io.Discard) != nil {
		return 78
	}
	if !p.stopped() {
		err = p.policyChange("stopped", p.Main.Policy.Enabled, lifecycleInhibitID(request.OperationID))
		p.Close()
		p = nil
		cancelA()
		if err != nil || ctx.Err() != nil {
			return 78
		}
		// A is released before handler progress; this intentional gap is
		// followed by a fresh B acquisition and complete independent proof.
		manager, cancelManager := context.WithTimeout(ctx, 35*time.Second)
		err = lifecycleManager(manager, "stop")
		cancelManager()
		if err != nil {
			return 78
		}
		p, err = OpenLifecycle(ctx)
		if err != nil {
			return 78
		}
	} else {
		if a.Err() != nil {
			return 78
		}
		cancelA()
	}
	p.deadline, _ = ctx.Deadline()
	if !lifecyclePackageCurrent(ctx, p, true) || lifecyclePackageChild(ctx, p, incoming, connector, executable, request, true, io.Discard) != nil {
		return 78
	}
	if request.Operation == "package-uninstall" && p.policyChange("retired", false, request.OperationID) != nil {
		return 78
	}
	if lifecyclePackageChild(ctx, p, incoming, connector, executable, request, false, output) != nil {
		return 78
	}
	if p.Refresh() != nil || !lifecyclePackageCurrent(ctx, p, true) {
		return 78
	}
	return 0
}

// LifecycleChildCensus keeps the held known roles, including a proven-dead
// launcher. New members must close bounded ancestry on that SAME live launcher;
// after its death only already-authenticated retained roles can be observed.
// The caller owns returned new pins and retains them through final absence.
func LifecycleChildCensus(ctx context.Context, owner LifecycleProcess, launcher *LifecyclePin, retained ...*LifecyclePin) ([]*LifecyclePin, error) {
	if ctx.Err() != nil || launcher == nil || launcher.Identity.Invocation != owner.Invocation {
		return nil, ErrLifecycle
	}
	wrapper, err := PinLifecycleProcess(owner)
	if err != nil {
		return nil, err
	}
	defer wrapper.Close()
	if ValidateLifecyclePin(ctx, wrapper, "happyranch-connector.service", true) != nil {
		return nil, ErrLifecycle
	}
	known := map[int]*LifecyclePin{launcher.Identity.PID: launcher}
	result := []*LifecyclePin{launcher}
	added := []*LifecyclePin{}
	refuse := func() ([]*LifecyclePin, error) {
		for _, pin := range added {
			pin.Close()
		}
		return nil, ErrLifecycle
	}
	for _, pin := range retained {
		if pin == nil || pin.Identity.Invocation != owner.Invocation || pin.Identity.PID == owner.PID {
			return refuse()
		}
		if prior, ok := known[pin.Identity.PID]; ok {
			if prior.Identity != pin.Identity {
				return refuse()
			}
			continue
		}
		known[pin.Identity.PID] = pin
		result = append(result, pin)
	}
	if len(known) > 7 {
		return refuse()
	}
	launcherDead := launcher.Dead()
	// Dead is a pidfd observation; failed Validate on a live pin stays unknown.
	for _, pin := range result {
		if !pin.Dead() && ValidateLifecyclePin(ctx, pin, "happyranch-connector.service", false) != nil {
			return refuse()
		}
	}
	members, err := lifecycleCgroupMembers("happyranch-connector.service")
	if err != nil {
		return refuse()
	}
	parents := map[int]int{}
	bytesRead := 0
	seenOwner := false
	newMember := map[int]bool{}
	for _, pid := range members {
		if pid == owner.PID {
			seenOwner = true
			continue
		}
		pin := known[pid]
		if pin != nil && pin.Dead() {
			return refuse()
		} // never reopen a reused PID
		if pin == nil {
			if launcherDead {
				return refuse()
			}
			pin, err = IdentifyLifecycleProcess(pid, owner.Invocation)
			if err != nil {
				return refuse()
			}
			added = append(added, pin)
			result = append(result, pin)
			known[pid] = pin
			newMember[pid] = true
			if len(known) > 7 || ValidateLifecyclePin(ctx, pin, "happyranch-connector.service", false) != nil {
				return refuse()
			}
		}
		raw, e := pin.read("stat", 4096)
		bytesRead += len(raw)
		if e != nil || bytesRead > 16384 {
			return refuse()
		}
		end := bytes.LastIndexByte(raw, ')')
		if end < 0 {
			return refuse()
		}
		fields := strings.Fields(string(raw[end+1:]))
		if len(fields) < 2 {
			return refuse()
		}
		parent, e := strconv.Atoi(fields[1])
		if e != nil {
			return refuse()
		}
		parents[pid] = parent
	}
	if !seenOwner {
		return refuse()
	}
	// A live role missing from the exact cgroup is not an absence receipt.
	for _, pin := range result {
		if !pin.Dead() {
			if _, ok := parents[pin.Identity.PID]; !ok {
				return refuse()
			}
		}
	}
	if !launcherDead && parents[launcher.Identity.PID] != owner.PID {
		return refuse()
	}
	for pid := range parents {
		if launcherDead && !newMember[pid] {
			continue
		} // earlier retained ancestry, not new adoption
		current := pid
		found := false
		for depth := 0; depth < 3; depth++ {
			if current == launcher.Identity.PID {
				found = true
				break
			}
			parent, ok := parents[current]
			if !ok || parent == current {
				break
			}
			current = parent
		}
		if !found {
			return refuse()
		}
	}
	// Repeat the wrapper/manager fence after the bounded scan, including expiry.
	if ctx.Err() != nil || ValidateLifecyclePin(ctx, wrapper, "happyranch-connector.service", true) != nil || ctx.Err() != nil {
		return refuse()
	}
	return result, nil
}
func LifecycleChildrenAbsent(ctx context.Context, owner LifecycleProcess, pins []*LifecyclePin) bool {
	if ctx.Err() != nil {
		return false
	}
	for _, pin := range pins {
		if pin == nil || pin.Identity.Invocation != owner.Invocation || !pin.Dead() {
			return false
		}
	}
	wrapper, err := PinLifecycleProcess(owner)
	if err != nil {
		return false
	}
	defer wrapper.Close()
	if ValidateLifecyclePin(ctx, wrapper, "happyranch-connector.service", true) != nil {
		return false
	}
	members, err := lifecycleCgroupMembers("happyranch-connector.service")
	return err == nil && len(members) == 1 && members[0] == owner.PID && ctx.Err() == nil
}

// Initial composite READY must tolerate the owned notify/start roles. Only
// qualification/status/reset demand both services already active and running.
// Use the existing fixed unit/job observations; do not change startup helpers.
func LifecycleReady(ctx context.Context, p *LifecyclePair, qualification bool) bool {
	boot, e := LifecycleBoot()
	now, clockErr := LifecycleNow()
	if e != nil || clockErr != nil || ctx.Err() != nil || p.Eligible(boot) != nil || p.Main.Phase != "live" || p.Main.Health == nil || p.Main.Health.ValidUntilNS <= now ||
		p.Sidecar.Outcome != "admitted" || p.Main.Sidecar == nil || p.Main.Worker == nil || p.Main.Wrapper == nil || p.Main.Launcher == nil || RunLifecyclePreflight(ctx) != nil {
		return false
	}
	policy, transition, generation, health := p.Main.Policy, p.Main.BootTransitionID, *p.Main.Generation, *p.Main.Health
	wrapperIdentity, launcherIdentity, workerIdentity, sideIdentity := *p.Main.Wrapper, *p.Main.Launcher, *p.Main.Worker, *p.Main.Sidecar
	managerCurrent := func() bool {
		units, e := lifecycleManagerCurrent(ctx, !qualification)
		if e != nil {
			return false
		}
		if qualification {
			for _, role := range []struct {
				unit     string
				identity LifecycleProcess
			}{
				{"happyranch-connector.service", wrapperIdentity},
				{"happyranch-tsnet-sidecar.service", sideIdentity},
			} {
				props := units[role.unit]
				if props["ActiveState"] != "active" || props["SubState"] != "running" || props["ControlPID"] != "0" || props["Job"] != "" ||
					props["MainPID"] != strconv.Itoa(role.identity.PID) || props["InvocationID"] != role.identity.Invocation || props["ControlGroup"] != lifecycleCgroup(role.unit) {
					return false
				}
			}
			target := units["happyranch-managed.target"]
			if target["ActiveState"] != "active" || target["SubState"] != "active" || target["Job"] != "" {
				return false
			}
		}
		return true
	}
	if !managerCurrent() {
		return false
	}
	for _, role := range []struct {
		identity *LifecycleProcess
		unit     string
		main     bool
	}{
		{p.Main.Wrapper, "happyranch-connector.service", true},
		{p.Main.Sidecar, "happyranch-tsnet-sidecar.service", true},
	} {
		pin, e := PinLifecycleProcess(*role.identity)
		if e != nil {
			return false
		}
		e = ValidateLifecyclePin(ctx, pin, role.unit, role.main)
		pin.Close()
		if e != nil {
			return false
		}
		props, e := LifecycleUnit(ctx, role.unit)
		if e != nil || props["ControlPID"] != "0" {
			return false
		}
	}
	launcher, e := PinLifecycleProcess(*p.Main.Launcher)
	if e != nil {
		return false
	}
	defer launcher.Close()
	if ValidateLifecyclePin(ctx, launcher, "happyranch-connector.service", false) != nil {
		return false
	}
	worker, e := PinLifecycleProcess(*p.Main.Worker)
	if e != nil {
		return false
	}
	defer worker.Close()
	if ValidateLifecycleWorker(ctx, worker, p.Main.Launcher.PID) != nil {
		return false
	}
	if !managerCurrent() {
		return false
	}
	// Refresh under the SAME held lock and original bound after independent
	// process/authority reads. No pre-lock or pre-query snapshot grants reset.
	if p.Refresh() != nil {
		return false
	}
	now, e = LifecycleNow()
	return e == nil && p.Eligible(boot) == nil && p.Main.Phase == "live" && p.Main.Policy == policy && p.Main.BootTransitionID == transition &&
		p.Main.Generation != nil && *p.Main.Generation == generation && p.Main.Health != nil && *p.Main.Health == health && health.ValidUntilNS > now &&
		p.Main.Wrapper != nil && *p.Main.Wrapper == wrapperIdentity && p.Main.Launcher != nil && *p.Main.Launcher == launcherIdentity &&
		p.Main.Worker != nil && *p.Main.Worker == workerIdentity && p.Main.Sidecar != nil && *p.Main.Sidecar == sideIdentity &&
		p.Sidecar.Outcome == "admitted" && p.Sidecar.Generation != nil && *p.Sidecar.Generation == generation && ctx.Err() == nil
	// Actual engine/peer/listener monitoring remains sidecar-local. Under
	// selected THR228579 an uncommunicated loss can still permit rate reset;
	// this predicate certifies only the current existing observations.
}

func LifecycleEmptyOwner(ctx context.Context, p *LifecyclePair, self LifecycleProcess) bool {
	boot, e := LifecycleBoot()
	if e != nil || p.Eligible(boot) != nil || !p.Main.Clean {
		return false
	}
	return lifecycleBootCensus(ctx, p, self, boot, true) == nil && ctx.Err() == nil
}

func (g *ManagedSidecarGuard) Forward(ctx context.Context, action func() error) error {
	if g.veto.Load() || g.failed.Load() {
		return ErrLifecycle
	}
	if e := startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	p, e := OpenLifecycle(ctx)
	if e != nil {
		return g.observe(ctx, e)
	}
	defer p.Close()
	if e = g.admitted(ctx, p); e != nil {
		return g.observe(ctx, e)
	}
	if p.Sidecar.Outcome != "admitted" {
		return g.observe(ctx, errLifecycleInvalid)
	}
	lease := p.Main.Health.ValidUntilNS
	var now int64
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	if e = action(); e != nil {
		return e
	}
	if e = g.admitted(ctx, p); e != nil {
		return g.observe(ctx, e)
	}
	if e = startupContextError(ctx); e != nil {
		return g.observe(ctx, e)
	}
	now, e = LifecycleNow()
	if contextErr := startupContextError(ctx); contextErr != nil {
		return g.observe(ctx, contextErr)
	}
	if e != nil {
		return g.observe(ctx, e)
	}
	if !p.currentRecords(g.boot, now) {
		return g.observe(ctx, errLifecycleInvalid)
	}
	if g.veto.Load() || g.failed.Load() || g.ctx.Err() != nil || p.Main.Health == nil || p.Main.Health.ValidUntilNS != lease || lease <= now {
		return g.observe(ctx, ErrLifecycle)
	}
	return nil
}

func lifecycleInhibitID(id string) string {
	sum := sha256.Sum256([]byte("managed-inhibit:" + id))
	return hex.EncodeToString(sum[:16])
}
func lifecycleBootIdentity() error {
	f, err := os.Open("/etc/happyranch/sidecar.json")
	if err != nil {
		return ErrLifecycle
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil || len(raw) > 4096 {
		return ErrLifecycle
	}
	var c Config
	if json.Unmarshal(raw, &c) != nil || c.Validate() != nil || c.StateDir != ManagedSidecarDirectory {
		return ErrLifecycle
	}
	c.CredentialFile = "/etc/happyranch/enrollment.key"
	if _, err = os.Lstat(filepath.Join(c.StateDir, consumedMarker)); err != nil {
		return ErrLifecycle
	}
	return ManagedIdentityPreflight(c)
}

// Validate the fixed incoming image before the gate can execute its connector.
// This is integrity/type/compatibility evidence within the intended reviewed
// package-input boundary; it is not a nonsecret certificate for arbitrary input.
func lifecycleIncoming(packageFile, connector *os.File) error {
	if _, err := packageFile.Seek(0, io.SeekStart); err != nil {
		return ErrLifecycle
	}
	expected := map[string]int64{
		"bin/happyranch-tsnet-sidecar": 0755, "bin/happyranch-connector": 0755,
		"share/happyranch.whl": 0644, "share/dependency-inventory.json": 0644,
		"share/sbom.cdx.json": 0644, "share/THIRD_PARTY_NOTICES.md": 0644,
		"systemd/happyranch-connector.service": 0644, "systemd/happyranch-tsnet-sidecar.service": 0644,
		"systemd/happyranch-managed.target": 0644, "manifest.json": 0644,
	}
	hashes := map[string]string{}
	var manifest []byte
	total := int64(0)
	reader := tar.NewReader(io.LimitReader(packageFile, 512<<20))
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return ErrLifecycle
		}
		name := strings.TrimPrefix(header.Name, "happyranch-linux-amd64/")
		mode, ok := expected[name]
		if !ok || name == header.Name || header.Typeflag != tar.TypeReg || header.Mode != mode || header.Uid != 0 || header.Gid != 0 || header.Uname != "root" || header.Gname != "root" || header.Size <= 0 || header.Size > 256<<20 {
			return ErrLifecycle
		}
		if _, seen := hashes[name]; seen {
			return ErrLifecycle
		}
		total += header.Size
		if total > 512<<20 {
			return ErrLifecycle
		}
		hash := sha256.New()
		if name == "manifest.json" {
			if header.Size > 16384 {
				return ErrLifecycle
			}
			manifest, err = io.ReadAll(reader)
			if err != nil || int64(len(manifest)) != header.Size {
				return ErrLifecycle
			}
			hash.Write(manifest)
		} else {
			count, err := io.Copy(hash, reader)
			if err != nil || count != header.Size {
				return ErrLifecycle
			}
		}
		hashes[name] = hex.EncodeToString(hash.Sum(nil))
	}
	if len(hashes) != len(expected) {
		return ErrLifecycle
	}
	var m struct {
		SchemaVersion int            `json:"schema_version"`
		Version       string         `json:"version"`
		Architecture  string         `json:"architecture"`
		Count         int            `json:"sidecar_dependency_count"`
		Contract      map[string]int `json:"managed_lifecycle_contract"`
		Files         []struct {
			Path   string `json:"path"`
			SHA256 string `json:"sha256"`
			Mode   string `json:"mode"`
		} `json:"files"`
	}
	decoder := json.NewDecoder(bytes.NewReader(manifest))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&m) != nil || m.SchemaVersion != 1 || m.Version == "" || m.Architecture != "linux-amd64" || m.Count <= 0 || len(m.Files) != 9 ||
		len(m.Contract) != 3 || m.Contract["lifecycle"] != 2 || m.Contract["child_health"] != 2 || m.Contract["recovery_diagnostics"] != 2 {
		return ErrLifecycle
	}
	seen := map[string]bool{}
	for _, entry := range m.Files {
		if seen[entry.Path] || entry.Path == "manifest.json" || hashes[entry.Path] != entry.SHA256 || entry.Mode != fmt.Sprintf("0o%o", expected[entry.Path]) {
			return ErrLifecycle
		}
		seen[entry.Path] = true
	}
	if _, err := connector.Seek(0, io.SeekStart); err != nil {
		return ErrLifecycle
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, io.LimitReader(connector, 256<<20)); err != nil || hex.EncodeToString(hash.Sum(nil)) != hashes["bin/happyranch-connector"] {
		return ErrLifecycle
	}
	self, err := os.Open("/proc/self/exe")
	if err != nil {
		return ErrLifecycle
	}
	defer self.Close()
	hash = sha256.New()
	if _, err = io.Copy(hash, io.LimitReader(self, 256<<20)); err != nil || hex.EncodeToString(hash.Sum(nil)) != hashes["bin/happyranch-tsnet-sidecar"] {
		return ErrLifecycle
	}
	if _, err = packageFile.Seek(0, io.SeekStart); err != nil {
		return ErrLifecycle
	}
	if _, err = connector.Seek(0, io.SeekStart); err != nil {
		return ErrLifecycle
	}
	return nil
}

func lifecycleServiceUID() uint32 {
	account, err := user.Lookup("happyranch")
	if err != nil {
		return ^uint32(0)
	}
	uid, err := strconv.ParseUint(account.Uid, 10, 32)
	if err != nil || uid == 0 {
		return ^uint32(0)
	}
	return uint32(uid)
}

// A run-only privileged pre/post body must be the actual current selected
// manager ControlPID's bounded descendant, never an external copied environment.
func lifecycleRunHelper(ctx context.Context) (int64, bool) {
	invocation := os.Getenv("INVOCATION_ID")
	if !lifecycleID(invocation) {
		return 0, false
	}
	properties, err := LifecycleUnit(ctx, "happyranch-tsnet-sidecar.service", "InactiveExitTimestampMonotonic")
	if err != nil || properties["InvocationID"] != invocation || properties["ControlGroup"] != lifecycleCgroup("happyranch-tsnet-sidecar.service") {
		return 0, false
	}
	stamp := properties["InactiveExitTimestampMonotonic"]
	activated, e := strconv.ParseInt(stamp, 10, 64)
	if e != nil || activated <= 0 || strconv.FormatInt(activated, 10) != stamp || activated > (math.MaxInt64-int64(120*time.Second))/1000 {
		return 0, false
	}
	sampled := time.Now()
	now, e := LifecycleNow()
	startup := activated*1000 + int64(120*time.Second)
	if e != nil || activated*1000 > now || startup <= now {
		return 0, false
	}
	limited, cancel := context.WithDeadline(ctx, sampled.Add(time.Duration(startup-now)))
	defer cancel()
	ctx = limited
	control, err := strconv.Atoi(properties["ControlPID"])
	if err != nil || control < 2 {
		return 0, false
	}
	pid := os.Getpid()
	total := 0
	for depth := 0; depth < 3; depth++ {
		fd, err := unix.Open("/proc/"+strconv.Itoa(pid), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		if err != nil {
			return 0, false
		}
		pin := &LifecyclePin{proc: os.NewFile(uintptr(fd), "fixed-helper"), pidfd: -1, Identity: LifecycleProcess{PID: pid, Invocation: invocation}, expectedUID: uint32(os.Geteuid())}
		pin.pidfd, err = unix.PidfdOpen(pid, 0)
		if err != nil {
			pin.Close()
			return 0, false
		}
		raw, e := pin.read("stat", 4096)
		total += len(raw)
		if e != nil || total > 16384 {
			pin.Close()
			return 0, false
		}
		end := bytes.LastIndexByte(raw, ')')
		if end < 0 {
			pin.Close()
			return 0, false
		}
		fields := strings.Fields(string(raw[end+1:]))
		if len(fields) < 20 {
			pin.Close()
			return 0, false
		}
		pin.Identity.StartTicks, e = strconv.ParseInt(fields[19], 10, 64)
		if e != nil || pin.Validate() != nil {
			pin.Close()
			return 0, false
		}
		group, e := pin.read("cgroup", 4096)
		if e != nil || string(group) != "0::"+lifecycleCgroup("happyranch-tsnet-sidecar.service")+"\n" {
			pin.Close()
			return 0, false
		}
		executable, e := unix.Openat(int(pin.proc.Fd()), "exe", unix.O_PATH|unix.O_CLOEXEC, 0)
		if e != nil {
			pin.Close()
			return 0, false
		}
		expected := "/opt/happyranch/bin/happyranch-connector"
		if depth == 0 {
			expected = "/opt/happyranch/bin/happyranch-tsnet-sidecar"
		}
		installed, e := unix.Open(expected, unix.O_PATH|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0)
		var a, b unix.Stat_t
		same := e == nil && unix.Fstat(executable, &a) == nil && unix.Fstat(installed, &b) == nil && a.Ino == b.Ino && a.Dev == b.Dev
		unix.Close(executable)
		if installed >= 0 {
			unix.Close(installed)
		}
		if !same {
			pin.Close()
			return 0, false
		}
		if pid == control {
			pin.Close()
			again, e := LifecycleUnit(ctx, "happyranch-tsnet-sidecar.service", "InactiveExitTimestampMonotonic")
			return startup, e == nil && again["InactiveExitTimestampMonotonic"] == stamp && again["InvocationID"] == invocation && again["ControlPID"] == properties["ControlPID"] && again["MainPID"] == properties["MainPID"] && ctx.Err() == nil
		}
		pid, e = strconv.Atoi(fields[1])
		pin.Close()
		if e != nil || pid < 2 {
			return 0, false
		}
	}
	return 0, false
}
