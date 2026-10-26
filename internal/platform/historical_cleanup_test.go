// 工具可信边界在真实受控 HTTP 输入上运行实际 helper，Ginkgo 判定收据与原 UID 责任。
package platform_test

import (
	"encoding/json"
	ginkgo "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"os"
	"os/exec"
	"path/filepath"
)

func checkHistoricalCleanup(t ginkgo.GinkgoTInterface) {
	python := os.Getenv("MONITOR_MGR_PYTHON")
	if python == "" {
		python = "python3"
	}
	command := exec.Command(python, filepath.Join("..", "..", "scripts", "acceptance", "historical_cleanup_probe.py"))
	raw, e := command.CombinedOutput()
	Expect(e).NotTo(HaveOccurred(), string(raw))
	var proof struct {
		Cleanup []struct {
			Variant string
			Receipt struct {
				Success       bool
				Phase, Reason string
			}
			RegistryRetained bool
			Events           []struct {
				Method        string
				Preconditions map[string]string
			}
		}
		Handoff []struct {
			Variant string
			Tracked []string
			Error   string
		}
	}
	Expect(json.Unmarshal(raw, &proof)).To(Succeed())
	Expect(proof.Cleanup).To(HaveLen(6))
	Expect(proof.Handoff).To(HaveLen(6))
	reasons := map[string]string{"blocked": "delete/delete-failed", "replaced": "identity/uid-mismatch", "read": "read/read-failed", "finalizers": "finalizers/finalizer-patch-failed", "delete": "delete/delete-failed"}
	for _, item := range proof.Cleanup {
		id := "I29-R03." + item.Variant
		if item.Variant == "gone" || item.Variant == "blocked" {
			id = "I29-R01." + item.Variant
		}
		ginkgo.By(id)
		deletes := 0
		for _, event := range item.Events {
			if event.Method == "DELETE" {
				deletes++
				Expect(event.Preconditions).To(Equal(map[string]string{"uid": "original-uid", "resourceVersion": "rv"}))
			}
		}
		if item.Variant == "gone" {
			Expect(item.Receipt.Success).To(BeTrue())
			Expect(item.RegistryRetained).To(BeFalse())
			Expect(deletes).To(Equal(1))
		} else {
			Expect(item.Receipt.Success).To(BeFalse())
			Expect(item.RegistryRetained).To(BeTrue())
			Expect(item.Receipt.Phase + "/" + item.Receipt.Reason).To(Equal(reasons[item.Variant]))
			if item.Variant != "blocked" && item.Variant != "delete" {
				Expect(deletes).To(BeZero())
			}
		}
		ginkgo.AddReportEntry(id, "实际HTTP边界保留原UID责任，失败收据不含私有异常")
	}
	for _, item := range proof.Handoff {
		id := "I29-R02." + item.Variant
		ginkgo.By(id)
		if item.Variant == "confirmed" {
			Expect(item.Error).To(BeEmpty())
			Expect(item.Tracked).To(Equal([]string{"original-uid"}))
		} else {
			Expect(item.Tracked).To(BeEmpty())
			if item.Variant == "unsent" {
				Expect(item.Error).To(BeEmpty())
			} else {
				Expect(item.Error).NotTo(BeEmpty())
			}
		}
		ginkgo.AddReportEntry(id, "精确发布操作交接不采用同名资源补认")
	}
	Expect(string(raw)).NotTo(ContainSubstring("private-sentinel"))
}
