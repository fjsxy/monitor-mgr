// 每个纵向场景传回白名单原始观测，业务结论由 Ginkgo/Gomega 执行。
package e2e_test

import (
	"encoding/json"
	"github.com/onsi/gomega"
)

var issueEvidenceAssertions = map[string]func(map[string]json.RawMessage){}
var requiredIssueEvidence = map[string][]string{}

// registerIssueEvidence 登记 Issue 证据对应的 Ginkgo 断言入口。
func registerIssueEvidence(key string, assertion func(map[string]json.RawMessage)) {
	issueEvidenceAssertions[key] = assertion
}

// requireIssueEvidence 为稳定用例登记不可遗漏的 Issue 原始证据。
func requireIssueEvidence(caseID, key string) {
	requiredIssueEvidence[caseID] = append(requiredIssueEvidence[caseID], key)
}

// assertIssueEvidence 不接受漏证据或未知的未经断言 proof，Python passed 字符串无法替代业务判据。
func assertIssueEvidence(caseID string, evidence map[string]json.RawMessage) {
	for _, key := range requiredIssueEvidence[caseID] {
		gomega.Expect(evidence).To(gomega.HaveKey(key), caseID+" 缺少原始验收证据 "+key)
	}
	for key := range evidence {
		assertion, ok := issueEvidenceAssertions[key]
		gomega.Expect(ok).To(gomega.BeTrue(), "没有对应 Ginkgo 断言："+key)
		assertion(evidence)
	}
}

// init 将创建身份与历史身份断言绑定既有稳定 E2E 用例。
func init() {
	registerIssueEvidence("issue25", assertIssue25Evidence)
	requireIssueEvidence("E2E-08", "issue25")
	registerIssueEvidence("issue26", assertIssue26Evidence)
	requireIssueEvidence("E2E-01", "issue26")
}
