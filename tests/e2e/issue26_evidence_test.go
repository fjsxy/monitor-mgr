// 历史升级通过真实 Pod/HTTP 采集观测，由 Ginkgo/Gomega 验证业务不变式。
package e2e_test

import (
	"encoding/json"
	"time"

	"github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

// assertIssue26Evidence 仅由 E2E-01 调用；缺少任何观测均失败，不能用编排进程成功冒充权限验收。
func assertIssue26Evidence(evidence map[string]json.RawMessage) {
	type confirmation struct {
		ActorID     string `json:"actorId"`
		ConfirmedAt string `json:"confirmedAt"`
		Allow       bool   `json:"allowClusterManagers"`
	}
	type instance struct {
		CreatorUserID string        `json:"creatorUserId"`
		CreatorRole   string        `json:"creatorRole"`
		CanManage     bool          `json:"canManage"`
		Confirmation  *confirmation `json:"managementConfirmation"`
	}
	var got struct {
		OriginalActor       string   `json:"originalActor"`
		OriginalUID         string   `json:"originalUid"`
		CurrentUID          string   `json:"currentUid"`
		Known               instance `json:"known"`
		Missing             instance `json:"missing"`
		Confirmed           instance `json:"confirmed"`
		Allowed             instance `json:"allowed"`
		Restricted          instance `json:"restricted"`
		OrdinaryConfirmCode string   `json:"ordinaryConfirmCode"`
		RestrictedSaveCode  string   `json:"restrictedSaveCode"`
		BeforeMutations     int      `json:"beforeMutationCount"`
		AutomaticMutations  int      `json:"automaticMutationCount"`
		AutomaticCollection bool     `json:"automaticSpecHasCollection"`
		PlatformSMWrites    int      `json:"platformSMWrites"`
		PublicationState    string   `json:"publicationState"`
		OldVersion          string   `json:"oldVersionId"`
		NewVersion          string   `json:"newVersionId"`
	}
	raw, ok := evidence["issue26"]
	Expect(ok).To(BeTrue(), "E2E-01 必须返回历史升级原始观测")
	Expect(json.Unmarshal(raw, &got)).To(Succeed())
	var fields map[string]json.RawMessage
	Expect(json.Unmarshal(raw, &fields)).To(Succeed())
	for _, key := range []string{"originalActor", "originalUid", "currentUid", "known", "missing", "confirmed", "allowed", "restricted", "ordinaryConfirmCode", "restrictedSaveCode", "beforeMutationCount", "automaticMutationCount", "automaticSpecHasCollection", "platformSMWrites", "publicationState", "oldVersionId", "newVersionId"} {
		Expect(fields).To(HaveKey(key))
	}
	for _, key := range []string{"known", "missing", "confirmed", "allowed", "restricted"} {
		var value map[string]json.RawMessage
		Expect(json.Unmarshal(fields[key], &value)).To(Succeed())
		for _, field := range []string{"creatorUserId", "creatorRole", "canManage"} {
			Expect(value).To(HaveKey(field), key)
		}
	}

	ginkgo.By("I26-K01：可靠创建用户恢复，未知创建事实保持且普通用户不能确认")
	Expect(got.OriginalActor).NotTo(BeEmpty())
	Expect(got.Known.CreatorUserID).To(Equal(got.OriginalActor))
	Expect(got.Known.CreatorRole).To(BeEmpty())
	Expect(got.Known.CanManage).To(BeFalse())
	Expect(got.Missing.CreatorUserID).To(BeEmpty())
	Expect(got.Missing.CreatorRole).To(BeEmpty())
	Expect(got.Missing.CanManage).To(BeFalse())
	Expect(got.OrdinaryConfirmCode).To(Equal("FORBIDDEN"))
	ginkgo.By("I26-K01：独立权限确认留痕，允许后真实发布、撤销后写入拒绝")
	Expect(got.Confirmed.CreatorUserID).To(Equal(got.OriginalActor))
	Expect(got.Confirmed.CreatorRole).To(BeEmpty())
	Expect(got.Confirmed.Confirmation).NotTo(BeNil())
	Expect(got.Confirmed.Confirmation.ActorID).To(Equal(got.OriginalActor))
	Expect(got.Confirmed.Confirmation.Allow).To(BeTrue())
	_, err := time.Parse(time.RFC3339Nano, got.Confirmed.Confirmation.ConfirmedAt)
	Expect(err).NotTo(HaveOccurred())
	Expect(got.Allowed.CanManage).To(BeTrue())
	Expect(got.PublicationState).To(Equal("SUCCEEDED"))
	Expect(got.OldVersion).NotTo(BeEmpty())
	Expect(got.NewVersion).NotTo(Equal(got.OldVersion))
	Expect(got.NewVersion).NotTo(BeEmpty())
	Expect(got.Restricted.CanManage).To(BeFalse())
	Expect(got.Restricted.CreatorRole).To(BeEmpty())
	Expect(got.Restricted.Confirmation).NotTo(BeNil())
	Expect(got.Restricted.Confirmation.Allow).To(BeFalse())
	Expect(got.RestrictedSaveCode).To(Equal("FORBIDDEN"))
	ginkgo.By("I26-K01：身份补齐、确认和轮询零写入；平台不写配套 ServiceMonitor")
	Expect(got.BeforeMutations).To(BeNumerically(">", 0))
	Expect(got.AutomaticMutations).To(Equal(got.BeforeMutations))
	Expect(got.AutomaticCollection).To(BeFalse())
	Expect(got.PlatformSMWrites).To(BeZero())
	Expect(got.OriginalUID).NotTo(BeEmpty())
	Expect(got.CurrentUID).To(Equal(got.OriginalUID))
	ginkgo.AddReportEntry("I26-K01", "真实旧库升级、明确权限允许/撤销、原 UID 与零自动采集写入逐项通过")
}
