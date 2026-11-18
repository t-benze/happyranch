// An inert, fixed-socket root runner. Secrets enter/leave only anonymous pipes.
package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"io"
	"log"
	"net/http"
	"os"
	"os/user"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/net/http2"
)

const maxResponse = 128 << 20
const statePath = "var/lib/happyranch-remote-access/home-minter"

type machine struct {
	root                             string
	uid, gid, serviceUID, serviceGID uint32
	now                              func() time.Time
}

func main() {
	s := machine{root: "/", now: time.Now}
	u, err := user.Lookup("happyranch-headscale")
	if err != nil || os.Geteuid() != 0 {
		os.Exit(2)
	}
	uid, e1 := strconv.ParseUint(u.Uid, 10, 32)
	gid, e2 := strconv.ParseUint(u.Gid, 10, 32)
	if e1 != nil || e2 != nil || uid == 0 || gid == 0 {
		os.Exit(2)
	}
	s.serviceUID, s.serviceGID = uint32(uid), uint32(gid)
	os.Exit(command(os.Args[1:], os.Stdin, os.Stdout, s))
}

// 2 proves no RPC was sent; 3 is uncertain. No underlying error or stream echo.
func command(args []string, in io.Reader, out io.Writer, s machine) int {
	// x/net initializes separate frame flags from GODEBUG before main runs.
	// Disable their standard-log sink before any secret is read or RPC starts,
	// regardless of those flags or the logger's inherited stdout/stderr target.
	log.SetOutput(io.Discard)
	http2.VerboseLogs = false
	if len(args) != 2 {
		return 2
	}
	verb := args[0]
	ms, err := strconv.Atoi(args[1])
	if err != nil || ms < 1 || ms > 5000 {
		return 2
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(ms)*time.Millisecond)
	defer cancel()
	data, err := io.ReadAll(io.LimitReader(in, 4097))
	if err != nil || len(data) > 4096 {
		return 2
	}
	name, deadline, ok := request(verb, data, s.now())
	if !ok {
		return 2
	}
	dir, err := openState(s, name)
	if err != nil {
		return 2
	}
	defer dir.Close()
	lock, err := fileAt(dir, "rpc.lock", s, true)
	if err != nil {
		return 2
	}
	defer lock.Close()
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return 2
	}
	if verb != "expire" && !settled(dir, s) {
		return 2
	}
	conn, err := connect(ctx, s)
	if err != nil {
		return 2
	}
	defer conn.Close()
	if verb == "create" && !fence(dir, deadline, s) {
		return 2
	}
	if ctx.Err() != nil {
		return 2
	}
	payload := make([]byte, 5, len(data)+5)
	binary.BigEndian.PutUint32(payload[1:], uint32(len(data)))
	payload = append(payload, data...)
	method := map[string]string{"create": "CreatePreAuthKey", "list": "ListPreAuthKeys", "expire": "ExpirePreAuthKey"}[verb]
	req, err := http.NewRequestWithContext(ctx, "POST", "http://localhost/headscale.v1.HeadscaleService/"+method, bytes.NewReader(payload))
	if err != nil {
		return 2
	}
	req.Header.Set("Content-Type", "application/grpc")
	req.Header.Set("TE", "trailers")
	req.Header.Set("grpc-timeout", strconv.Itoa(ms)+"m")
	// A single ClientConn avoids Transport.RoundTrip's automatic retries.
	until, _ := ctx.Deadline()
	if conn.SetDeadline(until) != nil {
		return 2
	}
	transport := &http2.Transport{}
	client, err := transport.NewClientConn(conn)
	if err != nil {
		return 2
	}
	defer client.Close()
	resp, err := client.RoundTrip(req)
	if err != nil {
		return 3
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponse+6))
	statuses := append(append([]string{}, resp.Header.Values("Grpc-Status")...), resp.Trailer.Values("Grpc-Status")...)
	if err != nil || ctx.Err() != nil || resp.StatusCode != 200 || len(statuses) != 1 || statuses[0] != "0" ||
		(resp.Header.Get("Content-Type") != "application/grpc" && resp.Header.Get("Content-Type") != "application/grpc+proto") ||
		(resp.Header.Get("Grpc-Encoding") != "" && resp.Header.Get("Grpc-Encoding") != "identity") ||
		len(body) < 5 || len(body) > maxResponse+5 || body[0] != 0 || uint64(binary.BigEndian.Uint32(body[1:5])) != uint64(len(body)-5) {
		return 3
	}
	// A complete successful unary reply proves this handler finished. Otherwise
	// retain the fence until the requested deadline, even across process death.
	if verb == "create" && (syscall.Unlinkat(int(dir.Fd()), "fence") != nil || dir.Sync() != nil) {
		return 3
	}
	if n, err := out.Write(body[5:]); err != nil || n != len(body)-5 {
		return 3
	}
	if ctx.Err() != nil {
		return 3
	}
	return 0
}

// Only canonical, bounded wire values. No unknown fields or duplicate singulars.
type field struct {
	number, wire uint64
	value        uint64
	data         []byte
}

func fields(data []byte) ([]field, bool) {
	result := []field{}
	for len(data) > 0 {
		tag, n := binary.Uvarint(data)
		if n <= 0 || n != varintSize(tag) || tag>>3 == 0 {
			return nil, false
		}
		data = data[n:]
		f := field{number: tag >> 3, wire: tag & 7}
		switch f.wire {
		case 0:
			v, n := binary.Uvarint(data)
			if n <= 0 || n != varintSize(v) {
				return nil, false
			}
			f.value = v
			data = data[n:]
		case 2:
			size, n := binary.Uvarint(data)
			if n <= 0 || n != varintSize(size) || size > uint64(len(data)-n) {
				return nil, false
			}
			data = data[n:]
			f.data = data[:int(size)]
			data = data[int(size):]
		default:
			return nil, false
		}
		result = append(result, f)
		if len(result) > 16 {
			return nil, false
		}
	}
	return result, true
}
func varintSize(v uint64) int {
	n := 1
	for v >= 128 {
		n++
		v >>= 7
	}
	return n
}
func request(verb string, data []byte, now time.Time) (string, int64, bool) {
	fs, ok := fields(data)
	if !ok {
		return "", 0, false
	}
	seen := map[uint64]bool{}
	name := ""
	deadline := int64(0)
	for _, f := range fs {
		if seen[f.number] {
			return "", 0, false
		}
		seen[f.number] = true
		switch {
		case f.number == 1 && f.wire == 2:
			name = string(f.data)
		case verb == "expire" && f.number == 2 && f.wire == 2:
			if len(f.data) != 48 || strings.Trim(string(f.data), "0123456789abcdef") != "" {
				return "", 0, false
			}
		case verb == "create" && (f.number == 2 || f.number == 3) && f.wire == 0 && f.value == 0:
		case verb == "create" && f.number == 4 && f.wire == 2:
			ts, valid := fields(f.data)
			if !valid {
				return "", 0, false
			}
			stamps := map[uint64]bool{}
			for _, t := range ts {
				if stamps[t.number] || t.wire != 0 {
					return "", 0, false
				}
				stamps[t.number] = true
				if t.number == 1 && t.value <= 253402300799 {
					deadline = int64(t.value)
				} else if t.number != 2 || t.value != 0 {
					return "", 0, false
				}
			}
		default:
			return "", 0, false
		}
	}
	if name != "happyranch-invite" && name != "happyranch-node" {
		return "", 0, false
	}
	if verb == "list" {
		return name, 0, len(fs) == 1
	}
	if verb == "expire" {
		return name, 0, seen[2] && len(fs) == 2
	}
	if verb != "create" || !seen[4] || deadline <= now.Unix() || time.Unix(deadline, 0).After(now.Add(300*time.Second)) {
		return "", 0, false
	}
	return name, deadline, true
}
