package main

import (
	"os"

	ginkgo "github.com/onsi/ginkgo/v2"
)

// Host 协议只验证真实文件和 SQLite，不代替集群 PVC 的运行证据。
var _ = ginkgo.It("TestVolumeProbeActualSQLite", func() {
	t := ginkgo.GinkgoT()
	// 接下来Host 协议只验证真实文件和 SQLite，不代替集群 PVC 的运行证据。
	ginkgo.By("Host 协议只验证真实文件和 SQLite，不代替集群 PVC 的运行证据。")

	proof, err := ProbeVolume(t.TempDir(), os.Getuid())
	if err != nil {
		t.Fatal(err)
	}
	if proof.JournalMode != "wal" || proof.Synchronous != 2 || !proof.LockBlocked || proof.ReopenValue != 21 || proof.KeyMode != "0600" {
		t.Fatalf("卷能力未满足: %+v", proof)
	}
})
