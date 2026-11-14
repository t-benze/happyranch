package main

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"

	"tailscale.com/logtail"
	"tailscale.com/net/tsaddr"
	"tailscale.com/tsnet"
	"tailscale.com/types/key"
	"tailscale.com/wgengine"
)

const (
	managedInvalid     = -10
	managedBusy        = -11
	managedCanceled    = -12
	managedDeadline    = -13
	managedBackend     = -14
	managedUnavailable = -15
	managedStateError  = -16
	managedUnsafe      = -17
	managedPeerChanged = -18
)

// opMu owns Start/Up/Close; srvMu separately owns cancellation and generations.
// Never hold srvMu across Start, Up, Close, Status or a blocking dial.
var opMu sync.Mutex
var managed *managedEngine
var engineGeneration uint64
var ambientKeys = []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"}

type managedEngine struct {
	server                                 *tsnet.Server
	profile                                *managedProfile
	ctx                                    context.Context
	cancel                                 context.CancelFunc
	generation                             uint64
	started, attempted, ready, closeFailed bool
	engine                                 wgengine.Engine
	dialTCP                                func(context.Context, netip.AddrPort) (net.Conn, error)
	pinned                                 key.NodePublic
}

type managedConnection struct {
	net.Conn
	owner      *managedEngine
	generation uint64
	host       string
	ip         netip.Addr
	peer       key.NodePublic
}

func managedResult(code int) int {
	messages := map[int]string{0: "", -10: "Invalid managed configuration or ambient override", -11: "Managed engine busy", -12: "Managed operation canceled", -13: "Managed operation deadline exceeded", -14: "Managed backend failed", -15: "Managed backend unavailable", -16: "Managed state or close failed", -17: "Managed target must be a current tailnet peer", -18: "Managed peer unavailable or changed; reconnect"}
	setLastError(messages[code])
	return code
}
func ambientOverride() bool {
	for _, name := range ambientKeys {
		if _, ok := os.LookupEnv(name); ok {
			return true
		}
	}
	return false
}
func hasControl(s string) bool { return strings.IndexFunc(s, unicode.IsControl) >= 0 }
func validHostname(s string) bool {
	if len(s) == 0 || len(s) > 63 || s[0] == '-' || s[len(s)-1] == '-' {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-') {
			return false
		}
	}
	return true
}

func managedInit(authKey, controlURL, hostname, stateDir string) int {
	if !opMu.TryLock() {
		return managedResult(managedBusy)
	}
	defer opMu.Unlock()
	srvMu.Lock()
	busy := srv != nil || managed != nil
	srvMu.Unlock()
	if busy {
		return managedResult(managedBusy)
	}
	u, err := url.Parse(controlURL)
	if err != nil || ambientOverride() || hasControl(controlURL+hostname+authKey) || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || strings.Contains(controlURL, "#") || (u.Path != "" && u.Path != "/") || !validHostname(hostname) {
		return managedResult(managedInvalid)
	}
	if port := u.Port(); port != "" {
		if n, err := strconv.Atoi(port); err != nil || n < 1 || n > 65535 {
			return managedResult(managedInvalid)
		}
	}
	u.Host = strings.ToLower(u.Host)
	u.Path = ""
	profile, err := openManagedProfile(stateDir, hostname, u.String(), authKey != "")
	if err != nil {
		return managedResult(managedStateError)
	}
	logtail.Disable()
	ctx, cancel := context.WithCancel(context.Background())
	m := &managedEngine{profile: profile, ctx: ctx, cancel: cancel}
	m.server = m.newServer(authKey)
	srvMu.Lock()
	engineGeneration++
	m.generation = engineGeneration
	managed = m
	srv = m.server
	dialCtx = ctx
	dialCxl = cancel
	srvMu.Unlock()
	return managedResult(0)
}
func (m *managedEngine) newServer(authKey string) *tsnet.Server {
	return &tsnet.Server{AuthKey: authKey, Dir: m.profile.path, ControlURL: m.profile.record.ControlURL, Hostname: m.profile.record.Hostname, Ephemeral: false, Logf: func(string, ...any) {}, UserLogf: func(string, ...any) {}}
}
func contextResult(ctx context.Context, fallback int) int {
	if errors.Is(ctx.Err(), context.Canceled) {
		return managedCanceled
	}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return managedDeadline
	}
	return fallback
}
func managedUp(timeoutMs int) int {
	if !opMu.TryLock() {
		return managedResult(managedBusy)
	}
	defer opMu.Unlock()
	srvMu.Lock()
	m := managed
	srvMu.Unlock()
	if m == nil {
		return managedResult(managedUnavailable)
	}
	if m.attempted {
		return managedResult(managedBusy)
	}
	m.attempted = true
	timeout := 120 * time.Second
	if timeoutMs > 0 && time.Duration(timeoutMs)*time.Millisecond < timeout {
		timeout = time.Duration(timeoutMs) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(m.ctx, timeout)
	defer cancel()
	fail := func(code int) int {
		srvMu.Lock()
		m.ready = false
		engineGeneration++
		m.cancel()
		srvMu.Unlock()
		closeAllConnections()
		if !m.closeServer() {
			code = managedStateError
		}
		return managedResult(code)
	}
	if ctx.Err() != nil {
		return fail(contextResult(ctx, managedBackend))
	}
	if ambientOverride() {
		return fail(managedInvalid)
	}
	if err := m.profile.validate(); err != nil {
		return fail(managedStateError)
	}
	if err := m.profile.phase("submitting"); err != nil {
		return fail(managedStateError)
	}
	enroll := m.server.AuthKey != ""
	run := func() int {
		if ctx.Err() != nil {
			return contextResult(ctx, managedBackend)
		}
		m.started = true // Close is legal only after this owner's synchronous Start.
		if err := m.server.Start(); err != nil {
			return contextResult(ctx, managedBackend)
		}
		st, err := m.server.Up(ctx)
		if err != nil || st == nil || st.BackendState != "Running" || len(st.TailscaleIPs) == 0 {
			return contextResult(ctx, managedBackend)
		}
		return contextResult(ctx, 0)
	}
	if code := run(); code != 0 {
		return fail(code)
	}
	if enroll {
		if !m.closeServer() {
			return fail(managedStateError)
		}
		if ctx.Err() != nil {
			return fail(contextResult(ctx, managedBackend))
		}
		if err := m.profile.validate(); err != nil {
			return fail(managedStateError)
		}
		srvMu.Lock()
		m.server = m.newServer("") // Release enrollment config; never mutate/replay it.
		srv = m.server
		srvMu.Unlock()
		if code := run(); code != 0 {
			return fail(code)
		}
	}
	sys := m.server.Sys()
	eng, ok := sys.Engine.GetOK()
	d, dok := sys.Dialer.GetOK()
	if !ok || eng == nil || !dok || d == nil || d.NetstackDialTCP == nil {
		return fail(managedUnavailable)
	}
	if err := m.profile.validate(); err != nil {
		return fail(managedStateError)
	}
	if err := m.profile.commitConsumed(); err != nil {
		return fail(managedStateError)
	}
	srvMu.Lock()
	if ctx.Err() != nil || engineGeneration != m.generation {
		srvMu.Unlock()
		return fail(contextResult(ctx, managedCanceled))
	}
	m.engine = eng
	m.dialTCP = d.NetstackDialTCP
	m.ready = true
	srvMu.Unlock()
	return managedResult(0)
}
func (m *managedEngine) closeServer() bool {
	if m.closeFailed {
		return false
	}
	if m.server == nil {
		return true
	}
	if m.started {
		if err := m.server.Close(); err != nil {
			m.closeFailed = true
			return false
		}
	}
	srvMu.Lock()
	if srv == m.server {
		srv = nil
	}
	m.server = nil
	m.started = false
	srvMu.Unlock()
	return true
}
func cancelManaged() {
	srvMu.Lock()
	active := managed != nil
	if active {
		managed.cancel()
		managed.ready = false
		engineGeneration++
	}
	srvMu.Unlock()
	if active {
		closeAllConnections()
	}
}
func closeAllConnections() {
	connMu.Lock()
	conns := connTable
	connTable = make(map[int32]net.Conn)
	connMu.Unlock()
	for _, c := range conns {
		_ = c.Close()
	}
}
func managedActive(m *managedEngine) bool {
	return managed == m && m != nil && m.ready && m.ctx.Err() == nil && m.generation == engineGeneration
}

// Resolve only full names or literal IPs from embedded peer status. No OS DNS.
func (m *managedEngine) resolvePeer(ctx context.Context, host string) (netip.Addr, key.NodePublic, int) {
	srvMu.Lock()
	if !managedActive(m) {
		srvMu.Unlock()
		return netip.Addr{}, key.NodePublic{}, managedCanceled
	}
	server := m.server
	engine := m.engine
	srvMu.Unlock()
	lc, err := server.LocalClient()
	if err != nil {
		return netip.Addr{}, key.NodePublic{}, managedPeerChanged
	}
	st, err := lc.Status(ctx)
	if err != nil || st == nil || st.BackendState != "Running" {
		return netip.Addr{}, key.NodePublic{}, managedPeerChanged
	}
	literal, parseErr := netip.ParseAddr(host)
	var ips []netip.Addr
	var peerKey key.NodePublic
	matches := 0
	for k, p := range st.Peer {
		if p == nil || k.IsZero() || p.PublicKey != k {
			continue
		}
		match := false
		if parseErr == nil {
			for _, ip := range p.TailscaleIPs {
				if ip == literal {
					match = true
				}
			}
		} else {
			match = p.DNSName != "" && strings.EqualFold(strings.TrimSuffix(p.DNSName, "."), strings.TrimSuffix(host, "."))
		}
		if match {
			matches++
			peerKey = k
			if parseErr == nil {
				ips = []netip.Addr{literal}
			} else {
				ips = append([]netip.Addr(nil), p.TailscaleIPs...)
			}
		}
	}
	if matches != 1 {
		return netip.Addr{}, key.NodePublic{}, managedUnsafe
	}
	sort.Slice(ips, func(i, j int) bool {
		if ips[i].Is4() != ips[j].Is4() {
			return ips[i].Is4()
		}
		return ips[i].Less(ips[j])
	})
	for _, ip := range ips {
		if ip.Zone() != "" || !tsaddr.IsTailscaleIP(ip) {
			continue
		}
		peer, ok := engine.PeerForIP(ip)
		if !ok || peer.IsSelf || !peer.Route.IsSingleIP() || peer.Route.Addr() != ip || !peer.Node.Valid() || peer.Node.Key() != peerKey {
			continue
		}
		for i := 0; i < peer.Node.Addresses().Len(); i++ {
			a := peer.Node.Addresses().At(i)
			if a.IsSingleIP() && a.Addr() == ip {
				return ip, peerKey, 0
			}
		}
	}
	return netip.Addr{}, key.NodePublic{}, managedUnsafe
}

// The captured NetstackDialTCP callback cannot select Server.Dial/UserDial or
// SystemDial. Map loss between observations therefore has no plaintext fallback.
func managedDialPeer(ctx context.Context, host string, port int) (net.Conn, int) {
	srvMu.Lock()
	m := managed
	if !managedActive(m) {
		srvMu.Unlock()
		return nil, managedUnavailable
	}
	pinned := m.pinned
	srvMu.Unlock()
	refusal := func(code int) int {
		if !pinned.IsZero() {
			return managedPeerChanged
		}
		return code
	}
	if host == "" || hasControl(host) || strings.TrimSpace(host) != host || port < 1 || port > 65535 {
		return nil, refusal(managedUnsafe)
	}
	ip, peer, code := m.resolvePeer(ctx, host)
	if code != 0 {
		return nil, contextResult(ctx, refusal(code))
	}
	if !pinned.IsZero() && pinned != peer {
		return nil, managedPeerChanged
	}
	conn, err := m.dialTCP(ctx, netip.AddrPortFrom(ip, uint16(port)))
	ip2, peer2, code := m.resolvePeer(ctx, host)
	if err != nil {
		srvMu.Lock()
		active := managedActive(m)
		srvMu.Unlock()
		if !active || code != 0 || ip2 != ip || peer2 != peer {
			return nil, contextResult(ctx, managedPeerChanged)
		}
		return nil, contextResult(ctx, managedBackend)
	}
	srvMu.Lock()
	defer srvMu.Unlock()
	if ctx.Err() != nil || !managedActive(m) || code != 0 || ip2 != ip || peer2 != peer || (!m.pinned.IsZero() && m.pinned != peer) {
		_ = conn.Close()
		return nil, contextResult(ctx, managedPeerChanged)
	}
	m.pinned = peer
	return &managedConnection{Conn: conn, owner: m, generation: m.generation, host: host, ip: ip, peer: peer}, 0
}
func (c *managedConnection) validate(ctx context.Context) int {
	srvMu.Lock()
	active := managedActive(c.owner) && c.generation == engineGeneration
	srvMu.Unlock()
	if !active {
		return managedCanceled
	}
	ip, peer, code := c.owner.resolvePeer(ctx, c.host)
	srvMu.Lock()
	active = managedActive(c.owner) && c.owner.pinned == c.peer
	srvMu.Unlock()
	if !active || code != 0 || ip != c.ip || peer != c.peer {
		return contextResult(ctx, managedPeerChanged)
	}
	return 0
}
