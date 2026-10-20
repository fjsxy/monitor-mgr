package main

import (
	"os"
	"testing"
)

// Host 协议只验证真实文件和 SQLite，不代替集群 PVC 的运行证据。
func TestVolumeProbeActualSQLite(t *testing.T) {
	proof, err := ProbeVolume(t.TempDir(), os.Getuid())
	if err != nil {
		t.Fatal(err)
	}
	if proof.JournalMode != "wal" || proof.Synchronous != 2 || !proof.LockBlocked || proof.ReopenValue != 21 || proof.KeyMode != "0600" {
		t.Fatalf("卷能力未满足: %+v", proof)
	}
}
