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

// checkHistoricalCleanup 通过真实 HTTP 清理夹具验证原 UID、失败阶段与零误删。
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
				Success       *bool
				Phase, Reason string
			}
			RegistryRetained *bool
			Events           *[]struct {
				Method        string
				Preconditions map[string]string
			}
		}
		Conditional []struct {
			Variant string
			Receipt struct {
				Success       bool
				Reason, Phase string
				Attempts      int
			}
			Events []struct {
				Method, PatchType string
				Preconditions     map[string]any
			}
			Diagnostic                                                                   map[string]any
			OtherFinalizerPreserved, OtherSpecPreserved, LegacyFieldAbsent, RulesApplied bool
		}
		Handoff []struct {
			Variant string
			Tracked *[]string
			Error   *string
		}
	}
	Expect(json.Unmarshal(raw, &proof)).To(Succeed())
	Expect(proof.Cleanup).To(HaveLen(9))
	Expect(proof.Handoff).To(HaveLen(6))
	reasons := map[string]string{"blocked": "delete/delete-failed", "replaced": "identity/uid-mismatch", "read": "read/read-failed", "finalizers": "finalizers/finalizer-patch-failed", "delete": "delete/delete-failed", "patch-replaced": "identity/uid-mismatch"}
	for _, item := range proof.Cleanup {
		id := "I29-R03." + item.Variant
		if item.Variant == "gone" || item.Variant == "blocked" {
			id = "I29-R01." + item.Variant
		}
		ginkgo.By(id)
		// 关键：缺字段不能反序列化为 false/空列表后冒充实际零写或已退休责任。
		Expect(item.Receipt.Success).NotTo(BeNil())
		Expect(item.RegistryRetained).NotTo(BeNil())
		Expect(item.Events).NotTo(BeNil())
		deletes := 0
		for _, event := range *item.Events {
			Expect(event.Method).NotTo(BeEmpty())
			if event.Method == "DELETE" {
				deletes++
				rv := "rv"
				if item.Variant == "patch-conflict" {
					rv = "rv2"
				}
				Expect(event.Preconditions).To(Equal(map[string]string{"uid": "original-uid", "resourceVersion": rv}))
			}
		}
		if item.Variant == "gone" || item.Variant == "patch-gone" || item.Variant == "patch-conflict" {
			Expect(*item.Receipt.Success).To(BeTrue())
			Expect(*item.RegistryRetained).To(BeFalse())
			if item.Variant == "patch-gone" {
				Expect(deletes).To(BeZero())
			} else {
				Expect(deletes).To(Equal(1))
			}
		} else {
			Expect(*item.Receipt.Success).To(BeFalse())
			Expect(*item.RegistryRetained).To(BeTrue())
			Expect(item.Receipt.Phase + "/" + item.Receipt.Reason).To(Equal(reasons[item.Variant]))
			if item.Variant != "blocked" && item.Variant != "delete" {
				Expect(deletes).To(BeZero())
			}
		}
		ginkgo.AddReportEntry(id, "实际HTTP边界保留原UID责任，失败收据不含私有异常")
	}
	Expect(proof.Conditional).To(HaveLen(10))
	for _, item := range proof.Conditional {
		ginkgo.By("I23-R27." + item.Variant)
		patches := 0
		for _, event := range item.Events {
			Expect(event.Method).NotTo(Equal("DELETE"))
			if event.Method != "PATCH" {
				continue
			}
			patches++
			Expect(event.PatchType).To(Equal("application/merge-patch+json"))
			Expect(event.Preconditions["uid"]).To(Equal("original-uid"))
			rv := "rv"
			if patches > 1 {
				rv = "rv2"
			}
			if item.Variant == "conflict-limit" && patches == 3 {
				rv = "rv3"
			}
			Expect(event.Preconditions["resourceVersion"]).To(Equal(rv))
		}
		Expect(item.OtherFinalizerPreserved).To(BeTrue())
		Expect(item.OtherSpecPreserved).To(BeTrue())
		switch item.Variant {
		case "preserve":
			Expect(item.Receipt.Success).To(BeTrue())
			Expect(patches).To(Equal(1))
		case "conflict", "legacy-conflict", "rules-conflict":
			Expect(item.Receipt.Success).To(BeTrue())
			Expect(patches).To(Equal(2))
			Expect(item.Receipt.Attempts).To(Equal(2))
		case "conflict-limit":
			Expect(item.Receipt.Success).To(BeFalse())
			Expect(patches).To(Equal(3))
			Expect(item.Receipt.Attempts).To(Equal(3))
			Expect(item.Receipt.Reason).To(Equal("conflict"))
		case "replacement", "legacy-replacement":
			Expect(item.Receipt.Success).To(BeFalse())
			Expect(patches).To(Equal(1))
			Expect(item.Receipt.Reason).To(Equal("uid-mismatch"))
		default:
			Expect(item.Receipt.Success).To(BeFalse())
			Expect(patches).To(Equal(1))
			reason := item.Variant
			if reason == "network" {
				reason = "unknown"
			}
			Expect(item.Receipt.Reason).To(Equal(reason))
		}
		if item.Variant == "rules-conflict" {
			Expect(item.RulesApplied).To(BeTrue())
		}
		if item.Variant == "legacy-conflict" {
			Expect(item.LegacyFieldAbsent).To(BeTrue())
		}
		if !item.Receipt.Success {
			Expect(item.Diagnostic).To(HaveLen(4))
			Expect(item.Diagnostic["operation"]).To(BeElementOf("fixture-hold-release", "legacy-service-monitor-remove"))
		}
	}
	Expect(string(raw)).NotTo(ContainSubstring("PRIVATE_SENTINEL"))
	for _, item := range proof.Handoff {
		id := "I29-R02." + item.Variant
		ginkgo.By(id)
		Expect(item.Error).NotTo(BeNil())
		Expect(item.Tracked).NotTo(BeNil())
		if item.Variant == "confirmed" {
			Expect(*item.Error).To(BeEmpty())
			Expect(*item.Tracked).To(Equal([]string{"original-uid"}))
		} else {
			Expect(*item.Tracked).To(BeEmpty())
			if item.Variant == "unsent" {
				Expect(*item.Error).To(BeEmpty())
			} else {
				Expect(*item.Error).NotTo(BeEmpty())
			}
		}
		ginkgo.AddReportEntry(id, "精确发布操作交接不采用同名资源补认")
	}
	Expect(string(raw)).NotTo(ContainSubstring("private-sentinel"))
}
