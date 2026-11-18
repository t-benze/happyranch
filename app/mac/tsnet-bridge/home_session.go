package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
	"tailscale.com/ipn/store/mem"
	"tailscale.com/logtail"
	"tailscale.com/tsnet"
)

// The session mode accepts the v2 trust tuple, but no saved profile or recovery.
// Add the internal absolute placeholder only AFTER checking forbidden keys;
// the existing exact parser and validator still own every trust field.
func readHomeSessionLaunch(in io.Reader) (homeLaunch, error) {
	var size uint32
	if binary.Read(in, binary.BigEndian, &size) != nil || size == 0 || size > 16384 {
		return homeLaunch{}, errHomeTrust
	}
	data := make([]byte, size)
	defer clear(data)
	if _, err := io.ReadFull(in, data); err != nil {
		return homeLaunch{}, errHomeTrust
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	if token, err := dec.Token(); err != nil || token != json.Delim('{') {
		return homeLaunch{}, errHomeTrust
	}
	for dec.More() {
		token, err := dec.Token()
		name, ok := token.(string)
		if err != nil || !ok || strings.EqualFold(name, "stateDirectory") || strings.EqualFold(name, "recovery") {
			return homeLaunch{}, errHomeTrust
		}
		var value json.RawMessage
		if dec.Decode(&value) != nil {
			return homeLaunch{}, errHomeTrust
		}
	}
	// exactHomeObject rejects duplicates, aliases, nulls, missing/extra fields
	// and trailing data after this prefix is added. No caller supplies a path.
	data = bytes.TrimSpace(data)
	normalized := append([]byte(`{"stateDirectory":"/",`), data[1:]...)
	defer clear(normalized)
	if !exactHomeObject(normalized, false) {
		return homeLaunch{}, errHomeTrust
	}
	var cfg homeLaunch
	if json.Unmarshal(normalized, &cfg) != nil || cfg.JoinKey == "" {
		return homeLaunch{}, errHomeTrust
	}
	if _, err := validateHomeLaunch(cfg); err != nil {
		return homeLaunch{}, errHomeTrust
	}
	return cfg, nil
}

// XDG_RUNTIME_DIR must be literal, current-user-owned and exactly 0700.
// Walk every component without following links, including aliased parents.
func homeSessionScratch() (string, error) {
	root := os.Getenv("XDG_RUNTIME_DIR")
	if !filepath.IsAbs(root) || filepath.Clean(root) != root || root == "/" {
		return "", errHomeTrust
	}
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return "", errHomeTrust
	}
	for _, part := range strings.Split(strings.TrimPrefix(root, "/"), "/") {
		next, e := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		unix.Close(fd)
		if e != nil {
			return "", errHomeTrust
		}
		fd = next
	}
	defer unix.Close(fd)
	var st unix.Stat_t
	if unix.Fstat(fd, &st) != nil || st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0700 {
		return "", errHomeTrust
	}
	dir, err := os.MkdirTemp(root, "happyranch-home-session-")
	if err != nil {
		return "", errHomeTrust
	}
	if unix.Lstat(dir, &st) != nil || st.Mode&unix.S_IFMT != unix.S_IFDIR || st.Mode&07777 != 0700 || st.Uid != uint32(os.Geteuid()) {
		_ = os.RemoveAll(dir)
		return "", errHomeTrust
	}
	return dir, nil
}

type homeSession struct {
	engine *managedEngine
	dir    string
}

// This owner never opens managedProfile, writes a marker or restarts from disk.
func newHomeSession(cfg homeLaunch) (*homeSession, error) {
	if cfg.Recovery != nil || cfg.JoinKey == "" {
		return nil, errHomeTrust
	}
	if _, err := validateHomeLaunch(cfg); err != nil {
		return nil, errHomeTrust
	}
	opMu.Lock()
	defer opMu.Unlock()
	srvMu.Lock()
	busy := srv != nil || managed != nil || pendingCloses != 0
	srvMu.Unlock()
	if busy {
		return nil, errManagedState
	}
	dir, err := homeSessionScratch()
	if err != nil {
		return nil, err
	}
	logtail.Disable()
	ctx, cancel := context.WithCancel(context.Background())
	m := &managedEngine{ctx: ctx, cancel: cancel}
	m.server = &tsnet.Server{Store: new(mem.Store), Ephemeral: true, Dir: dir,
		AuthKey: cfg.JoinKey, ControlURL: cfg.ControlURL, Hostname: cfg.Hostname,
		Logf: func(string, ...any) {}, UserLogf: func(string, ...any) {}}
	srvMu.Lock()
	engineGeneration++
	m.generation = engineGeneration
	managed, srv, dialCtx, dialCxl = m, m.server, ctx, cancel
	srvMu.Unlock()
	return &homeSession{engine: m, dir: dir}, nil
}

func (s *homeSession) up() int {
	opMu.Lock()
	defer opMu.Unlock()
	m := s.engine
	ctx, cancel := context.WithTimeout(m.ctx, 120*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return managedCanceled
	}
	m.started = true // Start may partially initialize; Close owns every result.
	if m.server.Start() != nil {
		return contextResult(ctx, managedBackend)
	}
	st, err := m.server.Up(ctx)
	if err != nil || st == nil || st.BackendState != "Running" || len(st.TailscaleIPs) == 0 || st.Self == nil || st.Self.ID == "" {
		return contextResult(ctx, managedBackend)
	}
	sys := m.server.Sys()
	eng, ok := sys.Engine.GetOK()
	d, dok := sys.Dialer.GetOK()
	lc, err := m.server.LocalClient()
	if !ok || eng == nil || !dok || d == nil || d.NetstackDialTCP == nil || err != nil || lc == nil {
		return managedUnavailable
	}
	srvMu.Lock()
	defer srvMu.Unlock()
	if ctx.Err() != nil || engineGeneration != m.generation {
		return managedCanceled
	}
	m.selfNodeID = string(st.Self.ID)
	m.health, m.engine, m.dialTCP, m.ready = newManagedHealth(lc), eng, d.NetstackDialTCP, true
	return 0
}

func (s *homeSession) Close() error {
	cancelManaged()
	opMu.Lock()
	defer opMu.Unlock()
	closed := s.engine.closeServer()
	// Remove non-credential log scratch even if upstream Close reports an error.
	err := os.RemoveAll(s.dir)
	if !closed || err != nil {
		return errManagedState
	}
	srvMu.Lock()
	if managed == s.engine {
		managed, srv, dialCtx, dialCxl = nil, nil, nil, nil
	}
	srvMu.Unlock()
	return nil
}

func homeSessionMain(in io.ReadCloser, out io.Writer) {
	defer in.Close()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGHUP, syscall.SIGINT, syscall.SIGTERM, syscall.SIGQUIT)
	defer stop()
	// Also interrupt a partial launch frame; no scratch exists at that point.
	interrupt := context.AfterFunc(ctx, func() { _ = in.Close() })
	defer interrupt()
	if code := runHomeSession(ctx, in, out); code != 0 {
		fmt.Fprintln(out, code)
	}
}

func runHomeSession(ctx context.Context, in io.Reader, out io.Writer) (code int) {
	cfg, err := readHomeSessionLaunch(in)
	if err != nil {
		return homeTrustRefused
	}
	session, err := newHomeSession(cfg)
	cfg.JoinKey = ""
	if err != nil {
		return homeTrustRefused
	}
	transport, err := prepareHomeProcess(cfg)
	if err != nil {
		_ = session.Close()
		return homeTrustRefused
	}
	return session.serve(ctx, in, out, transport, cfg)
}

func (session *homeSession) serve(ctx context.Context, in io.Reader, out io.Writer, transport *homeTransport, cfg homeLaunch) (code int) {
	defer func() {
		if session.Close() != nil {
			code = managedStateError
		}
	}()
	defer transport.Close()
	cancel := context.AfterFunc(ctx, func() { cancelManaged(); transport.Close() })
	defer cancel()
	// EOF closes admission during Up as well as while relaying. Never read
	// another launch object or accept a replacement credential in this process.
	go func() { _, _ = io.Copy(io.Discard, in); cancelManaged(); transport.Close() }()
	if code = session.up(); code != 0 {
		return code
	}
	listener, err := homeListener(cfg.SocketPath)
	if err != nil {
		return managedStateError
	}
	defer listener.Close()
	if writeHomeReady(out, session.engine.selfNodeID) != nil {
		return managedStateError
	}
	host, _, _ := net.SplitHostPort(cfg.Target)
	serveHomeRuntime(transport.ctx, listener, host)
	return 0
}
