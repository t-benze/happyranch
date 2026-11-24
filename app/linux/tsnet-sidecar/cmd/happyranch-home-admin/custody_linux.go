package main

import (
	"context"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
	"syscall"
)

func openDir(s machine, path string, service bool, create bool) (*os.File, error) {
	fd, err := syscall.Open(s.root, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, syscall.EPERM
	}
	current := os.NewFile(uintptr(fd), "root")
	var st syscall.Stat_t
	if syscall.Fstat(fd, &st) != nil || st.Uid != s.uid || st.Gid != s.gid || st.Mode&0022 != 0 {
		current.Close()
		return nil, syscall.EPERM
	}
	parts := strings.Split(path, "/")
	for i, part := range parts {
		managed := !service && i >= 2
		next, e := syscall.Openat(int(current.Fd()), part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if e == syscall.ENOENT && create && managed {
			if syscall.Mkdirat(int(current.Fd()), part, 0700) != nil {
				current.Close()
				return nil, syscall.EPERM
			}
			next, e = syscall.Openat(int(current.Fd()), part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
			if e == nil && (syscall.Fchown(next, int(s.uid), int(s.gid)) != nil || syscall.Fchmod(next, 0700) != nil || syscall.Fsync(next) != nil || current.Sync() != nil) {
				syscall.Close(next)
				current.Close()
				return nil, syscall.EPERM
			}
		}
		current.Close()
		if e != nil {
			return nil, syscall.EPERM
		}
		current = os.NewFile(uintptr(next), part)
		uid, gid := s.uid, s.gid
		exact := managed
		if service && i == len(parts)-1 {
			uid, gid = s.serviceUID, s.serviceGID
			exact = true
		}
		if syscall.Fstat(next, &st) != nil || st.Uid != uid || st.Gid != gid || st.Mode&0022 != 0 || (exact && st.Mode != syscall.S_IFDIR|0700) {
			current.Close()
			return nil, syscall.EPERM
		}
	}
	return current, nil
}
func openState(s machine, name string) (*os.File, error) {
	return openDir(s, statePath+"/"+name, false, true)
}
func fileAt(dir *os.File, name string, s machine, create bool) (*os.File, error) {
	flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_CLOEXEC | syscall.O_NONBLOCK
	if create {
		flags = syscall.O_RDWR | syscall.O_CREAT | syscall.O_NOFOLLOW | syscall.O_CLOEXEC | syscall.O_NONBLOCK
	}
	fd, err := syscall.Openat(int(dir.Fd()), name, flags, 0600)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), name)
	var st syscall.Stat_t
	if syscall.Fstat(fd, &st) != nil || st.Mode != syscall.S_IFREG|0600 || st.Uid != s.uid || st.Gid != s.gid || st.Nlink != 1 || st.Size > 32 || (create && st.Size != 0) {
		f.Close()
		return nil, syscall.EPERM
	}
	return f, nil
}
func settled(dir *os.File, s machine) bool {
	// Pending publication is always uncertain, including a zero-byte crash.
	f, err := fileAt(dir, "fence.pending", s, false)
	if err == nil {
		f.Close()
		return false
	}
	if err != syscall.ENOENT {
		return false
	}
	f, err = fileAt(dir, "fence", s, false)
	if err == syscall.ENOENT {
		return true
	}
	if err != nil {
		return false
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 33))
	if err != nil {
		return false
	}
	expiry, err := strconv.ParseInt(string(data), 10, 64)
	return err == nil && strconv.FormatInt(expiry, 10) == string(data) && expiry > 0 && expiry <= 253402300799 && s.now().Unix() >= expiry
}
func fence(dir *os.File, deadline int64, s machine) bool {
	fd, err := syscall.Openat(int(dir.Fd()), "fence.pending", syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return false
	}
	f := os.NewFile(uintptr(fd), "pending")
	defer f.Close()
	data := []byte(strconv.FormatInt(deadline, 10))
	if syscall.Fchown(fd, int(s.uid), int(s.gid)) != nil || syscall.Fchmod(fd, 0600) != nil {
		return false
	}
	if n, err := f.Write(data); err != nil || n != len(data) {
		return false
	}
	return f.Sync() == nil && syscall.Renameat(int(dir.Fd()), "fence.pending", int(dir.Fd()), "fence") == nil && dir.Sync() == nil
}
func connect(ctx context.Context, s machine) (net.Conn, error) {
	dir, err := openDir(s, "run/happyranch-headscale", true, false)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	path := "/proc/self/fd/" + strconv.Itoa(int(dir.Fd())) + "/headscale.sock"
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	st, ok := before.Sys().(*syscall.Stat_t)
	if !ok || st.Mode != syscall.S_IFSOCK|0600 || st.Uid != s.serviceUID || st.Gid != s.serviceGID || st.Nlink != 1 {
		return nil, syscall.EPERM
	}
	conn, err := (&net.Dialer{}).DialContext(ctx, "unix", path)
	if err != nil {
		return nil, err
	}
	after, err := os.Lstat(path)
	if err == nil {
		a, valid := after.Sys().(*syscall.Stat_t)
		if !valid || a.Mode != st.Mode || a.Uid != st.Uid || a.Gid != st.Gid || a.Nlink != 1 {
			err = syscall.EPERM
		}
	}
	if err != nil || !os.SameFile(before, after) {
		conn.Close()
		return nil, syscall.EPERM
	}
	unix, ok := conn.(*net.UnixConn)
	if !ok {
		conn.Close()
		return nil, syscall.EPERM
	}
	raw, err := unix.SyscallConn()
	if err != nil {
		conn.Close()
		return nil, err
	}
	valid := false
	if raw.Control(func(fd uintptr) {
		cred, e := syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
		valid = e == nil && cred.Uid == s.serviceUID && cred.Gid == s.serviceGID
	}) != nil || !valid {
		conn.Close()
		return nil, syscall.EPERM
	}
	return conn, nil
}
