// E2E 由 Ginkgo 调度；Python 持有集群生命周期并逐阶段发送 By，保留原场景实现语言。
package e2e_test

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"testing"
	"time"

	"github.com/onsi/ginkgo/v2"
	"github.com/onsi/gomega"
)

func TestE2E(t *testing.T) {
	if os.Getenv("MONITOR_MGR_E2E_SOCKET") == "" {
		t.Skip("使用 make e2e 自动准备独占环境")
	}
	gomega.RegisterFailHandler(ginkgo.Fail)
	ginkgo.RunSpecs(t, "一期真实集群 E2E")
}

var _ = ginkgo.Describe("独占双集群", ginkgo.Ordered, ginkgo.ContinueOnFailure, func() {
	titles := []string{"平台部署与 Pod 重建", "双集群登记凭据与归属", "KSM 三种真实拓扑", "Agent 关联与发送配置", "分片与 PVC 完整生命周期", "发布冻结漂移与版本", "目标故障与状态隔离", "写响应丢失与重启禁重放", "前台删除与 UID 保护", "唯一浏览器主流程"}
	// 关键：父进程传入固定分组，只注册本组 It；不使用 skip/focus 将漏跑掩盖为成功。
	selected := map[string]bool{}
	if raw := os.Getenv("MONITOR_MGR_E2E_CASES"); raw != "" {
		var ids []string
		if err := json.Unmarshal([]byte(raw), &ids); err != nil || len(ids) == 0 {
			panic("E2E 分组为空或格式错误")
		}
		for _, id := range ids {
			valid := false
			for n := range titles {
				valid = valid || id == fmt.Sprintf("E2E-%02d", n+1)
			}
			if !valid || selected[id] {
				panic("E2E 分组包含未知或重复 ID")
			}
			selected[id] = true
		}
	}
	for n, title := range titles {
		id := fmt.Sprintf("E2E-%02d", n+1)
		if len(selected) > 0 && !selected[id] {
			continue
		}
		ginkgo.It(id+" "+title, func() {
			conn, err := net.DialTimeout("unix", os.Getenv("MONITOR_MGR_E2E_SOCKET"), 5*time.Second)
			gomega.Expect(err).NotTo(gomega.HaveOccurred())
			defer conn.Close()
			gomega.Expect(conn.SetDeadline(time.Now().Add(30 * time.Minute))).To(gomega.Succeed())
			gomega.Expect(json.NewEncoder(conn).Encode(map[string]string{"id": id})).To(gomega.Succeed())
			decoder := json.NewDecoder(conn)
			for {
				var event struct {
					Type     string                     `json:"type"`
					Text     string                     `json:"text"`
					ID       string                     `json:"id"`
					Outcome  string                     `json:"outcome"`
					Evidence map[string]json.RawMessage `json:"evidence"`
				}
				gomega.Expect(decoder.Decode(&event)).To(gomega.Succeed())
				if event.Type == "step" {
					ginkgo.By(event.Text)
					continue
				}
				// 关键：只有同 ID 的实际断言和清理均成功才记通过；连接/进程成功不等于业务通过。
				gomega.Expect(event.ID).To(gomega.Equal(id))
				gomega.Expect(event.Outcome).To(gomega.Equal("passed"))
				assertIssueEvidence(id, event.Evidence)
				break
			}
		})
	}
})
