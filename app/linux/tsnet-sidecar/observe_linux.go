package sidecar

import (
	"context"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// Process-wide umask changes are serialized; 077 also protects bind-to-chmod.
var observeBindMu sync.Mutex

func newObservationListener(path string) (net.Listener, error) {
	if noSymlinkPath(path, false) != nil {
		return nil, ErrListener
	}
	parent, err := os.Lstat(filepath.Dir(path))
	if err != nil || !parent.IsDir() || !ownedByCurrentUser(parent) || parent.Mode().Perm()&0o022 != 0 {
		return nil, ErrListener
	}
	if st, err := os.Lstat(path); err == nil {
		sys, ok := st.Sys().(*syscall.Stat_t)
		if !ok || st.Mode()&os.ModeSocket == 0 || !ownedByCurrentUser(st) || sys.Nlink != 1 || unix.Unlink(path) != nil {
			return nil, ErrListener
		}
	} else if !os.IsNotExist(err) {
		return nil, ErrListener
	}
	fd, err := unix.Socket(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, ErrListener
	}
	f := os.NewFile(uintptr(fd), "observe")
	defer f.Close()
	observeBindMu.Lock()
	mask := unix.Umask(0o077)
	err = unix.Bind(fd, &unix.SockaddrUnix{Name: path})
	unix.Umask(mask)
	observeBindMu.Unlock()
	if err != nil {
		return nil, ErrListener
	}
	// The FD mode and Linux's distinct filesystem socket inode are both fixed
	// before listen. No connection can observe a permissive socket entry.
	if unix.Fchmod(fd, 0o600) != nil || os.Chmod(path, 0o600) != nil || unix.Listen(fd, 8) != nil {
		return nil, ErrListener
	}
	l, err := net.FileListener(f)
	if err != nil {
		return nil, ErrListener
	}
	// FileListener does not own pathname cleanup; only remove our original inode.
	info, err := os.Lstat(path)
	if err != nil {
		l.Close()
		return nil, ErrListener
	}
	return &observationListener{Listener: l, path: path, info: info}, nil
}

type observationListener struct {
	net.Listener
	path string
	info os.FileInfo
}

func (l *observationListener) Close() error {
	err := l.Listener.Close()
	if current, e := os.Lstat(l.path); e == nil && os.SameFile(l.info, current) {
		_ = unix.Unlink(l.path)
	}
	return err
}

func (s *Sidecar) startObserver(ctx context.Context) error {
	if s.cfg.ControlTLS == nil {
		return nil
	}
	l, err := newObservationListener(observeSocket)
	if err != nil {
		return err
	}
	s.observer = l
	s.observerWG.Add(1)
	go func() {
		defer s.observerWG.Done()
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			s.serveObservation(ctx, conn)
		}
	}()
	return nil
}

func (s *Sidecar) serveObservation(ctx context.Context, conn net.Conn) {
	defer conn.Close()
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	until, _ := ctx.Deadline()
	if conn.SetDeadline(until) != nil {
		return
	}
	request, err := io.ReadAll(io.LimitReader(conn, int64(len(observeRequest)+1)))
	if err != nil || string(request) != observeRequest || !s.Running() {
		return
	}
	provider, ok := s.engine.(interface {
		observeSelf(context.Context) (selfObservation, error)
	})
	if !ok {
		return
	}
	value, err := provider.observeSelf(ctx)
	if err != nil || !s.Running() {
		return
	}
	value.Binding = s.observationBinding
	data, err := observationBytes(value)
	if err == nil && ctx.Err() == nil {
		_, _ = conn.Write(data)
	}
}
