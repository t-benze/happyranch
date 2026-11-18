package main

import (
	"bytes"
	"encoding/binary"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
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
	r := binary.AppendUvarint(nil, uint64(n)*8+2)
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

func TestRunnerDiagnosticsStartup(t *testing.T) {
	// Re-exec so x/net observes GODEBUG during package initialization. The
	// child uses production command startup with real stdin/stdout/stderr and
	// the existing temporary machine/socket fixture; no shipped override exists.
	if verb := os.Getenv("HAPPYRANCH_HOME_ADMIN_TEST_VERB"); verb != "" {
		s := fixture(t)
		serve(t, s, func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.Copy(io.Discard, r.Body)
			var body []byte
			if verb != "expire" {
				body = fieldBytes(1, fieldBytes(3, bytes.Repeat([]byte("a1"), 24)))
			}
			reply(w, body, "0")
		})
		// Prove suppression even if the inherited logger targets stdout.
		log.SetOutput(os.Stdout)
		os.Exit(command([]string{verb, "1000"}, os.Stdin, os.Stdout, s))
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, level := range []string{"1", "2"} {
		for _, verb := range []string{"create", "list", "expire"} {
			t.Run(level+"/"+verb, func(t *testing.T) {
				child := exec.Command(executable, "-test.run=^TestRunnerDiagnosticsStartup$")
				child.Env = append(os.Environ(), "GODEBUG=http2debug="+level,
					"HAPPYRANCH_HOME_ADMIN_TEST_VERB="+verb, "TMPDIR="+t.TempDir())
				child.Stdin = bytes.NewReader(input(verb, machine{now: time.Now}))
				var out, diagnostics bytes.Buffer
				child.Stdout, child.Stderr = &out, &diagnostics
				if err := child.Run(); err != nil {
					t.Fatal("runner startup failed")
				}
				var expected []byte
				if verb != "expire" {
					expected = fieldBytes(1, fieldBytes(3, bytes.Repeat([]byte("a1"), 24)))
				}
				if diagnostics.Len() != 0 || !bytes.Equal(out.Bytes(), expected) {
					t.Fatal("runner emitted diagnostics outside private response")
				}
			})
		}
	}
}

type delayedResponse struct{ wrote bool }

func (w *delayedResponse) Write(data []byte) (int, error) {
	w.wrote = true
	time.Sleep(1100 * time.Millisecond)
	return len(data), nil
}

func TestRunnerDeadlineAtSuccess(t *testing.T) {
	s := fixture(t)
	serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
	out := &delayedResponse{}
	code := command([]string{"list", "1000"}, bytes.NewReader(input("list", s)), out, s)
	if !out.wrote {
		t.Fatal("did not reach final response boundary")
	}
	if code != 3 {
		t.Fatal("runner returned late success")
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

func adminUser(id uint64, name string) []byte {
	return append(binary.AppendUvarint([]byte{8}, id), fieldBytes(2, []byte(name))...)
}
func adminNode(id uint64, name string) []byte {
	return append(binary.AppendUvarint([]byte{8}, id), fieldBytes(7, adminUser(1, name))...)
}
func TestFixedAdminLists(t *testing.T) {
	for _, verb := range []string{"list-users", "list-nodes"} {
		t.Run(verb, func(t *testing.T) {
			s := fixture(t)
			row, data, method := adminUser(1, "happyranch-node"), []byte{}, "ListUsers"
			if verb == "list-nodes" {
				row, data, method = adminNode(7, "happyranch-node"), fieldBytes(1, []byte("happyranch-node")), "ListNodes"
			}
			want := fieldBytes(1, row)
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) {
				b, _ := io.ReadAll(r.Body)
				if r.URL.Path != "/headscale.v1.HeadscaleService/"+method || len(b) < 5 || !bytes.Equal(b[5:], data) {
					t.Error("fixed request")
				}
				wire := want
				if verb == "list-users" {
					raw := append(append([]byte{}, row...), fieldBytes(3, []byte{8, 1})...)
					for _, n := range []byte{4, 5, 6, 7, 8} {
						raw = append(raw, fieldBytes(n, []byte("synthetic-metadata-canary"))...)
					}
					wire = fieldBytes(1, raw)
				}
				if verb == "list-nodes" {
					wire = fieldBytes(1, append(append([]byte{}, row...), fieldBytes(3, []byte("synthetic-node-key-canary"))...))
				}
				reply(w, wire, "0")
			})
			var out bytes.Buffer
			if code := command([]string{verb, "1000"}, bytes.NewReader(data), &out, s); code != 0 || calls.Load() != 1 || !bytes.Equal(out.Bytes(), want) {
				t.Fatalf("projection code=%d calls=%d", code, calls.Load())
			}
		})
	}
}
func TestFixedAdminListRefusals(t *testing.T) {
	for _, scenario := range []string{"foreign", "duplicate-id", "duplicate-name", "unknown", "duplicate-field", "bad-wire", "truncated-record", "row-size", "row-count", "response-size", "pagination", "bad-timestamp", "utf8", "wrong-type", "grpc-error", "late"} {
		t.Run(scenario, func(t *testing.T) {
			s := fixture(t)
			row := adminUser(1, "happyranch-node")
			body := fieldBytes(1, row)
			switch scenario {
			case "foreign":
				body = append(body, fieldBytes(1, adminUser(2, "foreign"))...)
			case "duplicate-id":
				body = append(body, fieldBytes(1, adminUser(1, "happyranch-invite"))...)
			case "duplicate-name":
				body = append(body, fieldBytes(1, adminUser(2, "happyranch-node"))...)
			case "unknown", "pagination":
				body = append(body, fieldBytes(2, []byte("synthetic-secret-canary"))...)
			case "duplicate-field":
				body = fieldBytes(1, append(row, fieldBytes(2, []byte("happyranch-node"))...))
			case "bad-timestamp":
				body = fieldBytes(1, append(row, fieldBytes(3, binary.AppendUvarint([]byte{8}, 253402300800))...))
			case "utf8":
				body = fieldBytes(1, append(row, fieldBytes(4, []byte{0xff})...))
			case "wrong-type":
				body = fieldBytes(1, append(row, 0x20, 1))
			case "bad-wire":
				body = fieldBytes(1, []byte{8, 0x81, 0})
			case "truncated-record":
				body = append(body, 10, 127, 1)
			case "row-size":
				body = fieldBytes(1, append(row, fieldBytes(4, bytes.Repeat([]byte("x"), 4096))...))
			case "row-count":
				body = bytes.Repeat(body, 65)
			case "response-size":
				body = bytes.Repeat([]byte("x"), (4<<20)+1)
			}
			serve(t, s, func(w http.ResponseWriter, r *http.Request) {
				if scenario == "late" {
					<-r.Context().Done()
					return
				}
				status := "0"
				if scenario == "grpc-error" {
					status = "13"
					w.Header().Set("Grpc-Message", "synthetic-secret-canary")
				}
				reply(w, body, status)
			})
			var out, diagnostics bytes.Buffer
			prior := log.Writer()
			log.SetOutput(&diagnostics)
			defer log.SetOutput(prior)
			if code := command([]string{"list-users", "50"}, bytes.NewReader(nil), &out, s); code != 3 || out.Len() != 0 || diagnostics.Len() != 0 {
				t.Fatalf("unsafe list code=%d bytes=%d", code, out.Len())
			}
		})
	}
}
func TestFixedAdminInvalidRequestBeforeRPC(t *testing.T) {
	for _, verb := range []string{"create-user", "list-nodes", "list-users", "delete-node", "CreateUser"} {
		for _, data := range [][]byte{fieldBytes(1, []byte("foreign")), fieldBytes(1, []byte("synthetic-secret-canary")), bytes.Repeat([]byte("x"), 4097)} {
			s := fixture(t)
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
			var out bytes.Buffer
			if code := command([]string{verb, "1000"}, bytes.NewReader(data), &out, s); code != 2 || calls.Load() != 0 || out.Len() != 0 {
				t.Fatal("invalid request sent")
			}
		}
	}
}
func TestFixedAdminCreateReconciliation(t *testing.T) {
	for _, scenario := range []string{"success", "invite-success", "malformed-create", "mismatched-create", "lost-late", "preflight-foreign", "lost-adopt", "lost-missing", "lost-node", "lost-key", "lost-foreign", "lost-duplicate", "repeat", "preflight-error"} {
		t.Run(scenario, func(t *testing.T) {
			s := fixture(t)
			target := "happyranch-node"
			if scenario == "invite-success" {
				target = "happyranch-invite"
			}
			var creates, lists atomic.Int32
			serve(t, s, func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/headscale.v1.HeadscaleService/ListUsers":
					n := lists.Add(1)
					if scenario == "preflight-error" {
						reply(w, nil, "13")
						return
					}
					var body []byte
					if scenario == "repeat" || (n > 1 && scenario != "lost-missing") {
						body = fieldBytes(1, adminUser(1, target))
					}
					if n > 1 && scenario == "lost-foreign" {
						body = append(body, fieldBytes(1, adminUser(2, "foreign"))...)
					}
					if n > 1 && scenario == "lost-duplicate" {
						body = append(body, body...)
					}
					if scenario == "preflight-foreign" {
						body = fieldBytes(1, adminUser(2, "foreign"))
					}
					reply(w, body, "0")
				case "/headscale.v1.HeadscaleService/CreateUser":
					creates.Add(1)
					if scenario == "lost-late" {
						<-r.Context().Done()
						return
					}
					if scenario == "malformed-create" {
						reply(w, []byte{10, 127, 1}, "0")
						return
					}
					if scenario == "mismatched-create" {
						reply(w, fieldBytes(1, adminUser(2, target)), "0")
						return
					}
					if scenario == "success" || scenario == "invite-success" {
						reply(w, fieldBytes(1, adminUser(1, target)), "0")
					} else {
						reply(w, []byte("synthetic-secret-canary"), "13")
					}
				case "/headscale.v1.HeadscaleService/ListNodes":
					var body []byte
					if scenario == "lost-node" {
						body = fieldBytes(1, adminNode(7, "happyranch-node"))
					}
					reply(w, body, "0")
				case "/headscale.v1.HeadscaleService/ListPreAuthKeys":
					var body []byte
					if scenario == "lost-key" {
						body = fieldBytes(1, fieldBytes(3, []byte("synthetic-secret-canary")))
					}
					reply(w, body, "0")
				default:
					t.Error("unexpected RPC")
					reply(w, nil, "13")
				}
			})
			var out bytes.Buffer
			code := command([]string{"create-user", "100"}, bytes.NewReader(fieldBytes(1, []byte(target))), &out, s)
			want := 3
			if scenario == "success" || scenario == "invite-success" || scenario == "lost-adopt" || scenario == "repeat" {
				want = 0
			}
			if scenario == "preflight-error" || scenario == "preflight-foreign" {
				want = 2
			}
			if code != want {
				t.Fatalf("create category=%d want=%d", code, want)
			}
			if want == 0 && !bytes.Equal(out.Bytes(), fieldBytes(1, adminUser(1, target))) {
				t.Fatal("create projection")
			}
			if want != 0 && out.Len() != 0 {
				t.Fatal("uncertain output")
			}
			if scenario == "repeat" || scenario == "preflight-error" || scenario == "preflight-foreign" {
				if creates.Load() != 0 {
					t.Fatal("unneeded create")
				}
			}
			if creates.Load() > 0 {
				marker, err := os.ReadFile(filepath.Join(s.root, statePath, target, "user-create"))
				if err != nil || string(marker) != "1" {
					t.Fatal("create attempt not retained")
				}
			}
			if scenario == "lost-missing" {
				out.Reset()
				if command([]string{"create-user", "1000"}, bytes.NewReader(fieldBytes(1, []byte(target))), &out, s) != 3 || creates.Load() != 1 {
					t.Fatal("ambiguous create retried")
				}
			}
		})
	}
}

func TestFixedAdminNodeInventory(t *testing.T) {
	for _, scenario := range []string{"512", "513", "duplicate-id", "foreign-user", "wrong-fixed-user", "mismatched-user-id", "unknown-field", "duplicate-field", "bad-bool", "row-size", "truncated", "compressed", "multi-frame"} {
		t.Run(scenario, func(t *testing.T) {
			s := fixture(t)
			body := fieldBytes(1, adminNode(1, "happyranch-node"))
			switch scenario {
			case "512", "513":
				body = nil
				count, _ := strconv.Atoi(scenario)
				for i := 1; i <= count; i++ {
					body = append(body, fieldBytes(1, adminNode(uint64(i), "happyranch-node"))...)
				}
			case "duplicate-id":
				body = append(body, body...)
			case "foreign-user":
				body = fieldBytes(1, adminNode(1, "foreign"))
			case "wrong-fixed-user":
				body = fieldBytes(1, adminNode(1, "happyranch-invite"))
			case "mismatched-user-id":
				body = append(body, fieldBytes(1, append([]byte{8, 2}, fieldBytes(7, adminUser(2, "happyranch-node"))...))...)
			case "unknown-field":
				body = fieldBytes(1, append(adminNode(1, "happyranch-node"), fieldBytes(23, []byte("canary"))...))
			case "duplicate-field":
				body = fieldBytes(1, append(adminNode(1, "happyranch-node"), 8, 1))
			case "bad-bool":
				body = fieldBytes(1, append(adminNode(1, "happyranch-node"), 0xb0, 1, 2))
			case "row-size":
				body = fieldBytes(1, append(adminNode(1, "happyranch-node"), fieldBytes(3, bytes.Repeat([]byte("x"), 4096))...))
			}
			serve(t, s, func(w http.ResponseWriter, r *http.Request) {
				if scenario == "truncated" || scenario == "compressed" {
					w.Header().Set("Content-Type", "application/grpc")
					w.Header().Set("Trailer", "Grpc-Status")
					if scenario == "truncated" {
						_, _ = w.Write([]byte{0, 0, 0, 0, 8, 1})
					} else {
						_, _ = w.Write([]byte{1, 0, 0, 0, 0})
					}
					w.Header().Set("Grpc-Status", "0")
					return
				}
				reply(w, body, "0")
				if scenario == "multi-frame" {
					_, _ = w.Write([]byte{0, 0, 0, 0, 0})
				}
			})
			var out bytes.Buffer
			code := command([]string{"list-nodes", "1000"}, bytes.NewReader(fieldBytes(1, []byte("happyranch-node"))), &out, s)
			if scenario == "512" {
				if code != 0 || !bytes.Equal(out.Bytes(), body) {
					t.Fatal("maximum complete nodes refused")
				}
			} else if code != 3 || out.Len() != 0 {
				t.Fatal("unsafe node list admitted")
			}
		})
	}
}
func TestFixedAdminFinalDeadline(t *testing.T) {
	s := fixture(t)
	serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
	out := &delayedResponse{}
	if code := command([]string{"list-users", "1000"}, bytes.NewReader(nil), out, s); code != 3 || !out.wrote {
		t.Fatal("late output succeeded")
	}
}
func TestFixedAdminMarkerAndKeyFence(t *testing.T) {
	for _, scenario := range []string{"pending-key", "active-key", "torn-marker", "linked-marker", "busy"} {
		t.Run(scenario, func(t *testing.T) {
			s := fixture(t)
			dir, err := openState(s, "happyranch-invite")
			if err != nil {
				t.Fatal(err)
			}
			defer dir.Close()
			path := filepath.Join(s.root, statePath, "happyranch-invite")
			want := 2
			switch scenario {
			case "pending-key":
				os.WriteFile(filepath.Join(path, "fence.pending"), nil, 0600)
			case "active-key":
				if !fence(dir, s.now().Unix()+120, s) {
					t.Fatal("fixture fence")
				}
			case "torn-marker":
				os.WriteFile(filepath.Join(path, "user-create"), nil, 0600)
				want = 3
			case "linked-marker":
				os.Symlink("foreign", filepath.Join(path, "user-create"))
				want = 3
			case "busy":
				lock, err := fileAt(dir, "rpc.lock", s, true)
				if err != nil {
					t.Fatal(err)
				}
				defer lock.Close()
				if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
					t.Fatal("fixture lock")
				}
			}
			calls := serve(t, s, func(w http.ResponseWriter, r *http.Request) { reply(w, nil, "0") })
			var out bytes.Buffer
			if code := command([]string{"create-user", "1000"}, bytes.NewReader(fieldBytes(1, []byte("happyranch-invite"))), &out, s); code != want || calls.Load() != 0 || out.Len() != 0 {
				t.Fatal("unsafe user create admitted")
			}
		})
	}
}

func TestFixedAdminDiagnosticsStartup(t *testing.T) {
	if os.Getenv("HAPPYRANCH_HOME_ADMIN_B3_TEST") == "1" {
		s := fixture(t)
		serve(t, s, func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.Copy(io.Discard, r.Body)
			row := adminNode(7, "happyranch-node")
			for _, field := range []byte{2, 3, 4} {
				row = append(row, fieldBytes(field, []byte("synthetic-key-canary"))...)
			}
			row = append(row, fieldBytes(11, fieldBytes(3, []byte("synthetic-key-canary")))...)
			w.Header().Set("Grpc-Message", "synthetic-key-canary")
			reply(w, fieldBytes(1, row), "0")
		})
		log.SetOutput(os.Stdout)
		os.Exit(command([]string{"list-nodes", "1000"}, os.Stdin, os.Stdout, s))
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, level := range []string{"1", "2"} {
		t.Run(level, func(t *testing.T) {
			child := exec.Command(executable, "-test.run=^TestFixedAdminDiagnosticsStartup$")
			child.Env = append(os.Environ(), "GODEBUG=http2debug="+level, "HAPPYRANCH_HOME_ADMIN_B3_TEST=1", "TMPDIR="+t.TempDir())
			child.Stdin = bytes.NewReader(fieldBytes(1, []byte("happyranch-node")))
			var out, diagnostics bytes.Buffer
			child.Stdout, child.Stderr = &out, &diagnostics
			if child.Run() != nil {
				t.Fatal("runner startup failed")
			}
			if diagnostics.Len() != 0 || !bytes.Equal(out.Bytes(), fieldBytes(1, adminNode(7, "happyranch-node"))) {
				t.Fatal("key material escaped projection/log suppression")
			}
		})
	}
}
