// #29 业务结论由原始 HTTP/Kubernetes 观测断言，零值不能由缺字段伪造。
package e2e_test

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"gopkg.in/yaml.v3"
)

// init 将历史迁移证据绑定既有稳定 E2E 用例。
func init() {
	registerIssueEvidence("issue29", assertIssue29Evidence)
	for _, id := range []string{"E2E-01", "E2E-08", "E2E-10"} {
		requireIssueEvidence(id, "issue29")
	}
}

// migrationFields 要求字段存在且非null，0、false和空数组都必须来自实际观测。
func migrationFields(raw any, keys ...string) map[string]any {
	m, ok := raw.(map[string]any)
	Expect(ok).To(BeTrue(), "原始观测必须是对象")
	for _, key := range keys {
		Expect(m).To(HaveKey(key))
		Expect(m[key]).NotTo(BeNil(), "观测字段不能为null: "+key)
	}
	return m
}

// migrationResources 验证并返回旧操作的独立资源事实。
func migrationResources(raw any) []any {
	m := migrationFields(raw, "resources")
	resources, ok := m["resources"].([]any)
	Expect(ok).To(BeTrue())
	return resources
}

// migrationSingleParent 核实新发布只冻结一份父 CR。
func migrationSingleParent(raw any) {
	resources := migrationResources(raw)
	Expect(resources).To(HaveLen(1))
	item := migrationFields(resources[0])
	if target, ok := item["target"]; ok {
		item = migrationFields(target)
	}
	Expect(item["kind"]).To(Equal("KubeStateMetrics"))
}

// migrationSMFact 提取旧 SM 原执行事实，防止外部处理覆盖未知结果。
func migrationSMFact(raw any) map[string]any {
	for _, resource := range migrationResources(raw) {
		r := migrationFields(resource, "target", "state", "confirmedUid", "possiblySent")
		target := migrationFields(r["target"], "kind")
		if target["kind"] == "ServiceMonitor" {
			return r
		}
	}
	ginkgo.Fail("缺少原 ServiceMonitor 执行事实")
	return nil
}

// assertIssue29Evidence 逐项断言历史迁移原始证据，不能信任 Python passed 字符串。
func assertIssue29Evidence(evidence map[string]json.RawMessage) {
	var value any
	Expect(json.Unmarshal(evidence["issue29"], &value)).To(Succeed())
	proof := migrationFields(value, "testedCommit", "kind", "platformSMWrites")
	Expect(os.Getenv("MONITOR_MGR_E2E_CANDIDATE")).NotTo(BeEmpty())
	Expect(proof["testedCommit"]).To(Equal(os.Getenv("MONITOR_MGR_E2E_CANDIDATE")))
	Expect(proof["platformSMWrites"]).To(Equal(float64(0)))
	switch proof["kind"] {
	case "pure":
		ginkgo.By("I29-K01：真实旧单CR升级零业务写，新的明确发布保留父UID且只冻结父CR")
		migrationFields(proof, "originalUid", "currentUid", "automaticBefore", "automaticAfter", "oldVersion", "oldVersionAfter", "oldOperation", "oldOperationAfter", "newVersion", "newOperation", "creatorUserId", "parent", "companion")
		Expect(proof["originalUid"]).NotTo(BeEmpty())
		Expect(proof["currentUid"]).To(Equal(proof["originalUid"]))
		Expect(proof["automaticAfter"]).To(Equal(proof["automaticBefore"]))
		Expect(proof["oldVersionAfter"]).To(Equal(proof["oldVersion"]))
		Expect(proof["oldOperationAfter"]).To(Equal(proof["oldOperation"]))
		migrationSingleParent(proof["newVersion"])
		migrationSingleParent(proof["newOperation"])
		old := migrationFields(proof["oldVersion"], "versionId")
		fresh := migrationFields(proof["newVersion"], "versionId")
		Expect(fresh["versionId"]).NotTo(Equal(old["versionId"]))
		oldResources := migrationResources(old)
		Expect(oldResources).To(HaveLen(1))
		oldResource := migrationFields(oldResources[0], "yaml")
		var oldDoc map[string]any
		Expect(yaml.Unmarshal([]byte(oldResource["yaml"].(string)), &oldDoc)).To(Succeed())
		Expect(cm(oldDoc, "spec")).NotTo(HaveKey("serviceMonitor"))
		parent := migrationFields(proof["parent"], "metadata", "spec", "status")
		Expect(cm(parent, "metadata")["uid"]).To(Equal(proof["originalUid"]))
		monitor := migrationFields(cm(parent, "spec")["serviceMonitor"], "enabled", "labels")
		Expect(monitor["enabled"]).To(BeTrue())
		Expect(cm(monitor, "labels")["monitor-mgr.io/user-id"]).To(Equal(proof["creatorUserId"]))
		assertMigrationCompanion(parent, migrationFields(proof["companion"]))
	case "legacy":
		ginkgo.By("I29-K02：成功/未知旧SM不同名称均显式外部处理，旧事实不改，新Operator唯一管理")
		migrationFields(proof, "cases")
		cases, ok := proof["cases"].([]any)
		Expect(ok).To(BeTrue())
		Expect(cases).To(HaveLen(2))
		variants := []string{}
		for _, raw := range cases {
			item := migrationFields(raw, "variant", "originalUid", "currentUid", "inventory", "inventoryAfter", "blockedCode", "resolution", "release", "oldOperation", "oldOperationAfter", "newOperation", "parent", "companion", "ownedCompanions", "oldName", "oldPathAbsent", "collectionWindowSeconds")
			variants = append(variants, item["variant"].(string))
			Expect(item["currentUid"]).To(Equal(item["originalUid"]))
			Expect(item["blockedCode"]).To(Equal("CONFLICT"))
			Expect(item["oldPathAbsent"]).To(BeTrue())
			Expect(item["oldOperationAfter"]).To(Equal(item["oldOperation"]))
			Expect(item["ownedCompanions"]).To(HaveLen(1))
			Expect(item["collectionWindowSeconds"]).To(BeNumerically(">=", 0))
			resolution := migrationFields(item["resolution"], "resolvedBy", "resolvedAt", "originalUid", "parentReleased")
			Expect(resolution["resolvedBy"]).NotTo(BeEmpty())
			_, e := time.Parse(time.RFC3339Nano, resolution["resolvedAt"].(string))
			Expect(e).NotTo(HaveOccurred())
			Expect(resolution["parentReleased"]).To(BeFalse())
			Expect(migrationFields(item["release"], "parentReleased")["parentReleased"]).To(BeTrue())
			beforeInventory := migrationFields(item["inventory"], "resources")
			afterInventory := migrationFields(item["inventoryAfter"], "resources")
			for _, rawBefore := range beforeInventory["resources"].([]any) {
				original := migrationFields(rawBefore, "operationId", "resourceKey", "documentFingerprint")
				found := false
				for _, rawAfter := range afterInventory["resources"].([]any) {
					current := migrationFields(rawAfter, "operationId", "resourceKey", "documentFingerprint")
					if current["operationId"] == original["operationId"] && current["resourceKey"] == original["resourceKey"] {
						Expect(current["documentFingerprint"]).To(Equal(original["documentFingerprint"]))
						found = true
					}
				}
				Expect(found).To(BeTrue())
			}
			oldSM := migrationSMFact(item["oldOperationAfter"])
			Expect(oldSM["state"]).To(Equal(item["variant"]))
			Expect(oldSM["possiblySent"]).To(BeTrue())
			if item["variant"] == "IDENTITY_UNCERTAIN" {
				Expect(oldSM["confirmedUid"]).To(Equal(""))
				Expect(resolution["originalUid"]).To(Equal(""))
			}
			migrationSingleParent(item["newOperation"])
			parent := migrationFields(item["parent"])
			child := migrationFields(item["companion"])
			Expect(cm(child, "metadata")["name"]).NotTo(Equal(item["oldName"]))
			assertMigrationCompanion(parent, child)
		}
		Expect(variants).To(ConsistOf("SUCCEEDED", "IDENTITY_UNCERTAIN"))
		assertHistoricalCollection(migrationFields(proof, "collection")["collection"])
	case "browser":
		ginkgo.By("I29-B01：页面独立确认旧SM与父身份，新版本单父意图且旧unknown保留")
		migrationFields(proof, "instanceId", "oldOperation", "newOperation", "newVersion", "inventory")
		oldSM := migrationSMFact(proof["oldOperation"])
		Expect(oldSM["state"]).To(Equal("IDENTITY_UNCERTAIN"))
		Expect(oldSM["confirmedUid"]).To(Equal(""))
		Expect(oldSM["possiblySent"]).To(BeTrue())
		migrationSingleParent(proof["newOperation"])
		migrationSingleParent(proof["newVersion"])
		inventory := migrationFields(proof["inventory"], "protectionOperationId", "resources", "impactComplete")
		Expect(inventory["protectionOperationId"]).To(Equal(""))
		Expect(inventory["impactComplete"]).To(BeTrue())
		confirmed := false
		for _, raw := range inventory["resources"].([]any) {
			r := migrationFields(raw, "resourceKey", "resolvedBy", "resolvedAt")
			if r["resourceKey"] == "legacy-sm" {
				Expect(r["resolvedBy"]).NotTo(BeEmpty())
				Expect(r["resolvedAt"]).NotTo(BeEmpty())
				confirmed = true
			}
		}
		Expect(confirmed).To(BeTrue())
	default:
		ginkgo.Fail("未知#29原始证据种类")
	}
	ginkgo.AddReportEntry("I29-"+proof["kind"].(string), "逐项原始观测断言通过；实际执行范围由当前E2E case决定")
}

// 子对象必须由原父UID控制，引用与实际对象一致，不能凭同名认领。
func assertMigrationCompanion(parent, child map[string]any) {
	meta := migrationFields(parent["metadata"], "uid")
	childMeta := migrationFields(child["metadata"], "uid", "name", "namespace", "ownerReferences")
	status := migrationFields(cm(parent, "status")["serviceMonitor"], "state", "resource")
	Expect(status["state"]).To(Equal("Ready"))
	reference := migrationFields(status["resource"], "uid", "name", "namespace")
	for _, key := range []string{"uid", "name", "namespace"} {
		Expect(reference[key]).To(Equal(childMeta[key]))
	}
	Expect(childMeta["namespace"]).To(Equal(cm(parent, "spec")["targetNamespace"]))
	owned := false
	for _, raw := range childMeta["ownerReferences"].([]any) {
		owner := migrationFields(raw)
		if owner["controller"] == true && owner["uid"] == meta["uid"] {
			owned = true
		}
	}
	Expect(owned).To(BeTrue())
}

// 实际进程的精确activeTargets与端点×原Pod样本矩阵共同证明没有重复采集，SM数量不能替代它。
func assertHistoricalCollection(raw any) {
	collection := migrationFields(raw, "receiverBefore", "receiverAfter", "agentBefore", "agentAfter", "adoptedBefore", "adoptedAfter", "targetsBefore", "targetsAfter", "exports", "samples", "timestamps", "cutoverStarted")
	before := migrationFields(collection["receiverBefore"], "process", "jobs", "remoteWrite", "configSHA256")
	after := migrationFields(collection["receiverAfter"], "process", "jobs", "remoteWrite", "configSHA256")
	Expect(before["jobs"]).To(BeEmpty())
	Expect(before["remoteWrite"]).To(BeEmpty())
	Expect(after["jobs"]).To(BeEmpty())
	Expect(after["remoteWrite"]).To(BeEmpty())
	Expect(after["configSHA256"]).To(Equal(before["configSHA256"]))
	for _, key := range []string{"podUID", "containerID"} {
		b := migrationFields(before["process"], key)
		a := migrationFields(after["process"], key)
		Expect(a[key]).NotTo(BeEmpty())
		Expect(a[key]).To(Equal(b[key]))
	}
	oldAgent := migrationFields(collection["agentBefore"], "instance")
	newAgent := migrationFields(collection["agentAfter"], "instance")
	for _, key := range []string{"uid", "currentVersionId"} {
		b := migrationFields(oldAgent["instance"], key)
		a := migrationFields(newAgent["instance"], key)
		Expect(a[key]).NotTo(BeEmpty())
		Expect(a[key]).To(Equal(b[key]))
	}
	oldConfig := migrationFields(collection["adoptedBefore"], "jobs", "configSHA256")
	newConfig := migrationFields(collection["adoptedAfter"], "jobs", "configSHA256", "externalLabels")
	Expect(newConfig["configSHA256"]).NotTo(Equal(oldConfig["configSHA256"]))
	Expect(oldConfig["jobs"]).NotTo(BeEmpty())
	expectedTargets := []string{}
	expectedSamples := []string{}
	jobs := map[string]bool{}
	for _, raw := range collection["exports"].([]any) {
		export := migrationFields(raw, "targets", "pods", "snapshot")
		targets := export["targets"].([]any)
		pods := export["pods"].([]any)
		Expect(targets).NotTo(BeEmpty())
		Expect(pods).NotTo(BeEmpty())
		for _, targetRaw := range targets {
			target := migrationFields(targetRaw, "job", "instance", "service", "namespace", "endpoint", "podUID")
			jobs[target["job"].(string)] = true
			expectedTargets = append(expectedTargets, fmt.Sprint(target["job"], "|", target["instance"], "|", target["service"], "|", target["endpoint"]))
			for _, podRaw := range pods {
				pod := migrationFields(podRaw, "metadata")
				meta := migrationFields(pod["metadata"], "name", "uid")
				expectedSamples = append(expectedSamples, fmt.Sprint(target["namespace"], "|", target["service"], "|", target["instance"], "|", target["endpoint"], "|", meta["name"], "|", meta["uid"]))
			}
		}
	}
	expectedJobs := []string{}
	for job := range jobs {
		expectedJobs = append(expectedJobs, job)
	}
	Expect(newConfig["jobs"]).To(ConsistOf(expectedJobs))
	Expect(collection["targetsBefore"]).NotTo(BeEmpty())
	actualTargets := []string{}
	for _, raw := range collection["targetsAfter"].([]any) {
		target := migrationFields(raw, "scrapePool", "labels", "health", "lastScrape")
		Expect(target["health"]).To(Equal("up"))
		labels := migrationFields(target["labels"], "instance", "service", "endpoint")
		actualTargets = append(actualTargets, fmt.Sprint(target["scrapePool"], "|", labels["instance"], "|", labels["service"], "|", labels["endpoint"]))
	}
	Expect(actualTargets).To(ConsistOf(expectedTargets))
	for _, field := range []string{"samples", "timestamps"} {
		actual := []string{}
		for _, raw := range collection[field].([]any) {
			sample := migrationFields(raw, "metric", "value")
			labels := migrationFields(sample["metric"], "namespace", "service", "instance", "endpoint", "exported_pod", "uid", "prometheus")
			Expect(labels["prometheus"]).To(Equal(cm(newConfig, "externalLabels")["prometheus"]))
			value := sample["value"].([]any)
			Expect(value).To(HaveLen(2))
			if field == "samples" {
				Expect(value[1]).To(Equal("1"))
			} else {
				stamp, e := strconv.ParseFloat(value[1].(string), 64)
				Expect(e).NotTo(HaveOccurred())
				Expect(stamp).To(BeNumerically(">=", collection["cutoverStarted"]))
			}
			actual = append(actual, fmt.Sprint(labels["namespace"], "|", labels["service"], "|", labels["instance"], "|", labels["endpoint"], "|", labels["exported_pod"], "|", labels["uid"]))
		}
		Expect(actual).To(ConsistOf(expectedSamples))
	}
}
