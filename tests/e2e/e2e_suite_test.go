package e2e_test

import (
	"encoding/json"
	"net"
	"os"
	"testing"
	"time"

	"github.com/onsi/ginkgo/v2"
	"github.com/onsi/gomega"
)

// dispatchPlan 只传递用例键和私有 Unix socket；凭据继续留在原验收 Session。
type dispatchPlan struct {
	Socket  string `json:"socket"`
	Entries []struct {
		VariantID string `json:"variantId"`
		Layer     string `json:"layer"`
	} `json:"entries"`
}

// TestE2E 接入一期唯一入口；普通 Go UT 不创建集群或访问任何已存在环境。
func TestE2E(t *testing.T) {
	if os.Getenv("MONITOR_MGR_GINKGO_PLAN") == "" {
		t.Skip("通过 make acceptance-run 显式提供环境并运行 E2E")
	}
	gomega.RegisterFailHandler(ginkgo.Fail)
	ginkgo.RunSpecs(t, "一期 E2E 调度")
}

var _ = ginkgo.Describe("固定验收键", ginkgo.Ordered, ginkgo.ContinueOnFailure, func() {
	path := os.Getenv("MONITOR_MGR_GINKGO_PLAN")
	if path == "" {
		return
	}
	var plan dispatchPlan
	raw, readErr := os.ReadFile(path)
	parseErr := json.Unmarshal(raw, &plan)
	ginkgo.BeforeAll(func() {
		gomega.Expect(readErr).NotTo(gomega.HaveOccurred(), "读取本次私有调度计划")
		gomega.Expect(parseErr).NotTo(gomega.HaveOccurred(), "解析固定用例键")
		gomega.Expect(plan.Socket).NotTo(gomega.BeEmpty())
		gomega.Expect(plan.Entries).NotTo(gomega.BeEmpty())
	})
	for _, entry := range plan.Entries {
		entry := entry
		ginkgo.It(entry.Layer+":"+entry.VariantID, func() {
			// 先连接持有原 lane 和 UID 账本的 Session，再请求唯一的本例业务动作。
			ginkgo.By("派发原 Python/Playwright 场景并等待断言及清理结束")
			conn, err := net.DialTimeout("unix", plan.Socket, 5*time.Second)
			gomega.Expect(err).NotTo(gomega.HaveOccurred())
			defer conn.Close()
			gomega.Expect(conn.SetDeadline(time.Now().Add(30 * time.Minute))).To(gomega.Succeed())
			gomega.Expect(json.NewEncoder(conn).Encode(entry)).To(gomega.Succeed())
			var result struct {
				VariantID string `json:"variantId"`
				Layer     string `json:"layer"`
				Outcome   string `json:"outcome"`
			}
			gomega.Expect(json.NewDecoder(conn).Decode(&result)).To(gomega.Succeed())
			// 关键：必须收到同一个键的真实完成结果，连接成功或进程退出不能替代业务断言。
			gomega.Expect(result.VariantID).To(gomega.Equal(entry.VariantID))
			gomega.Expect(result.Layer).To(gomega.Equal(entry.Layer))
			gomega.Expect(result.Outcome).To(gomega.Equal("passed"), "实际业务结果未通过；原始诊断保存在私有目录")
		})
	}
})
