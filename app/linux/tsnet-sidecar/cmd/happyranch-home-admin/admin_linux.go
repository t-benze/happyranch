package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"io"
	"net/http"
	"net/netip"
	"os"
	"strconv"
	"syscall"
	"time"
	"unicode/utf8"

	"golang.org/x/net/http2"
)

const adminMaxResponse = 4 << 20

func fixedUser(name string) bool { return name == "happyranch-node" || name == "happyranch-invite" }
func wireBytes(number byte, data []byte) []byte {
	out := binary.AppendUvarint(nil, uint64(number)*8+2)
	out = binary.AppendUvarint(out, uint64(len(data)))
	return append(out, data...)
}

// This scanner is separate so the pre-auth-key request parser remains unchanged.
// The enclosing response/record bounds cap work; schema owners bound repetitions.
func adminFields(data []byte, types map[uint64]uint64, repeated map[uint64]bool) (map[uint64][]field, bool) {
	out := map[uint64][]field{}
	for len(data) > 0 {
		tag, n := binary.Uvarint(data)
		if n <= 0 || n != varintSize(tag) || tag>>3 == 0 || tag>>3 >= 1<<29 {
			return nil, false
		}
		data = data[n:]
		f := field{number: tag >> 3, wire: tag & 7}
		wire, known := types[f.number]
		if !known || wire != f.wire || (!repeated[f.number] && len(out[f.number]) > 0) {
			return nil, false
		}
		v, n := binary.Uvarint(data)
		if n <= 0 || n != varintSize(v) {
			return nil, false
		}
		data = data[n:]
		if wire == 2 {
			if v > uint64(len(data)) {
				return nil, false
			}
			f.data, data = data[:int(v)], data[int(v):]
		} else {
			f.value = v
		}
		out[f.number] = append(out[f.number], f)
	}
	return out, true
}
func stampValid(data []byte) bool {
	fs, ok := adminFields(data, map[uint64]uint64{1: 0, 2: 0}, nil)
	if !ok {
		return false
	}
	var seconds int64
	if len(fs[1]) > 0 {
		seconds = int64(fs[1][0].value)
	}
	return seconds >= -62135596800 && seconds <= 253402300799 && (len(fs[2]) == 0 || fs[2][0].value <= 999999999)
}

type dbUser struct {
	id   uint64
	name string
	wire []byte
}

func projectUser(data []byte) (dbUser, bool) {
	if len(data) > 4096 {
		return dbUser{}, false
	}
	fs, ok := adminFields(data, map[uint64]uint64{1: 0, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2}, nil)
	if !ok || len(fs[1]) != 1 || len(fs[2]) != 1 || fs[1][0].value == 0 {
		return dbUser{}, false
	}
	for number, fields := range fs {
		if number == 3 {
			if !stampValid(fields[0].data) {
				return dbUser{}, false
			}
		} else if number != 1 && !utf8.Valid(fields[0].data) {
			return dbUser{}, false
		}
	}
	u := dbUser{id: fs[1][0].value, name: string(fs[2][0].data)}
	if !fixedUser(u.name) {
		return dbUser{}, false
	}
	// Only nonsecret identity crosses the runner's output pipe.
	u.wire = append(binary.AppendUvarint([]byte{8}, u.id), wireBytes(2, []byte(u.name))...)
	return u, true
}

// Bound the outer row count and each record before allocating its inventory.
func adminRows(data []byte, limit int) ([][]byte, bool) {
	rows := [][]byte{}
	for len(data) > 0 {
		if len(rows) >= limit || data[0] != 10 {
			return nil, false
		}
		data = data[1:]
		size, n := binary.Uvarint(data)
		if n <= 0 || n != varintSize(size) || size > 4096 || size > uint64(len(data)-n) {
			return nil, false
		}
		data = data[n:]
		rows = append(rows, data[:int(size)])
		data = data[int(size):]
	}
	return rows, true
}
func projectUsers(data []byte) ([]dbUser, bool) {
	rows, ok := adminRows(data, 64)
	if !ok {
		return nil, false
	}
	users := []dbUser{}
	ids, names := map[uint64]bool{}, map[string]bool{}
	for _, row := range rows {
		u, ok := projectUser(row)
		if !ok || ids[u.id] || names[u.name] {
			return nil, false
		}
		ids[u.id], names[u.name] = true, true
		users = append(users, u)
	}
	return users, true
}
func preAuthValid(data []byte) bool {
	if len(data) > 4096 {
		return false
	}
	fs, ok := adminFields(data, map[uint64]uint64{1: 2, 2: 2, 3: 2, 4: 0, 5: 0, 6: 0, 7: 2, 8: 2, 9: 2}, map[uint64]bool{9: true})
	if !ok {
		return false
	}
	for number, fields := range fs {
		for _, f := range fields {
			if number >= 4 && number <= 6 {
				if f.value > 1 {
					return false
				}
			} else if number == 7 || number == 8 {
				if !stampValid(f.data) {
					return false
				}
			} else if !utf8.Valid(f.data) {
				return false
			}
		}
	}
	return true
}
func projectNodes(data []byte, name string) ([]byte, int, bool) {
	rows, ok := adminRows(data, 512)
	if !ok {
		return nil, 0, false
	}
	out := []byte{}
	ids := map[uint64]bool{}
	var userID uint64
	for _, row := range rows {
		node, ok := adminFields(row, map[uint64]uint64{1: 0, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 10: 2, 11: 2, 12: 2, 13: 0, 18: 2, 19: 2, 20: 2, 21: 2, 22: 0}, map[uint64]bool{5: true, 18: true, 19: true, 20: true})
		if !ok || len(node[1]) != 1 || node[1][0].value == 0 || len(node[7]) != 1 {
			return nil, 0, false
		}
		u, ok := projectUser(node[7][0].data)
		id := node[1][0].value
		if !ok || u.name != name || ids[id] || (userID != 0 && userID != u.id) {
			return nil, 0, false
		}
		ids[id], userID = true, u.id
		for number, fields := range node {
			for _, value := range fields {
				switch number {
				case 1, 7:
				case 8, 10, 12:
					if !stampValid(value.data) {
						return nil, 0, false
					}
				case 11:
					if !preAuthValid(value.data) {
						return nil, 0, false
					}
				case 13:
					if value.value > 3 {
						return nil, 0, false
					}
				case 22:
					if value.value > 1 {
						return nil, 0, false
					}
				default:
					if !utf8.Valid(value.data) {
						return nil, 0, false
					}
				}
			}
		}
		if len(node[3]) != 1 || len(node[3][0].data) == 0 || len(node[5]) > 16 {
			return nil, 0, false
		}
		digest := sha256.Sum256(node[3][0].data)
		row := append(binary.AppendUvarint([]byte{8}, id), wireBytes(7, u.wire)...)
		row = append(row, wireBytes(3, []byte(hex.EncodeToString(digest[:])))...)
		seen := map[string]bool{}
		for _, value := range node[5] {
			ip, err := netip.ParseAddr(string(value.data))
			if err != nil || ip.Zone() != "" || ip.Is4In6() || seen[ip.String()] {
				return nil, 0, false
			}
			seen[ip.String()] = true
			row = append(row, wireBytes(5, []byte(ip.String()))...)
		}
		out = append(out, wireBytes(1, row)...)
	}
	return out, len(rows), true
}

// Fixed RPCs only, with no retry, stream echo, server message or generic dispatch.
// 2 proves this RPC was not sent; all post-send failures are 3.
func adminRPC(ctx context.Context, s machine, verb string, data []byte, dir *os.File) ([]byte, int) {
	method, limit := "", adminMaxResponse
	switch verb {
	case "list-users":
		method = "ListUsers"
	case "create-user":
		method = "CreateUser"
	case "list-nodes":
		method = "ListNodes"
	case "reconcile-keys":
		method, limit = "ListPreAuthKeys", maxResponse
	default:
		return nil, 2
	}
	conn, err := connect(ctx, s)
	if err != nil {
		return nil, 2
	}
	defer conn.Close()
	until, _ := ctx.Deadline()
	if conn.SetDeadline(until) != nil {
		return nil, 2
	}
	client, err := (&http2.Transport{}).NewClientConn(conn)
	if err != nil {
		return nil, 2
	}
	defer client.Close()
	frame := make([]byte, 5, len(data)+5)
	binary.BigEndian.PutUint32(frame[1:], uint32(len(data)))
	frame = append(frame, data...)
	req, err := http.NewRequestWithContext(ctx, "POST", "http://localhost/headscale.v1.HeadscaleService/"+method, bytes.NewReader(frame))
	if err != nil {
		return nil, 2
	}
	req.Header.Set("Content-Type", "application/grpc")
	req.Header.Set("TE", "trailers")
	remaining := time.Until(until)
	if remaining <= 0 {
		return nil, 2
	}
	req.Header.Set("grpc-timeout", strconv.FormatInt(max(1, remaining.Milliseconds()), 10)+"m")
	if verb == "create-user" && !reserveUserCreate(dir, s) {
		return nil, 2
	}
	if ctx.Err() != nil {
		return nil, 2
	}
	resp, err := client.RoundTrip(req)
	if err != nil {
		return nil, 3
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, int64(limit)+6))
	statuses := append(append([]string{}, resp.Header.Values("Grpc-Status")...), resp.Trailer.Values("Grpc-Status")...)
	if err != nil || ctx.Err() != nil || resp.StatusCode != 200 || len(statuses) != 1 || statuses[0] != "0" ||
		(resp.Header.Get("Content-Type") != "application/grpc" && resp.Header.Get("Content-Type") != "application/grpc+proto") ||
		(resp.Header.Get("Grpc-Encoding") != "" && resp.Header.Get("Grpc-Encoding") != "identity") ||
		len(body) < 5 || len(body) > limit+5 || body[0] != 0 || uint64(binary.BigEndian.Uint32(body[1:5])) != uint64(len(body)-5) {
		return nil, 3
	}
	return body[5:], 0
}

// An insert-only, nonsecret attempt marker prevents retrying a lost create that
// may still commit. It is never cleared, repaired or adopted after a torn write.
func userCreateAttempt(dir *os.File, s machine) int {
	f, err := fileAt(dir, "user-create", s, false)
	if err == syscall.ENOENT {
		return 0
	}
	if err != nil {
		return -1
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 2))
	if err != nil || !bytes.Equal(b, []byte("1")) {
		return -1
	}
	return 1
}
func reserveUserCreate(dir *os.File, s machine) bool {
	fd, err := syscall.Openat(int(dir.Fd()), "user-create", syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return false
	}
	f := os.NewFile(uintptr(fd), "user-create")
	defer f.Close()
	if syscall.Fchown(fd, int(s.uid), int(s.gid)) != nil || syscall.Fchmod(fd, 0600) != nil {
		return false
	}
	n, err := f.Write([]byte("1"))
	return err == nil && n == 1 && f.Sync() == nil && dir.Sync() == nil
}
func userEmpty(ctx context.Context, s machine, name string) bool {
	data := wireBytes(1, []byte(name))
	body, code := adminRPC(ctx, s, "list-nodes", data, nil)
	if code != 0 {
		return false
	}
	_, count, ok := projectNodes(body, name)
	if !ok || count != 0 || ctx.Err() != nil {
		return false
	}
	body, code = adminRPC(ctx, s, "reconcile-keys", data, nil)
	// Existing fences contain no key IDs: no encountered key can be proven ours.
	return code == 0 && len(body) == 0 && ctx.Err() == nil
}
func createFixedUser(ctx context.Context, s machine, name string, data []byte) ([]byte, int) {
	dir, err := openState(s, name)
	if err != nil {
		return nil, 2
	}
	defer dir.Close()
	lock, err := fileAt(dir, "rpc.lock", s, true)
	if err != nil {
		return nil, 2
	}
	defer lock.Close()
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return nil, 2
	}
	attempt := userCreateAttempt(dir, s)
	category := 2
	if attempt != 0 {
		category = 3
	}
	if attempt < 0 || !settled(dir, s) {
		return nil, category
	}
	body, code := adminRPC(ctx, s, "list-users", nil, nil)
	users, ok := projectUsers(body)
	if code != 0 || !ok || ctx.Err() != nil {
		return nil, category
	}
	for _, u := range users {
		if u.name == name {
			if !userEmpty(ctx, s, name) {
				return nil, category
			}
			return wireBytes(1, u.wire), 0
		}
	}
	if attempt != 0 {
		return nil, 3
	}
	body, code = adminRPC(ctx, s, "create-user", data, dir)
	if code == 2 {
		return nil, 2
	}
	var createdID uint64
	if code == 0 {
		fs, valid := adminFields(body, map[uint64]uint64{1: 2}, nil)
		if !valid || len(fs[1]) != 1 {
			return nil, 3
		}
		u, valid := projectUser(fs[1][0].data)
		if !valid || u.name != name {
			return nil, 3
		}
		createdID = u.id
	}
	// Lost reply is never a second create. Reconcile a complete global list,
	// then verify exactly the named user is empty under the same original budget.
	body, code = adminRPC(ctx, s, "list-users", nil, nil)
	users, ok = projectUsers(body)
	if code != 0 || !ok || ctx.Err() != nil {
		return nil, 3
	}
	for _, u := range users {
		if u.name == name && (createdID == 0 || createdID == u.id) && userEmpty(ctx, s, name) {
			return wireBytes(1, u.wire), 0
		}
	}
	return nil, 3
}
func adminCommand(ctx context.Context, verb string, data []byte, out io.Writer, s machine) int {
	name := ""
	if verb == "list-users" {
		if len(data) != 0 {
			return 2
		}
	} else {
		fs, ok := fields(data)
		if !ok || len(fs) != 1 || fs[0].number != 1 || fs[0].wire != 2 || !fixedUser(string(fs[0].data)) {
			return 2
		}
		name = string(fs[0].data)
	}
	var body []byte
	var code int
	if verb == "create-user" {
		body, code = createFixedUser(ctx, s, name, data)
	} else {
		body, code = adminRPC(ctx, s, verb, data, nil)
		if code == 0 {
			if verb == "list-users" {
				users, ok := projectUsers(body)
				if !ok {
					return 3
				}
				body = nil
				for _, u := range users {
					body = append(body, wireBytes(1, u.wire)...)
				}
			} else {
				var ok bool
				body, _, ok = projectNodes(body, name)
				if !ok {
					return 3
				}
			}
		}
	}
	if code != 0 {
		return code
	}
	if ctx.Err() != nil {
		return 3
	}
	if n, err := out.Write(body); err != nil || n != len(body) || ctx.Err() != nil {
		return 3
	}
	return 0
}
