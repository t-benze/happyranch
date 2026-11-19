package sidecar

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

const observeSocket = "/run/happyranch-tsnet-sidecar/observe.sock"
const observeRequest = "observe-self@1\n"

// Bind/chmod before listen: net.ListenUnix would admit connections too early.
func listenObservation(path string) (net.Listener, error) {
	parent, err := os.Lstat(filepath.Dir(path))
	if err != nil || !parent.IsDir() || parent.Mode().Perm()&0022 != 0 || parent.Sys().(*syscall.Stat_t).Uid != uint32(os.Geteuid()) {
		return nil, ErrEngine
	}
	parentFD, err := unix.Open(filepath.Dir(path), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, ErrEngine
	}
	defer unix.Close(parentFD)
	var pinned unix.Stat_t
	if unix.Fstat(parentFD, &pinned) != nil || (pinned.Ino != parent.Sys().(*syscall.Stat_t).Ino || pinned.Dev != uint64(parent.Sys().(*syscall.Stat_t).Dev)) || pinned.Uid != uint32(os.Geteuid()) || pinned.Mode&0022 != 0 {
		return nil, ErrEngine
	}
	var st unix.Stat_t
	if err := unix.Fstatat(parentFD, filepath.Base(path), &st, unix.AT_SYMLINK_NOFOLLOW); err == nil {
		if st.Mode&unix.S_IFMT != unix.S_IFSOCK || st.Uid != uint32(os.Geteuid()) || st.Nlink != 1 {
			return nil, ErrEngine
		}
		if unix.Unlinkat(parentFD, filepath.Base(path), 0) != nil {
			return nil, ErrEngine
		}
	} else if !os.IsNotExist(err) {
		return nil, ErrEngine
	}
	fd, err := unix.Socket(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, ErrEngine
	}
	file := os.NewFile(uintptr(fd), path)
	defer file.Close()
	// The process is dedicated to the home sidecar; keep restrictive umask.
	unix.Umask(0077)
	if unix.Bind(fd, &unix.SockaddrUnix{Name: fmt.Sprintf("/proc/self/fd/%d/%s", parentFD, filepath.Base(path))}) != nil {
		return nil, ErrEngine
	}
	// fchmod the descriptor and its bound filesystem inode (Linux keeps these distinct).
	if unix.Fchmod(fd, 0600) != nil || unix.Fchmodat(parentFD, filepath.Base(path), 0600, 0) != nil || unix.Listen(fd, 4) != nil {
		return nil, ErrEngine
	}
	listener, err := net.FileListener(file)
	if err != nil {
		return nil, ErrEngine
	}
	return observationListener{listener, path}, nil
}

// Keep the canonical path as the listener address after the bind parent's FD closes.
type observationListener struct {
	net.Listener
	path string
}

func (l observationListener) Addr() net.Addr { return &net.UnixAddr{Name: l.path, Net: "unix"} }

type observationServer struct {
	listener net.Listener
	mu       sync.Mutex
	active   net.Conn
	closed   bool
	path     string
	identity unix.Stat_t
}

func (s *observationServer) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	if s.active != nil {
		_ = s.active.Close()
	}
	err := s.listener.Close()
	var st unix.Stat_t
	if unix.Lstat(s.path, &st) == nil && st.Dev == s.identity.Dev && st.Ino == s.identity.Ino {
		if unix.Unlink(s.path) != nil {
			return ErrEngine
		}
	}
	return err
}
func (e *TSNetEngine) startHomeObserver(c EngineConfig) error {
	if e.home == nil {
		return nil
	}
	client, err := e.server.LocalClient()
	if err != nil {
		return ErrEngine
	}
	var binding *HomeBinding
	if c.HomeBinding != nil {
		copy := *c.HomeBinding
		binding = &copy
	}
	e.admissionMu.Lock()
	defer e.admissionMu.Unlock()
	if e.closed {
		return ErrEngine
	}
	listener, err := listenObservation(observeSocket)
	if err != nil {
		return ErrEngine
	}
	e.observer = serveObservation(listener, func(ctx context.Context) ([]byte, error) {
		e.admissionMu.Lock()
		closed := e.closed
		select {
		case <-e.home.done:
			closed = true
		default:
		}
		e.admissionMu.Unlock()
		if closed {
			return nil, ErrEngine
		}
		reply, err := observeSelf(ctx, client, binding)
		e.admissionMu.Lock()
		defer e.admissionMu.Unlock()
		if e.closed {
			return nil, ErrEngine
		}
		select {
		case <-e.home.done:
			return nil, ErrEngine
		default:
		}
		return reply, err
	})
	return nil
}
func serveObservation(listener net.Listener, read func(context.Context) ([]byte, error)) *observationServer {
	s := &observationServer{listener: listener, path: listener.Addr().String()}
	_ = unix.Lstat(s.path, &s.identity)
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			s.mu.Lock()
			if s.closed {
				_ = conn.Close()
				s.mu.Unlock()
				return
			}
			s.active = conn
			s.mu.Unlock()
			func() {
				defer conn.Close()
				until := time.Now().Add(5 * time.Second)
				_ = conn.SetDeadline(until)
				ctx, cancel := context.WithDeadline(context.Background(), until)
				defer cancel()
				// EOF (client half-close) terminates the sole exact request, rejecting pipelines.
				request, err := io.ReadAll(io.LimitReader(conn, int64(len(observeRequest)+1)))
				if err != nil || string(request) != observeRequest {
					return
				}
				reply, err := read(ctx)
				if err != nil || ctx.Err() != nil || len(reply) == 0 || len(reply) > 4096 {
					return
				}
				_, _ = conn.Write(reply)
			}()
			s.mu.Lock()
			s.active = nil
			s.mu.Unlock()
		}
	}()
	return s
}
