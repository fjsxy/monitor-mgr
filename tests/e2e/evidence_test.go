// 每个纵向场景传回白名单原始观测，业务结论由 Ginkgo/Gomega 执行。
package e2e_test

import (
	"encoding/json"
	"github.com/onsi/gomega"
)

var issueEvidenceAssertions = map[string]func(map[string]json.RawMessage){}
var requiredIssueEvidence = map[string][]string{}

func registerIssueEvidence(key string, assertion func(map[string]json.RawMessage)) {
	issueEvidenceAssertions[key] = assertion
}
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
