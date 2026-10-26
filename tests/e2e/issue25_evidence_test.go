package e2e_test

import (
	"encoding/json"
	"os"

	"github.com/onsi/ginkgo/v2"
	"github.com/onsi/gomega"
)

// assertIssue25Evidence 用真实 API 与持久操作观测验证写前授权，拒绝仅凭 Python passed 字符串通过。
func assertIssue25Evidence(evidence map[string]json.RawMessage) {
	raw, exists := evidence["issue25"]
	gomega.Expect(exists).To(gomega.BeTrue(), "E2E-08 必须提供 I25 两个真实发送边界")
	var proof struct {
		TestedCommit string
		Unsent       struct {
			ActorID, CreatorUserID, CreatorRole, OwnerAfterRevocation      string
			PausedResource, StateBefore, AttemptActor, StateAfter, Message string
			PossiblySent                                                   *bool
			NotFound                                                       bool
			BusinessWrites                                                 *int
		}
		Sent struct {
			StateUncertain, StateAfter, OriginalUID, ActualUID, InstanceUID string
			ActualCPU, CreatorUserID, ActorID                               string
			CountBeforeRecovery, CountAfterRecovery, PutCount               int
			PossiblySent                                                    bool
			Resources                                                       []struct{ Key, State string }
		}
		ServiceMonitorMutations *[]struct{ Method, Path string }
	}
	gomega.Expect(json.Unmarshal(raw, &proof)).To(gomega.Succeed())
	gomega.Expect(proof.TestedCommit).To(gomega.MatchRegexp("^[0-9a-f]{40}$"))
	gomega.Expect(proof.TestedCommit).To(gomega.Equal(os.Getenv("MONITOR_MGR_E2E_CANDIDATE")))
	ginkgo.By("I25-K01.unsent：原 ADMIN 实例仍受保护，降级但保留集群归属后零写入")
	u := proof.Unsent
	gomega.Expect(u.ActorID).NotTo(gomega.BeEmpty())
	gomega.Expect(u.CreatorUserID).To(gomega.Equal(u.ActorID))
	gomega.Expect(u.CreatorRole).To(gomega.Equal("ADMIN"))
	gomega.Expect(u.OwnerAfterRevocation).To(gomega.Equal(u.ActorID))
	gomega.Expect(u.AttemptActor).To(gomega.Equal(u.ActorID))
	gomega.Expect(u.PausedResource).To(gomega.Equal("kubestatemetrics"))
	gomega.Expect(u.StateBefore).To(gomega.Equal("CHECKING"))
	// 关键：缺字段或 null 不能被零值解释为未发送、零写入。
	gomega.Expect(u.PossiblySent).NotTo(gomega.BeNil())
	gomega.Expect(*u.PossiblySent).To(gomega.BeFalse())
	gomega.Expect(u.StateAfter).To(gomega.Equal("FAILED"))
	gomega.Expect(u.Message).To(gomega.ContainSubstring("管理员创建"))
	gomega.Expect(u.BusinessWrites).NotTo(gomega.BeNil())
	gomega.Expect(*u.BusinessWrites).To(gomega.BeZero())
	gomega.Expect(u.NotFound).To(gomega.BeTrue())
	ginkgo.By("I25-K01.sent：停用发起账号后原 UID 只读恢复，不回滚或重放已发更新")
	s := proof.Sent
	gomega.Expect(s.StateUncertain).To(gomega.Equal("RESULT_UNCERTAIN"))
	gomega.Expect(s.StateAfter).To(gomega.Equal("SUCCEEDED"))
	gomega.Expect(s.OriginalUID).NotTo(gomega.BeEmpty())
	gomega.Expect(s.ActualUID).To(gomega.Equal(s.OriginalUID))
	gomega.Expect(s.InstanceUID).To(gomega.Equal(s.OriginalUID))
	gomega.Expect(s.ActualCPU).To(gomega.Equal("244m"))
	gomega.Expect(s.CreatorUserID).To(gomega.Equal(s.ActorID))
	gomega.Expect(s.PossiblySent).To(gomega.BeTrue())
	gomega.Expect(s.PutCount).To(gomega.Equal(1))
	gomega.Expect(s.CountAfterRecovery).To(gomega.Equal(s.CountBeforeRecovery))
	gomega.Expect(s.Resources).To(gomega.HaveLen(1))
	gomega.Expect(s.Resources[0].Key).To(gomega.Equal("component"))
	gomega.Expect(s.Resources[0].State).To(gomega.Equal("SUCCEEDED"))
	gomega.Expect(proof.ServiceMonitorMutations).NotTo(gomega.BeNil())
	gomega.Expect(*proof.ServiceMonitorMutations).To(gomega.BeEmpty())
}
