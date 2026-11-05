//go:build linux

package sidecar

// Private package images are a cooperating-root custody protocol, not sealed
// files or protection against hostile root. No ready image is reclaimed here.
import (
	"context"
	"crypto/sha256"
	"io"
	"os"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

const (
	lifecycleImageRoot        = "happyranch-connector-images"
	lifecycleImageLock        = ".allocation.lock"
	lifecycleImageLimit int64 = 256 << 20
	lifecycleImageTotal int64 = 512 << 20
)

type lifecycleImageSite int

const (
	lifecycleImageAdmission lifecycleImageSite = iota
	lifecycleImageStoppedAdmission
	lifecycleImageBody
)

var lifecycleImageSites = [...]string{"admission", "stopped-admission", "body"}

type lifecyclePackageImage struct {
	name             string
	directory, image unix.Stat_t
	// Selection is single-use even when validation fails. possibleStart is set
	// immediately before Start, but neither flag licenses image reclamation.
	selected, possibleStart bool
}

type lifecyclePackageImages struct {
	boot, operation string
	ancestry        [3]unix.Stat_t
	members         [3]lifecyclePackageImage
	size            int64
	digest          [32]byte
	ready           bool
}

// Each owner attempts close once, including on error. A failed close is never
// retried and prevents pre-ready unwind as well as readiness.
type lifecycleImageFD struct {
	file      *os.File
	attempted bool
	closeErr  error
}

func lifecycleImageOwn(fd int) *lifecycleImageFD {
	return &lifecycleImageFD{file: os.NewFile(uintptr(fd), "private-package-image")}
}
func (f *lifecycleImageFD) close() error {
	if !f.attempted {
		f.attempted = true
		f.closeErr = f.file.Close()
		lifecycleDiagnosticLoss("G500006", f.closeErr)
	}
	return lifecycleDiagnosticError("G000445", f.closeErr)
}
func (f *lifecycleImageFD) fd() int { return int(f.file.Fd()) }

func lifecycleImageSame(a, b unix.Stat_t) bool {
	return a.Dev == b.Dev && a.Ino == b.Ino
}

func lifecycleImageLive(ctx context.Context) bool {
	deadline, bounded := ctx.Deadline()
	return bounded && ctx.Err() == nil && time.Now().Before(deadline)
}

func lifecycleImageFilesystem(f *lifecycleImageFD) bool {
	var fs unix.Statfs_t
	return unix.Fstatfs(f.fd(), &fs) == nil && fs.Type == unix.TMPFS_MAGIC && fs.Flags&(unix.ST_RDONLY|unix.ST_NOEXEC) == 0
}
func lifecycleImageStat(f *lifecycleImageFD) (unix.Stat_t, error) {
	var st unix.Stat_t
	if unix.Fstat(f.fd(), &st) != nil {
		return st, lifecycleDiagnosticError("G000446", ErrLifecycle)
	}
	return st, nil
}
func lifecycleImageEntry(parent *lifecycleImageFD, name string, held unix.Stat_t) bool {
	var st unix.Stat_t
	return unix.Fstatat(parent.fd(), name, &st, unix.AT_SYMLINK_NOFOLLOW) == nil &&
		lifecycleImageSame(st, held) && st.Mode == held.Mode && st.Uid == held.Uid &&
		st.Gid == held.Gid && st.Nlink == held.Nlink && st.Size == held.Size
}
func lifecycleImageDirectory(st unix.Stat_t, mode uint32) bool {
	return st.Mode&unix.S_IFMT == unix.S_IFDIR && st.Uid == 0 && st.Gid == 0 &&
		st.Mode&07777 == mode
}
func lifecycleImageRegular(st unix.Stat_t, mode uint32, dev uint64) bool {
	return st.Mode&unix.S_IFMT == unix.S_IFREG && st.Uid == 0 && st.Gid == 0 &&
		st.Mode&07777 == mode && st.Nlink == 1 && st.Dev == dev
}
func lifecycleImageOpenDirectory(parent *lifecycleImageFD, name string) (*lifecycleImageFD, error) {
	fd, err := unix.Openat(parent.fd(), name, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000447", ErrLifecycle, err)
	}
	return lifecycleImageOwn(fd), nil
}

type lifecycleImageTree struct {
	root, run, base *lifecycleImageFD
	settled         bool
}

func (t *lifecycleImageTree) close() bool {
	for _, f := range []*lifecycleImageFD{t.base, t.run, t.root} {
		if f != nil && f.close() != nil {
			t.settled = false
		}
	}
	return t.settled
}
func (t *lifecycleImageTree) validate(ctx context.Context, expected *[3]unix.Stat_t) ([3]unix.Stat_t, error) {
	var got [3]unix.Stat_t
	if !lifecycleImageLive(ctx) {
		return got, lifecycleDiagnosticError("G000448", ErrLifecycle)
	}
	for i, f := range []*lifecycleImageFD{t.root, t.run, t.base} {
		st, err := lifecycleImageStat(f)
		if err != nil {
			return got, lifecycleDiagnosticError("G000449", ErrLifecycle, err)
		}
		got[i] = st
		if i < 2 {
			if st.Mode&unix.S_IFMT != unix.S_IFDIR || st.Uid != 0 || st.Gid != 0 || st.Mode&07022 != 0 {
				return got, lifecycleDiagnosticError("G000450", ErrLifecycle)
			}
		} else if !lifecycleImageDirectory(st, 0700) || st.Dev != got[1].Dev {
			return got, lifecycleDiagnosticError("G000451", ErrLifecycle)
		}
		if expected != nil && !lifecycleImageSame(st, expected[i]) {
			return got, lifecycleDiagnosticError("G000452", ErrLifecycle)
		}
	}
	// Compare literal / again, so an old detached root FD is not sufficient.
	var rootNow unix.Stat_t
	if unix.Lstat("/", &rootNow) != nil || !lifecycleImageSame(rootNow, got[0]) ||
		!lifecycleImageEntry(t.root, "run", got[1]) || !lifecycleImageEntry(t.run, lifecycleImageRoot, got[2]) {
		return got, lifecycleDiagnosticError("G000453", ErrLifecycle)
	}
	if !lifecycleImageFilesystem(t.run) || !lifecycleImageFilesystem(t.base) || !lifecycleImageLive(ctx) {
		return got, lifecycleDiagnosticError("G000454", ErrLifecycle)
	}
	return got, nil
}
func lifecycleImageOpenTree(ctx context.Context, create bool) (tree *lifecycleImageTree, failure error) {
	tree = &lifecycleImageTree{settled: true}
	defer func() {
		if failure != nil {
			tree.close()
		}
	}()
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return tree, lifecycleDiagnosticError("G000455", ErrLifecycle, err)
	}
	tree.root = lifecycleImageOwn(fd)
	tree.run, err = lifecycleImageOpenDirectory(tree.root, "run")
	if err != nil {
		return tree, lifecycleDiagnosticError("G000456", ErrLifecycle, err)
	}
	// Validate ancestry/mount BEFORE the first mkdir; never repair host metadata.
	for _, f := range []*lifecycleImageFD{tree.root, tree.run} {
		st, e := lifecycleImageStat(f)
		if e != nil || st.Mode&unix.S_IFMT != unix.S_IFDIR || st.Uid != 0 || st.Gid != 0 || st.Mode&07022 != 0 {
			return tree, lifecycleDiagnosticError("G000457", ErrLifecycle)
		}
	}
	var fs unix.Statfs_t
	runStat, e := lifecycleImageStat(tree.run)
	if e != nil || !lifecycleImageEntry(tree.root, "run", runStat) || unix.Fstatfs(tree.run.fd(), &fs) != nil ||
		fs.Type != unix.TMPFS_MAGIC || fs.Flags&(unix.ST_RDONLY|unix.ST_NOEXEC) != 0 || !lifecycleImageLive(ctx) {
		return tree, lifecycleDiagnosticError("G000458", ErrLifecycle)
	}
	tree.base, err = lifecycleImageOpenDirectory(tree.run, lifecycleImageRoot)
	if err != nil && create {
		// Only a genuinely absent literal entry may be created. EEXIST races
		// refuse instead of adopting an object created by another operation.
		var absent unix.Stat_t
		if unix.Fstatat(tree.run.fd(), lifecycleImageRoot, &absent, unix.AT_SYMLINK_NOFOLLOW) != unix.ENOENT ||
			unix.Mkdirat(tree.run.fd(), lifecycleImageRoot, 0700) != nil {
			return tree, lifecycleDiagnosticError("G000459", ErrLifecycle)
		}
		tree.base, err = lifecycleImageOpenDirectory(tree.run, lifecycleImageRoot)
	}
	if err != nil {
		return tree, lifecycleDiagnosticError("G000460", ErrLifecycle, err)
	}
	_, err = tree.validate(ctx, nil)
	if err != nil {
		return tree, lifecycleDiagnosticError("G000461", ErrLifecycle, err)
	}
	return tree, nil
}

func lifecycleImageAllocationLock(ctx context.Context, tree *lifecycleImageTree) (*lifecycleImageFD, error) {
	if !lifecycleImageLive(ctx) {
		return nil, lifecycleDiagnosticError("G000462", ErrLifecycle)
	}
	var before unix.Stat_t
	err := unix.Fstatat(tree.base.fd(), lifecycleImageLock, &before, unix.AT_SYMLINK_NOFOLLOW)
	flags := unix.O_RDWR | unix.O_NOFOLLOW | unix.O_CLOEXEC | unix.O_NONBLOCK
	if err == unix.ENOENT {
		flags |= unix.O_CREAT | unix.O_EXCL
	} else if err != nil || !lifecycleImageRegular(before, 0600, before.Dev) || before.Size != 0 {
		return nil, lifecycleDiagnosticError("G000463", ErrLifecycle)
	}
	fd, err := unix.Openat(tree.base.fd(), lifecycleImageLock, flags, 0600)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000464", ErrLifecycle, err)
	}
	f := lifecycleImageOwn(fd)
	st, e := lifecycleImageStat(f)
	base, be := lifecycleImageStat(tree.base)
	if e != nil || be != nil || !lifecycleImageRegular(st, 0600, base.Dev) || st.Size != 0 ||
		flags&unix.O_EXCL == 0 && !lifecycleImageSame(st, before) || !lifecycleImageEntry(tree.base, lifecycleImageLock, st) ||
		unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB) != nil || !lifecycleImageLive(ctx) {
		if f.close() != nil {
			tree.settled = false
		}
		return nil, lifecycleDiagnosticError("G000465", ErrLifecycle)
	}
	return f, nil
}

// Read at most limit entries, with the final entry reserved as an overflow
// sentinel. Every enumeration uses a newly opened directory OFD.
func lifecycleImageNames(ctx context.Context, parent *lifecycleImageFD, limit int) (names []string, failure error) {
	f, err := lifecycleImageOpenDirectory(parent, ".")
	if err != nil {
		return nil, lifecycleDiagnosticError("G000466", ErrLifecycle, err)
	}
	defer func() {
		if f.close() != nil {
			failure = lifecycleDiagnosticError("G000467", ErrLifecycle, failure)
		}
	}()
	entries, err := f.file.ReadDir(limit)
	if err != nil && err != io.EOF || len(entries) >= limit || !lifecycleImageLive(ctx) {
		return nil, lifecycleDiagnosticError("G000468", ErrLifecycle)
	}
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	return names, nil
}
func lifecycleImageGeneration(name, boot string) bool {
	parts := strings.Split(name, ".")
	return len(parts) == 3 && parts[0] == boot && lifecycleID(parts[1]) &&
		lifecycleOneOf(parts[2], lifecycleImageSites[:]...)
}
func lifecycleImageCount(ctx context.Context, tree *lifecycleImageTree, boot string) (slots int, bytes int64, failure error) {
	names, err := lifecycleImageNames(ctx, tree.base, 34)
	if err != nil {
		return 0, 0, lifecycleDiagnosticError("G000469", ErrLifecycle, err)
	}
	base, err := lifecycleImageStat(tree.base)
	if err != nil {
		return 0, 0, lifecycleDiagnosticError("G000470", ErrLifecycle, err)
	}
	lockSeen := false
	for _, name := range names {
		if !lifecycleImageLive(ctx) {
			return 0, 0, lifecycleDiagnosticError("G000471", ErrLifecycle)
		}
		if name == lifecycleImageLock {
			lockSeen = true
			continue
		}
		if !lifecycleImageGeneration(name, boot) {
			return 0, 0, lifecycleDiagnosticError("G000472", ErrLifecycle)
		}
		slots++
		if slots > 32 {
			return 0, 0, lifecycleDiagnosticError("G000473", ErrLifecycle)
		}
		var size int64
		err := func() (failure error) {
			dir, e := lifecycleImageOpenDirectory(tree.base, name)
			if e != nil {
				return lifecycleDiagnosticError("G000474", ErrLifecycle, e)
			}
			defer func() {
				if dir.close() != nil {
					tree.settled = false
					failure = lifecycleDiagnosticError("G000475", ErrLifecycle, failure)
				}
			}()
			st, e := lifecycleImageStat(dir)
			if e != nil || !(lifecycleImageDirectory(st, 0700) || lifecycleImageDirectory(st, 0500)) || st.Dev != base.Dev || !lifecycleImageFilesystem(dir) ||
				!lifecycleImageEntry(tree.base, name, st) {
				return lifecycleDiagnosticError("G000476", ErrLifecycle)
			}
			children, e := lifecycleImageNames(ctx, dir, 2)
			if e != nil {
				tree.settled = false
				return lifecycleDiagnosticError("G000477", ErrLifecycle)
			}
			if len(children) == 1 {
				var image unix.Stat_t
				if children[0] != "image" || unix.Fstatat(dir.fd(), "image", &image, unix.AT_SYMLINK_NOFOLLOW) != nil ||
					!(lifecycleImageRegular(image, 0600, base.Dev) || lifecycleImageRegular(image, 0500, base.Dev)) ||
					image.Size < 0 || image.Size > lifecycleImageLimit {
					return lifecycleDiagnosticError("G000478", ErrLifecycle)
				}
				size = image.Size
			}
			if !lifecycleImageEntry(tree.base, name, st) {
				return lifecycleDiagnosticError("G000479", ErrLifecycle)
			}
			return nil
		}()
		if err != nil || size > lifecycleImageTotal-bytes {
			return 0, 0, lifecycleDiagnosticError("G000480", ErrLifecycle)
		}
		bytes += size
	}
	if !lockSeen || !lifecycleImageLive(ctx) {
		return 0, 0, lifecycleDiagnosticError("G000481", ErrLifecycle)
	}
	return slots, bytes, nil
}

func lifecycleImageSource(source *os.File) (unix.Stat_t, error) {
	var st unix.Stat_t
	seals, err := unix.FcntlInt(source.Fd(), unix.F_GET_SEALS, 0)
	required := unix.F_SEAL_WRITE | unix.F_SEAL_GROW | unix.F_SEAL_SHRINK | unix.F_SEAL_SEAL
	if err != nil || seals&required != required || unix.Fstat(int(source.Fd()), &st) != nil ||
		st.Mode&unix.S_IFMT != unix.S_IFREG || st.Uid != 0 || st.Gid != 0 || st.Nlink != 0 || st.Size <= 0 || st.Size > lifecycleImageLimit {
		return st, lifecycleDiagnosticError("G000482", ErrLifecycle)
	}
	return st, nil
}
func lifecycleImageHash(ctx context.Context, file *os.File, size int64, writer *os.File) (digest [32]byte, failure error) {
	hash := sha256.New()
	var buffer [64 << 10]byte
	for offset := int64(0); offset < size; {
		if !lifecycleImageLive(ctx) {
			return digest, lifecycleDiagnosticError("G000483", ErrLifecycle)
		}
		length := int64(len(buffer))
		if size-offset < length {
			length = size - offset
		}
		n, err := file.ReadAt(buffer[:int(length)], offset)
		if err != nil || n != int(length) || !lifecycleImageLive(ctx) {
			return digest, lifecycleDiagnosticError("G000484", ErrLifecycle)
		}
		if writer != nil {
			written, e := writer.Write(buffer[:n])
			if e != nil || written != n {
				return digest, lifecycleDiagnosticError("G000485", ErrLifecycle)
			}
		}
		_, _ = hash.Write(buffer[:n])
		offset += int64(n)
	}
	var sentinel [1]byte
	if n, err := file.ReadAt(sentinel[:], size); n != 0 || err != io.EOF || !lifecycleImageLive(ctx) {
		return digest, lifecycleDiagnosticError("G000486", ErrLifecycle)
	}
	copy(digest[:], hash.Sum(nil))
	return digest, nil
}

func lifecycleImageRead(ctx context.Context, tree *lifecycleImageTree, member *lifecyclePackageImage, set *lifecyclePackageImages) (failure error) {
	dir, err := lifecycleImageOpenDirectory(tree.base, member.name)
	if err != nil {
		return lifecycleDiagnosticError("G000487", ErrLifecycle, err)
	}
	defer func() {
		if dir.close() != nil {
			tree.settled = false
			failure = lifecycleDiagnosticError("G000488", ErrLifecycle, failure)
		}
	}()
	ds, err := lifecycleImageStat(dir)
	if err != nil || !lifecycleImageSame(ds, member.directory) || !lifecycleImageDirectory(ds, 0500) || !lifecycleImageFilesystem(dir) ||
		!lifecycleImageEntry(tree.base, member.name, ds) {
		return lifecycleDiagnosticError("G000489", ErrLifecycle)
	}
	var before unix.Stat_t
	if unix.Fstatat(dir.fd(), "image", &before, unix.AT_SYMLINK_NOFOLLOW) != nil ||
		!lifecycleImageRegular(before, 0500, set.ancestry[2].Dev) || before.Size != set.size ||
		!lifecycleImageSame(before, member.image) {
		return lifecycleDiagnosticError("G000490", ErrLifecycle)
	}
	fd, err := unix.Openat(dir.fd(), "image", unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return lifecycleDiagnosticError("G000491", ErrLifecycle, err)
	}
	image := lifecycleImageOwn(fd)
	defer func() {
		if image.close() != nil {
			tree.settled = false
			failure = lifecycleDiagnosticError("G000492", ErrLifecycle, failure)
		}
	}()
	st, err := lifecycleImageStat(image)
	if err != nil || !lifecycleImageRegular(st, 0500, set.ancestry[2].Dev) || st.Size != set.size ||
		!lifecycleImageSame(st, member.image) || !lifecycleImageEntry(dir, "image", st) {
		return lifecycleDiagnosticError("G000493", ErrLifecycle)
	}
	digest, err := lifecycleImageHash(ctx, image.file, set.size, nil)
	if err != nil || digest != set.digest || !lifecycleImageEntry(dir, "image", st) ||
		!lifecycleImageEntry(tree.base, member.name, ds) {
		return lifecycleDiagnosticError("G000494", ErrLifecycle)
	}
	_, err = tree.validate(ctx, &set.ancestry)
	return lifecycleDiagnosticError("G000495", err)
}

// Caller holds the ORIGINAL common A lock throughout this function. Allocation
// flock is acquired second, nonblocking, and released before any child/wait or
// common unlock. No other call site can allocate images.
func lifecyclePreparePackageImages(ctx context.Context, p *LifecyclePair, source *os.File, request lifecyclePackageRequest) (set *lifecyclePackageImages, failure error) {
	if p == nil || p.lock == nil || !lifecycleImageLive(ctx) || !lifecycleID(request.OperationID) {
		return nil, lifecycleDiagnosticError("G000496", ErrLifecycle)
	}
	boot, err := LifecycleBoot()
	if err != nil {
		return nil, lifecycleDiagnosticError("G000497", ErrLifecycle, err)
	}
	sourceStat, err := lifecycleImageSource(source)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000498", ErrLifecycle, err)
	}
	set = &lifecyclePackageImages{boot: boot, operation: request.OperationID, size: sourceStat.Size}
	tree, err := lifecycleImageOpenTree(ctx, true)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000499", ErrLifecycle, err)
	}
	defer func() {
		if !tree.close() || !lifecycleImageLive(ctx) {
			failure = lifecycleDiagnosticError("G000500", ErrLifecycle, failure)
		}
	}()
	allocation, err := lifecycleImageAllocationLock(ctx, tree)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000501", ErrLifecycle, err)
	}
	defer func() {
		if allocation.close() != nil {
			failure = lifecycleDiagnosticError("G000502", ErrLifecycle, failure)
		}
	}()
	set.ancestry, err = tree.validate(ctx, nil)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000503", ErrLifecycle, err)
	}
	slots, bytes, err := lifecycleImageCount(ctx, tree, boot)
	// Subtraction/division checks avoid overflow. Capacity for the ENTIRE triple
	// is admitted before the first mkdir; actual files reserve it across A/B.
	if err != nil || slots > 32-len(set.members) || set.size > (lifecycleImageTotal-bytes)/int64(len(set.members)) {
		return nil, lifecycleDiagnosticError("G000504", ErrLifecycle)
	}
	for i, site := range lifecycleImageSites {
		set.members[i].name = boot + "." + request.OperationID + "." + site
		var st unix.Stat_t
		if unix.Fstatat(tree.base.fd(), set.members[i].name, &st, unix.AT_SYMLINK_NOFOLLOW) != unix.ENOENT || !lifecycleImageLive(ctx) {
			return nil, lifecycleDiagnosticError("G000505", ErrLifecycle)
		}
	}
	set.digest, err = lifecycleImageHash(ctx, source, set.size, nil)
	if err != nil {
		return nil, lifecycleDiagnosticError("G000506", ErrLifecycle, err)
	}
	// Own pre-ready handles and entries only. Failed/unknown closes latch against
	// cleanup. Ready members, peers, parents and the permanent lock never unwind.
	var handles []*lifecycleImageFD
	created := [3]bool{}
	defer func() {
		for i := len(handles) - 1; i >= 0; i-- {
			if handles[i].close() != nil {
				tree.settled = false
				failure = lifecycleDiagnosticError("G000507", ErrLifecycle, failure)
			}
		}
		if failure != nil && !set.ready && tree.settled && lifecycleImageLive(ctx) {
			lifecycleImageUnwind(ctx, tree, set, created)
		}
	}()
	for i := range set.members {
		member := &set.members[i]
		if !lifecycleImageLive(ctx) || unix.Mkdirat(tree.base.fd(), member.name, 0700) != nil {
			return set, lifecycleDiagnosticError("G000508", ErrLifecycle)
		}
		created[i] = true
		dir, e := lifecycleImageOpenDirectory(tree.base, member.name)
		if e != nil {
			return set, lifecycleDiagnosticError("G000509", ErrLifecycle, e)
		}
		handles = append(handles, dir)
		member.directory, e = lifecycleImageStat(dir)
		if e != nil || !lifecycleImageDirectory(member.directory, 0700) || member.directory.Dev != set.ancestry[2].Dev || !lifecycleImageFilesystem(dir) ||
			!lifecycleImageEntry(tree.base, member.name, member.directory) {
			return set, lifecycleDiagnosticError("G000510", ErrLifecycle)
		}
		fd, e := unix.Openat(dir.fd(), "image", unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0600)
		if e != nil {
			return set, lifecycleDiagnosticError("G000511", ErrLifecycle, e)
		}
		writer := lifecycleImageOwn(fd)
		handles = append(handles, writer)
		member.image, e = lifecycleImageStat(writer)
		if e != nil || !lifecycleImageRegular(member.image, 0600, set.ancestry[2].Dev) || member.image.Size != 0 || lifecycleImageSame(member.image, sourceStat) {
			return set, lifecycleDiagnosticError("G000512", ErrLifecycle)
		}
		for j := 0; j < i; j++ {
			if lifecycleImageSame(member.image, set.members[j].image) {
				return set, lifecycleDiagnosticError("G000513", ErrLifecycle)
			}
		}
		digest, e := lifecycleImageHash(ctx, source, set.size, writer.file)
		if e != nil || digest != set.digest || !lifecycleImageLive(ctx) || writer.file.Sync() != nil ||
			!lifecycleImageLive(ctx) || unix.Fchmod(writer.fd(), 0500) != nil || !lifecycleImageLive(ctx) || writer.file.Sync() != nil {
			return set, lifecycleDiagnosticError("G000514", ErrLifecycle)
		}
		// No write, duplicate, mmap, alias, or transfer exists after this one close.
		if writer.close() != nil {
			tree.settled = false
			return set, lifecycleDiagnosticError("G000515", ErrLifecycle)
		}
		if !lifecycleImageLive(ctx) || unix.Fchmod(dir.fd(), 0500) != nil || !lifecycleImageLive(ctx) || dir.file.Sync() != nil {
			return set, lifecycleDiagnosticError("G000516", ErrLifecycle)
		}
		member.directory, e = lifecycleImageStat(dir)
		if e != nil || !lifecycleImageDirectory(member.directory, 0500) {
			return set, lifecycleDiagnosticError("G000517", ErrLifecycle)
		}
		if dir.close() != nil {
			tree.settled = false
			return set, lifecycleDiagnosticError("G000518", ErrLifecycle)
		}
		if lifecycleImageRead(ctx, tree, member, set) != nil {
			return set, lifecycleDiagnosticError("G000519", ErrLifecycle)
		}
	}
	if !lifecycleImageLive(ctx) || tree.base.file.Sync() != nil || !lifecycleImageLive(ctx) || tree.run.file.Sync() != nil {
		return set, lifecycleDiagnosticError("G000520", ErrLifecycle)
	}
	for i := range set.members {
		if lifecycleImageRead(ctx, tree, &set.members[i], set) != nil {
			return set, lifecycleDiagnosticError("G000521", ErrLifecycle)
		}
	}
	currentSource, err := lifecycleImageSource(source)
	lockStat, lockErr := lifecycleImageStat(allocation)
	if err != nil || !lifecycleImageSame(sourceStat, currentSource) || sourceStat.Size != currentSource.Size ||
		lockErr != nil || !lifecycleImageRegular(lockStat, 0600, set.ancestry[2].Dev) || lockStat.Size != 0 ||
		!lifecycleImageEntry(tree.base, lifecycleImageLock, lockStat) {
		return set, lifecycleDiagnosticError("G000522", ErrLifecycle)
	}
	if _, err = tree.validate(ctx, &set.ancestry); err != nil {
		return set, lifecycleDiagnosticError("G000523", ErrLifecycle)
	}
	// All three files are complete, closed and validated BEFORE lock release.
	// This transition is deliberately earlier than even the first Start attempt.
	set.ready = true
	return set, nil
}

func lifecycleImageUnwind(ctx context.Context, tree *lifecycleImageTree, set *lifecyclePackageImages, created [3]bool) {
	for i := len(set.members) - 1; i >= 0; i-- {
		if !created[i] {
			continue
		}
		member := &set.members[i]
		if _, err := tree.validate(ctx, &set.ancestry); err != nil {
			return
		}
		dir, err := lifecycleImageOpenDirectory(tree.base, member.name)
		if err != nil {
			return
		}
		// Reopen handles are read-only; a close error stops all further cleanup.
		ok := func() bool {
			st, e := lifecycleImageStat(dir)
			if e != nil || !lifecycleImageSame(st, member.directory) ||
				!(lifecycleImageDirectory(st, 0700) || lifecycleImageDirectory(st, 0500)) || !lifecycleImageEntry(tree.base, member.name, st) {
				return false
			}
			names, e := lifecycleImageNames(ctx, dir, 2)
			if e != nil {
				return false
			}
			if len(names) == 1 {
				var image unix.Stat_t
				if names[0] != "image" || unix.Fstatat(dir.fd(), "image", &image, unix.AT_SYMLINK_NOFOLLOW) != nil ||
					!lifecycleImageSame(image, member.image) || !(lifecycleImageRegular(image, 0600, st.Dev) || lifecycleImageRegular(image, 0500, st.Dev)) || image.Size < 0 || image.Size > set.size || !lifecycleImageLive(ctx) {
					return false
				}
				if _, e := tree.validate(ctx, &set.ancestry); e != nil || !lifecycleImageEntry(tree.base, member.name, st) {
					return false
				}
				if unix.Unlinkat(dir.fd(), "image", 0) != nil {
					return false
				}
			}
			return lifecycleImageLive(ctx)
		}()
		if dir.close() != nil || !ok || !lifecycleImageLive(ctx) {
			return
		}
		var st unix.Stat_t
		if unix.Fstatat(tree.base.fd(), member.name, &st, unix.AT_SYMLINK_NOFOLLOW) != nil ||
			!lifecycleImageSame(st, member.directory) || !(lifecycleImageDirectory(st, 0700) || lifecycleImageDirectory(st, 0500)) ||
			!lifecycleImageLive(ctx) || unix.Unlinkat(tree.base.fd(), member.name, unix.AT_REMOVEDIR) != nil {
			return
		}
	}
}

func (set *lifecyclePackageImages) path(ctx context.Context, site lifecycleImageSite) (path string, failure error) {
	if set == nil || !set.ready || site < 0 || int(site) >= len(set.members) || set.members[site].selected || !lifecycleImageLive(ctx) {
		return "", lifecycleDiagnosticError("G000524", ErrLifecycle)
	}
	set.members[site].selected = true
	boot, err := LifecycleBoot()
	if err != nil || boot != set.boot {
		return "", lifecycleDiagnosticError("G000525", ErrLifecycle)
	}
	tree, err := lifecycleImageOpenTree(ctx, false)
	if err != nil {
		return "", lifecycleDiagnosticError("G000526", ErrLifecycle, err)
	}
	defer func() {
		if !tree.close() || !lifecycleImageLive(ctx) {
			path, failure = "", lifecycleDiagnosticError("G000527", ErrLifecycle)
		}
	}()
	if lifecycleImageRead(ctx, tree, &set.members[site], set) != nil {
		return "", lifecycleDiagnosticError("G000528", ErrLifecycle)
	}
	return "/run/" + lifecycleImageRoot + "/" + set.members[site].name + "/image", nil
}
