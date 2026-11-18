package main

import (
	"bytes"
	"encoding/binary"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"golang.org/x/net/http2"
)

func fixture(t *testing.T) machine {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"var/lib", "run/happyranch-headscale"} {
		if err := os.MkdirAll(filepath.Join(root, p), 0700); err != nil {
			t.Fatal(err)
		}
	}
	return machine{root: root, uid: uint32(os.Getuid()), gid: uint32(os.Getgid()), serviceUID: uint32(os.Getuid()), serviceGID: uint32(os.Getgid()), now: time.Now}
}
func fieldBytes(n byte, b []byte) []byte {
	r := []byte{n*8 + 2}
	r = binary.AppendUvarint(r, uint64(len(b)))
	return append(r, b...)
}
func input(verb string, s machine) []byte {
	b := fieldBytes(1, []byte("happyranch-invite"))
	if verb == "create" {
		ts := binary.AppendUvarint([]byte{8}, uint64(s.now().Unix()+120))
		b = append(b, fieldBytes(4, ts)...)
	}
	if verb == "expire" {
		b = append(b, fieldBytes(2, bytes.Repeat([]byte("a"), 48))...)
	}
	return b
}
func serve(t *testing.T, s machine, handler http.HandlerFunc) *atomic.Int32 {
	t.Helper()
	parent, err := os.Open(filepath.Join(s.root, "run/happyranch-headscale"))
	if err != nil {
		t.Fatal(err)
	}
	// Linux AF_UNIX has a 108-byte path limit; anchor the test listener just
	// like production's descriptor-relative dial, without shortening task roots.
	l, err := net.Listen("unix", "/proc/self/fd/"+strconv.Itoa(int(parent.Fd()))+"/headscale.sock")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(l.Addr().String(), 0600); err != nil {
		t.Fatal(err)
	}
	calls := &atomic.Int32{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			c, e := l.Accept()
			if e != nil {
				return
			}
			go (&http2.Server{}).ServeConn(c, &http2.ServeConnOpts{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); handler(w, r) })})
		}
	}()
	t.Cleanup(func() { l.Close(); <-done })
	return calls
}
func reply(w http.ResponseWriter, body []byte, status string) {
	w.Header().Set("Content-Type", "application/grpc")
	w.Header().Set("Trailer", "Grpc-Status")
	frame := make([]byte, 5)
	binary.BigEndian.PutUint32(frame[1:], uint32(len(body)))
	_, _ = w.Write(append(frame, body...))
	w.Header().Set("Grpc-Status", status)
}
func TestUnaryOperations(t *testing.T) {
	var diagnostics bytes.Buffer
	previous := log.Writer()
	log.SetOutput(&diagnostics)
	defer log.SetOutput(previous)
	http2.VerboseLogs = true
	s := fixture(t)
	calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) {
		b, e := io.ReadAll(r.Body)
		if e != nil || len(b) < 5 || b[0] != 0 || int(binary.BigEndian.Uint32(b[1:])) != len(b)-5 {
			t.Error("request framing")
		}
		verb := map[string]string{"/headscale.v1.HeadscaleService/CreatePreAuthKey": "create", "/headscale.v1.HeadscaleService/ListPreAuthKeys": "list", "/headscale.v1.HeadscaleService/ExpirePreAuthKey": "expire"}[r.URL.Path]
		if _, _, ok := request(verb, b[5:], s.now()); !ok {
			t.Error("request schema")
		}
		body := []byte{}
		if verb != "expire" {
			body = fieldBytes(1, fieldBytes(3, bytes.Repeat([]byte("a1"), 24)))
		}
		reply(w, body, "0")
	})
	for _, verb := range []string{"create", "list", "expire"} {
		var out bytes.Buffer
		if code := command([]string{verb, "1000"}, bytes.NewReader(input(verb, s)), &out, s); code != 0 {
			t.Fatalf("%s: %d", verb, code)
		}
	}
	if bytes.Contains(diagnostics.Bytes(), bytes.Repeat([]byte("a1"), 24)) || http2.VerboseLogs {
		t.Fatal("secret diagnostics")
	}
	if calls.Load() != 3 {
		t.Fatal("missing RPC")
	}
}
func TestLostCreateFenceSurvivesRestart(t *testing.T) {
	for _, failure := range []string{"timeout", "lost", "status3", "status5", "status13", "truncated", "compressed", "multi"} {
		t.Run(failure, func(t *testing.T) {
			s := fixture(t)
			clock := s.now()
			s.now = func() time.Time { return clock }
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) {
				switch failure {
				case "timeout":
					<-r.Context().Done()
				case "lost":
					w.Header().Set("Content-Type", "application/grpc")
				case "truncated":
					w.Header().Set("Content-Type", "application/grpc")
					w.Header().Set("Trailer", "Grpc-Status")
					_, _ = w.Write([]byte{0, 0, 0, 0, 8, 1})
					w.Header().Set("Grpc-Status", "0")
				case "compressed":
					w.Header().Set("Content-Type", "application/grpc")
					w.Header().Set("Trailer", "Grpc-Status")
					_, _ = w.Write([]byte{1, 0, 0, 0, 0})
					w.Header().Set("Grpc-Status", "0")
				case "multi":
					reply(w, []byte{}, "0")
					_, _ = w.Write([]byte{0, 0, 0, 0, 0})
				default:
					reply(w, []byte{}, failure[6:])
				}
			})
			var out bytes.Buffer
			if code := command([]string{"create", "50"}, bytes.NewReader(input("create", s)), &out, s); code != 3 || out.Len() != 0 {
				t.Fatalf("create %d", code)
			}
			// A new command opens files afresh, with no in-memory settlement state.
			if code := command([]string{"list", "50"}, bytes.NewReader(input("list", s)), &out, s); code != 2 || calls.Load() != 1 {
				t.Fatal("lost-create ordering fence bypassed")
			}
			if b, e := os.ReadFile(filepath.Join(s.root, statePath, "happyranch-invite/fence")); e != nil || string(b) != strconv.FormatInt(clock.Unix()+120, 10) {
				t.Fatal("durable expiry")
			}
			clock = clock.Add(121 * time.Second)
			// Expiry admits the RPC; the fake still returns its deliberately bad reply.
			command([]string{"list", "50"}, bytes.NewReader(input("list", s)), &out, s)
			if calls.Load() != 2 {
				t.Fatal("expiry did not settle")
			}
		})
	}
}
func TestSocketCustodyRefusesBeforeRPC(t *testing.T) {
	for _, tamper := range []string{"mode", "parent-mode", "symlink", "parent-link", "owner"} {
		t.Run(tamper, func(t *testing.T) {
			s := fixture(t)
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
			path := filepath.Join(s.root, "run/happyranch-headscale/headscale.sock")
			switch tamper {
			case "mode":
				os.Chmod(path, 0660)
			case "parent-mode":
				os.Chmod(filepath.Dir(path), 0750)
			case "symlink":
				os.Rename(path, path+".real")
				os.Symlink(path+".real", path)
			case "parent-link":
				os.Rename(filepath.Dir(path), filepath.Dir(path)+".real")
				os.Symlink(filepath.Dir(path)+".real", filepath.Dir(path))
			case "owner":
				s.serviceUID++
			}
			var out bytes.Buffer
			if code := command([]string{"create", "100"}, bytes.NewReader(input("create", s)), &out, s); code != 2 || calls.Load() != 0 || out.Len() != 0 {
				t.Fatal("unsafe socket admitted")
			}
		})
	}
}
func TestFenceCustodyAndResidue(t *testing.T) {
	for _, tamper := range []string{"pending", "empty", "bad", "link", "hardlink", "mode", "directory", "parent-link", "busy"} {
		t.Run(tamper, func(t *testing.T) {
			s := fixture(t)
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
			dir, e := openState(s, "happyranch-invite")
			if e != nil {
				t.Fatal(e)
			}
			defer dir.Close()
			base := filepath.Join(s.root, statePath, "happyranch-invite")
			path := filepath.Join(base, "fence")
			switch tamper {
			case "pending":
				os.WriteFile(filepath.Join(base, "fence.pending"), nil, 0600)
			case "empty":
				os.WriteFile(path, nil, 0600)
			case "bad":
				os.WriteFile(path, []byte("not-a-time"), 0600)
			case "link":
				os.Symlink("foreign", path)
			case "hardlink":
				os.WriteFile(path, []byte("1"), 0600)
				os.Link(path, path+".alias")
			case "mode":
				os.WriteFile(path, []byte("1"), 0644)
			case "directory":
				os.Mkdir(path, 0700)
			case "parent-link":
				os.Rename(base, base+".real")
				os.Symlink(base+".real", base)
			case "busy":
				lock, e := fileAt(dir, "rpc.lock", s, true)
				if e != nil {
					t.Fatal(e)
				}
				defer lock.Close()
				syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
			}
			var out bytes.Buffer
			if code := command([]string{"list", "100"}, bytes.NewReader(input("list", s)), &out, s); code != 2 || calls.Load() != 0 {
				t.Fatal("unsafe fence admitted")
			}
		})
	}
}
func TestRequestClosedSchema(t *testing.T) {
	s := fixture(t)
	calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
	good := input("create", s)
	for _, b := range [][]byte{append(append([]byte{}, good...), 0x28, 0), append(append([]byte{}, good...), good...), {0x0a, 0x80}, {0x0a, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 2}, {0x08, 1}} {
		var out bytes.Buffer
		if command([]string{"create", "100"}, bytes.NewReader(b), &out, s) != 2 || calls.Load() != 0 {
			t.Fatal("malformed request")
		}
	}
}
