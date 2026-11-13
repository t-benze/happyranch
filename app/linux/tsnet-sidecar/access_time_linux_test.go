//go:build linux

package sidecar

import (
	"os"
	"syscall"
)

func accessTimeSeconds(st os.FileInfo) int64 {
	return st.Sys().(*syscall.Stat_t).Atim.Sec
}
