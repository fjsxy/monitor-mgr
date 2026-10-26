// 实际Agent配置与receiver原始样本共同证明采集，Python编排成功不能替代此处业务断言。
package e2e_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
)

func init() {
	registerIssueEvidence("issue30", assertIssue30Evidence)
	requireIssueEvidence("E2E-04", "issue30")
}

type process30 struct{ PodUID, ContainerID, Node, ConfigDigest, ImageID string }
type identity30 struct {
	Instance  struct{ ID, UID, Name, CreatorUserID, CurrentVersionID string }
	CR, Pod   collectionObject
	Workloads []collectionObject
	Container process30
	Version   struct{ VersionID, CreatedAt string }
}
type adopted30 struct {
	Process           process30
	ConfigSHA256      string
	ExternalLabels    map[string]string
	Jobs, RemoteWrite []string
	ScrapeConfigs     []map[string]any
}
type sample30 struct {
	Metric map[string]string
	Value  []any
}
type query30 struct {
	Query                     map[string]string
	Results, TimestampResults []sample30
	NotBefore, ObservedAt     float64
	ReceiverPodUID            string
}
type target30 struct{ Namespace, Service, ServiceUID, EndpointUID, Endpoint, Instance, Pod, PodUID, Job string }
type export30 struct {
	Snapshot        json.RawMessage
	StartedAt       float64
	Pods, Workloads []collectionObject
	Containers      []process30
	Targets         []target30
}
type source30 struct {
	Source, Error, LastAttemptAt, LastSuccessAt string
	Historical                                  *bool
	Matches                                     []struct{ UID, Namespace, Name string }
}
type group30 struct {
	Role, CreatorUserID         string
	PublishedAt                 float64
	InitialDraft                struct{ Agent map[string]any }
	Identity                    identity30
	OwnPreview, MatchingPreview source30
	Adopted, InitialAdopted     adopted30
	Exports                     []export30
	Samples, InitialSamples     []query30
	AllSamples                  []sample30
	NegativeQueries             []query30
}
type audit30 struct {
	AuditID, Stage, Username, Verb string
	ObjectRef                      struct{ APIGroup, Resource, Namespace, Name string }
	ResponseStatus                 struct{ Code int }
}

type receiver30 struct {
	Process                    process30
	ScrapeConfigs, RemoteWrite []map[string]any
	ConfigSHA256               string
	Args                       []string
}

// required30 防止省略/显式null变成Go零值，尤其是零写、空负查询和false权限不能默认通过。
func required30(raw json.RawMessage, keys ...string) map[string]json.RawMessage {
	var fields map[string]json.RawMessage
	Expect(json.Unmarshal(raw, &fields)).To(Succeed())
	Expect(fields).NotTo(BeNil())
	for _, key := range keys {
		Expect(fields).To(HaveKey(key), "缺少原始证据字段 "+key)
		Expect(strings.TrimSpace(string(fields[key]))).NotTo(Equal("null"), key+"不能为null")
	}
	return fields
}

// 读取显式JSON数组，null不能替代空集合。
func items30(raw json.RawMessage) []json.RawMessage {
	var items []json.RawMessage
	Expect(json.Unmarshal(raw, &items)).To(Succeed())
	Expect(items).NotTo(BeNil())
	return items
}

// 运行容器身份与环境已经核验的OCI config对应。
func processProof30(value process30, expected string) {
	Expect(expected).To(MatchRegexp("^sha256:[0-9a-f]{64}$"))
	Expect(value.PodUID).NotTo(BeEmpty())
	Expect(value.ContainerID).To(MatchRegexp("^[0-9a-f]{64}$"))
	Expect(value.Node).NotTo(BeEmpty())
	Expect(value.ConfigDigest).To(Equal(expected))
	Expect(value.ImageID).To(ContainSubstring("sha256:"))
}

// 采集样本按全部目标标签建独立键，避免只凭指标存在放行。
func tuple30(m map[string]string) string {
	out := []string{}
	for _, key := range []string{"namespace", "service", "instance", "endpoint", "exported_pod", "uid", "prometheus"} {
		Expect(m).To(HaveKey(key))
		Expect(m[key]).NotTo(BeEmpty())
		out = append(out, m[key])
	}
	return strings.Join(out, "\x00")
}

// Prometheus value保留原数字或字符串，解析失败即中止验收。
func number30(value any) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case string:
		n, err := strconv.ParseFloat(v, 64)
		Expect(err).NotTo(HaveOccurred())
		return n
	}
	ginkgo.Fail("样本值不是数字")
	return 0
}

// 所有权沿实际controller引用核对，名称相同不能补认UID。
func owned30(child collectionObject, parents []collectionObject) bool {
	for _, owner := range child.Metadata.OwnerReferences {
		if !owner.Controller {
			continue
		}
		for _, parent := range parents {
			if owner.UID == parent.Metadata.UID && owner.Name == parent.Metadata.Name && owner.Kind == parent.Kind && owner.APIVersion == parent.APIVersion {
				return true
			}
		}
	}
	return false
}

// 平台已发布身份、实际CR和原工作负载链必须指向同一个Agent。
func identityProof30(value identity30, expectedConfig string) {
	Expect(value.Instance.ID).NotTo(BeEmpty())
	Expect(value.Instance.UID).To(Equal(value.CR.Metadata.UID))
	Expect(value.Instance.Name).To(Equal(value.CR.Metadata.Name))
	Expect(value.Instance.CurrentVersionID).NotTo(BeEmpty())
	Expect(value.Version.VersionID).To(Equal(value.Instance.CurrentVersionID))
	_, err := time.Parse(time.RFC3339Nano, value.Version.CreatedAt)
	Expect(err).NotTo(HaveOccurred())
	Expect(value.Instance.CreatorUserID).NotTo(BeEmpty())
	Expect(value.CR.Spec).NotTo(BeEmpty())
	Expect(value.CR.Metadata.Generation).To(BeNumerically(">", 0))
	Expect(value.CR.Kind).To(Equal("PrometheusAgent"))
	Expect(value.CR.APIVersion).To(Equal("monitoring.coreos.com/v1alpha1"))
	Expect(value.CR.Metadata.Namespace).NotTo(BeEmpty())
	Expect(value.Workloads).To(HaveLen(1))
	Expect(owned30(value.Workloads[0], []collectionObject{value.CR})).To(BeTrue())
	Expect(owned30(value.Pod, value.Workloads)).To(BeTrue())
	Expect(value.Container.PodUID).To(Equal(value.Pod.Metadata.UID))
	processProof30(value.Container, expectedConfig)
}

// targets30 以实际SM selector/命名端口/Endpoints与Pod所有权建立目标矩阵，不猜Operator资源名称。
func targets30(exported export30, creator, prometheus, ksmConfig string) (map[string]bool, []string, collectionSnapshot) {
	var snapshot collectionSnapshot
	Expect(json.Unmarshal(exported.Snapshot, &snapshot)).To(Succeed())
	assertCompanion(snapshot)
	Expect(snapshot.Instance.CreatorUserID).To(Equal(creator))
	Expect(exported.StartedAt).To(BeNumerically(">", 0))
	Expect(exported.Pods).NotTo(BeEmpty())
	Expect(exported.Containers).To(HaveLen(len(exported.Pods)))
	graph := append([]collectionObject{snapshot.Parent}, exported.Workloads...)
	reachable := []collectionObject{snapshot.Parent}
	for range graph {
		for _, object := range exported.Workloads {
			if owned30(object, reachable) {
				reachable = append(reachable, object)
			}
		}
	}
	for n, pod := range exported.Pods {
		Expect(pod.Kind).To(Equal("Pod"))
		Expect(pod.Metadata.Namespace).To(Equal(snapshot.Companion.Metadata.Namespace))
		Expect(owned30(pod, reachable)).To(BeTrue(), "源Pod必须沿真实controller owner链属于该exporter")
		Expect(exported.Containers[n].PodUID).To(Equal(pod.Metadata.UID))
		processProof30(exported.Containers[n], ksmConfig)
	}
	var raw struct {
		Endpoints []struct {
			Metadata collectionMeta
			Subsets  []struct {
				Addresses []struct {
					IP        string
					TargetRef struct{ Kind, Name, Namespace, UID string }
				}
				Ports []struct {
					Name string
					Port int
				}
			}
		}
	}
	Expect(json.Unmarshal(exported.Snapshot, &raw)).To(Succeed())
	var selector metav1.LabelSelector
	blob, err := json.Marshal(snapshot.Companion.Spec["selector"])
	Expect(err).NotTo(HaveOccurred())
	Expect(json.Unmarshal(blob, &selector)).To(Succeed())
	filter, err := metav1.LabelSelectorAsSelector(&selector)
	Expect(err).NotTo(HaveOccurred())
	wantedTargets := map[target30]bool{}
	jobs := []string{}
	endpoints := snapshot.Companion.Spec["endpoints"].([]any)
	for index, scrape := range endpoints {
		portName := cs(scrape.(map[string]any), "port")
		job := fmt.Sprintf("serviceMonitor/%s/%s/%d", snapshot.Companion.Metadata.Namespace, snapshot.Companion.Metadata.Name, index)
		jobs = append(jobs, job)
		for _, service := range snapshot.Services {
			if !filter.Matches(labels.Set(service.Metadata.Labels)) {
				continue
			}
			Expect(service.Metadata.UID).NotTo(BeEmpty())
			for _, endpoint := range raw.Endpoints {
				if endpoint.Metadata.Name != service.Metadata.Name || endpoint.Metadata.Namespace != service.Metadata.Namespace {
					continue
				}
				Expect(endpoint.Metadata.UID).NotTo(BeEmpty())
				for _, subset := range endpoint.Subsets {
					for _, port := range subset.Ports {
						if port.Name != portName {
							continue
						}
						Expect(port.Port).To(BeNumerically(">", 0))
						for _, address := range subset.Addresses {
							Expect(address.TargetRef.Kind).To(Equal("Pod"))
							Expect(address.IP).NotTo(BeEmpty())
							found := false
							for _, pod := range exported.Pods {
								if pod.Metadata.UID == address.TargetRef.UID && pod.Metadata.Name == address.TargetRef.Name && pod.Metadata.Namespace == address.TargetRef.Namespace {
									found = true
								}
							}
							Expect(found).To(BeTrue(), "Endpoint targetRef必须匹配原PodUID")
							target := target30{Namespace: service.Metadata.Namespace, Service: service.Metadata.Name, ServiceUID: service.Metadata.UID, EndpointUID: endpoint.Metadata.UID, Endpoint: portName, Instance: address.IP + ":" + strconv.Itoa(port.Port), Pod: address.TargetRef.Name, PodUID: address.TargetRef.UID, Job: job}
							wantedTargets[target] = true
						}
					}
				}
			}
		}
	}
	Expect(wantedTargets).NotTo(BeEmpty())
	Expect(exported.Targets).To(HaveLen(len(wantedTargets)))
	seen := map[target30]bool{}
	expected := map[string]bool{}
	for _, target := range exported.Targets {
		Expect(wantedTargets).To(HaveKey(target))
		Expect(seen).NotTo(HaveKey(target))
		seen[target] = true
		// kube-state-metrics每个真实scrape endpoint报告该namespace所有Pod，因此独立建立endpoint×被报告Pod的矩阵。
		for _, pod := range exported.Pods {
			expected[tuple30(map[string]string{"namespace": target.Namespace, "service": target.Service, "instance": target.Instance, "endpoint": target.Endpoint, "exported_pod": pod.Metadata.Name, "uid": pod.Metadata.UID, "prometheus": prometheus})] = true
		}
	}
	return expected, jobs, snapshot
}

// 公开原始观测必须同时满足控制面、数据面、权限和动态发现边界。
func assertIssue30Evidence(evidence map[string]json.RawMessage) {
	raw := evidence["issue30"]
	fields := required30(raw, "testedCommit", "runId", "metric", "groups", "credentials", "operatorIdentity", "operatorProofs", "operatorContent", "monitorOpBefore", "monitorOpAfter", "runtimeConfigs", "receiverBefore", "receiverAfter", "receiverInitialSamples", "receiverURL", "dynamicBefore", "dynamicAfter", "dynamicAuditBefore", "dynamicAuditAfter", "adminEditedDraft", "protectedAgentSave", "prometheusOperator", "readFailure", "permissionFailure", "recoveredPreview", "audit")
	var proof struct {
		DynamicAuditBefore, DynamicAuditAfter                      []audit30
		ReceiverInitialSamples                                     []sample30
		MonitorOpBefore, MonitorOpAfter                            process30
		TestedCommit, RunID, Metric, OperatorIdentity, ReceiverURL string
		Groups                                                     []group30
		ReceiverBefore, ReceiverAfter                              receiver30
		DynamicBefore, DynamicAfter                                identity30
		RuntimeConfigs                                             map[string]string
		AdminEditedDraft                                           struct{ Agent map[string]any }
		ProtectedAgentSave                                         struct{ Code string }
		Credentials                                                struct {
			Identity, ServiceAccountUID string
			IdentityReview              struct {
				Status struct{ UserInfo struct{ Username string } }
			}
			Permissions []struct {
				Group, Resource, Verb string
				Status                struct{ Allowed *bool }
			}
		}
		OperatorProofs  []struct{ Target, Deployment, Source, PodUID, ContainerID, ConfigDigest, Node string }
		OperatorContent []struct {
			Node, Source, Platform, IndexDigest, ManifestDigest, ConfigDigest string
			OriginalContentBytesVerified                                      *bool
		}
		PrometheusOperator struct {
			Before, After process30
			Metrics       []struct {
				Metric string
				Value  float64
			}
		}
		ReadFailure, PermissionFailure, RecoveredPreview source30
		Audit                                            []audit30
	}
	Expect(json.Unmarshal(raw, &proof)).To(Succeed())
	Expect(proof.TestedCommit).To(MatchRegexp("^[0-9a-f]{40}$"))
	Expect(proof.TestedCommit).To(Equal(os.Getenv("MONITOR_MGR_E2E_CANDIDATE")))
	Expect(proof.RunID).To(HavePrefix("mm-"))
	Expect(proof.Metric).To(Equal("kube_pod_info"))
	Expect(proof.Groups).To(HaveLen(2))
	Expect(proof.RuntimeConfigs).To(HaveKey("prometheus"))
	Expect(proof.RuntimeConfigs).To(HaveKey("ksm"))
	Expect(proof.RuntimeConfigs).To(HaveKey("promop"))
	ginkgo.By("I30-K01.receiver：原receiver无主动采集、无发送且保持同Pod/容器/config")
	for _, key := range []string{"receiverBefore", "receiverAfter"} {
		required30(fields[key], "process", "scrapeConfigs", "remoteWrite", "configSHA256", "args")
	}
	for _, receiver := range []receiver30{proof.ReceiverBefore, proof.ReceiverAfter} {
		Expect(receiver.ScrapeConfigs).NotTo(BeNil())
		Expect(receiver.ScrapeConfigs).To(BeEmpty())
		Expect(receiver.RemoteWrite).NotTo(BeNil())
		Expect(receiver.RemoteWrite).To(BeEmpty())
		Expect(receiver.Args).To(ContainElement("--web.enable-remote-write-receiver"))
		processProof30(receiver.Process, proof.RuntimeConfigs["prometheus"])
		Expect(receiver.ConfigSHA256).To(MatchRegexp("^[0-9a-f]{64}$"))
	}
	Expect(proof.ReceiverInitialSamples).NotTo(BeNil())
	Expect(proof.ReceiverInitialSamples).To(BeEmpty())
	Expect(proof.ReceiverAfter).To(Equal(proof.ReceiverBefore))
	Expect(proof.ReceiverURL).To(HaveSuffix("/api/v1/write"))
	groupFields := items30(fields["groups"])
	expectedByRole := map[string]map[string]bool{}
	snapshots := map[string][]collectionSnapshot{}
	roles := map[string]bool{}
	creators := map[string]bool{}
	for index, group := range proof.Groups {
		rawGroup := required30(groupFields[index], "role", "creatorUserId", "publishedAt", "initialDraft", "identity", "ownPreview", "matchingPreview", "adopted", "initialAdopted", "exports", "samples", "initialSamples", "allSamples", "negativeQueries")
		Expect([]string{"user", "admin"}).To(ContainElement(group.Role))
		Expect(roles).NotTo(HaveKey(group.Role))
		roles[group.Role] = true
		Expect(creators).NotTo(HaveKey(group.CreatorUserID))
		creators[group.CreatorUserID] = true
		ginkgo.By("I30-K01." + group.Role + "：固定创建用户、默认None/Own与真实跨namespace匹配")
		Expect(group.CreatorUserID).NotTo(BeEmpty())
		Expect(group.PublishedAt).To(BeNumerically(">", 0))
		Expect(group.Identity.Instance.CreatorUserID).To(Equal(group.CreatorUserID))
		identityProof30(group.Identity, proof.RuntimeConfigs["prometheus"])
		associations := cm(group.InitialDraft.Agent, "associations")
		for _, kind := range []string{"serviceMonitor", "podMonitor", "probe", "scrapeConfig"} {
			rule := cm(associations, kind)
			Expect(cm(rule, "object")["mode"]).To(Equal("None"))
			Expect(cm(rule, "namespace")["mode"]).To(Equal("Own"))
		}
		for _, name := range []string{"ownPreview", "matchingPreview"} {
			required30(rawGroup[name], "source", "lastAttemptAt", "lastSuccessAt", "error", "historical", "matches")
		}
		Expect(group.OwnPreview.Source).To(Equal("association:ServiceMonitor"))
		Expect(group.OwnPreview.Matches).NotTo(BeNil())
		Expect(group.OwnPreview.Matches).To(BeEmpty())
		Expect(group.OwnPreview.Error).To(BeEmpty())
		Expect(group.OwnPreview.LastSuccessAt).NotTo(BeEmpty())
		Expect(group.OwnPreview.Historical).NotTo(BeNil())
		Expect(*group.OwnPreview.Historical).To(BeFalse())
		rule := cm(group.Identity.CR.Spec, "serviceMonitorSelector")
		Expect(cm(rule, "matchLabels")["monitor-mgr.io/user-id"]).To(Equal(group.CreatorUserID))
		Expect(cm(group.Identity.CR.Spec, "serviceMonitorNamespaceSelector")).NotTo(BeEmpty())
		Expect(group.Adopted.Process).To(Equal(group.InitialAdopted.Process))
		Expect(group.Adopted.Process.PodUID).To(Equal(group.Identity.Container.PodUID))
		Expect(group.Adopted.Process.ContainerID).To(Equal(group.Identity.Container.ContainerID))
		processProof30(group.Adopted.Process, proof.RuntimeConfigs["prometheus"])
		Expect(group.Adopted.RemoteWrite).To(Equal([]string{proof.ReceiverURL}))
		Expect(group.Adopted.ConfigSHA256).To(MatchRegexp("^[0-9a-f]{64}$"))
		external := group.Identity.CR.Metadata.Namespace + "/" + group.Identity.CR.Metadata.Name
		Expect(group.Adopted.ExternalLabels["prometheus"]).To(Equal(external))
		exportCount := 1
		if group.Role == "user" {
			exportCount = 2
		}
		Expect(group.Exports).To(HaveLen(exportCount))
		wanted := map[string]bool{}
		minimumByTuple := map[string]float64{}
		jobs := []string{}
		for _, exported := range group.Exports {
			expected, actualJobs, snapshot := targets30(exported, group.CreatorUserID, external, proof.RuntimeConfigs["ksm"])
			for key := range expected {
				Expect(wanted).NotTo(HaveKey(key))
				wanted[key] = true
				minimum := group.PublishedAt
				if exported.StartedAt > minimum {
					minimum = exported.StartedAt
				}
				agentTime, err := time.Parse(time.RFC3339Nano, group.Identity.Version.CreatedAt)
				Expect(err).NotTo(HaveOccurred())
				if float64(agentTime.UnixNano())/1e9 > minimum {
					minimum = float64(agentTime.UnixNano()) / 1e9
				}
				var frozen struct{ Version struct{ CreatedAt string } }
				Expect(json.Unmarshal(exported.Snapshot, &frozen)).To(Succeed())
				exportTime, err := time.Parse(time.RFC3339Nano, frozen.Version.CreatedAt)
				Expect(err).NotTo(HaveOccurred())
				if float64(exportTime.UnixNano())/1e9 > minimum {
					minimum = float64(exportTime.UnixNano()) / 1e9
				}
				minimumByTuple[key] = minimum
			}
			jobs = append(jobs, actualJobs...)
			snapshots[group.Role] = append(snapshots[group.Role], snapshot)
		}
		sort.Strings(jobs)
		actualJobs := append([]string{}, group.Adopted.Jobs...)
		sort.Strings(actualJobs)
		Expect(actualJobs).To(Equal(jobs), "实际采用job必须恰好是Operator配套集合")
		Expect(group.MatchingPreview.Historical).NotTo(BeNil())
		Expect(*group.MatchingPreview.Historical).To(BeFalse())
		Expect(group.MatchingPreview.Error).To(BeEmpty())
		Expect(group.MatchingPreview.LastSuccessAt).NotTo(BeEmpty())
		Expect(group.MatchingPreview.Matches).To(HaveLen(1))
		Expect(group.MatchingPreview.Matches[0].UID).To(Equal(snapshots[group.Role][0].Companion.Metadata.UID))
		ginkgo.By("I30-K01." + group.Role + "：receiver真实样本与全部Service/Endpoint/PodUID逐项对应")
		Expect(group.Samples).To(HaveLen(len(wanted)))
		observed := map[string]bool{}
		for _, sample := range group.Samples {
			Expect(sample.Query).NotTo(BeNil())
			key := tuple30(sample.Query)
			Expect(wanted).To(HaveKey(key))
			Expect(observed).NotTo(HaveKey(key))
			observed[key] = true
			Expect(sample.Results).To(HaveLen(1))
			row := sample.Results[0]
			Expect(row.Metric["__name__"]).To(Equal("kube_pod_info"))
			Expect(tuple30(row.Metric)).To(Equal(key))
			Expect(row.Value).To(HaveLen(2))
			Expect(number30(row.Value[1])).To(Equal(float64(1)))
			Expect(sample.TimestampResults).To(HaveLen(1))
			Expect(sample.TimestampResults[0].Value).To(HaveLen(2))
			Expect(tuple30(sample.TimestampResults[0].Metric)).To(Equal(key))
			sampleTime := number30(sample.TimestampResults[0].Value[1])
			Expect(sample.NotBefore).To(BeNumerically(">=", group.PublishedAt))
			Expect(sampleTime).To(BeNumerically(">=", sample.NotBefore))
			Expect(sampleTime).To(BeNumerically(">=", minimumByTuple[key]))
			Expect(number30(row.Value[0])).To(BeNumerically("<=", float64(time.Now().Unix())+5))
			Expect(sampleTime).To(BeNumerically("<=", number30(row.Value[0])+5))
			Expect(sample.ReceiverPodUID).To(Equal(proof.ReceiverBefore.Process.PodUID))
		}
		ginkgo.By("I30-K01.exact：" + group.Role + "组全部查询无额外样本/目标")
		Expect(group.AllSamples).NotTo(BeNil())
		Expect(group.AllSamples).To(HaveLen(len(wanted)))
		all := map[string]bool{}
		for _, row := range group.AllSamples {
			Expect(row.Metric["__name__"]).To(Equal("kube_pod_info"))
			key := tuple30(row.Metric)
			Expect(wanted).To(HaveKey(key))
			Expect(all).NotTo(HaveKey(key))
			all[key] = true
			Expect(row.Value).To(HaveLen(2))
			Expect(number30(row.Value[1])).To(Equal(float64(1)))
		}
		expectedByRole[group.Role] = wanted
		// 关键：空负查询必须显式有results=[]，省略/null不能被解释成“没有另一组样本”。
		negatives := items30(rawGroup["negativeQueries"])
		for _, negative := range negatives {
			required30(negative, "query", "results", "observedAt")
			Expect(items30(required30(negative)["results"])).To(BeEmpty())
		}
		ginkgo.AddReportEntry("I30-K01."+group.Role, fmt.Sprintf("%d个实际Endpoint/Pod样本，全部查询精确相等", len(wanted)))
	}
	ginkgo.By("I30-K01.negative-user/negative-admin：两个方向的真实负样本查询完整且为空")
	for _, group := range proof.Groups {
		other := "user"
		if group.Role == "user" {
			other = "admin"
		}
		expected := map[string]bool{}
		for _, e := range proof.Groups {
			if e.Role != other {
				continue
			}
			for _, exported := range e.Exports {
				var snap collectionSnapshot
				Expect(json.Unmarshal(exported.Snapshot, &snap)).To(Succeed())
				for _, pod := range exported.Pods {
					expected[snap.Companion.Metadata.Namespace+"/"+pod.Metadata.UID] = true
				}
			}
		}
		Expect(group.NegativeQueries).To(HaveLen(len(expected)))
		seen := map[string]bool{}
		for _, q := range group.NegativeQueries {
			Expect(q.Query).To(HaveLen(3))
			Expect(q.Query["prometheus"]).To(Equal(group.Adopted.ExternalLabels["prometheus"]))
			key := q.Query["namespace"] + "/" + q.Query["uid"]
			Expect(expected).To(HaveKey(key))
			Expect(seen).NotTo(HaveKey(key))
			seen[key] = true
			Expect(q.Results).NotTo(BeNil())
			Expect(q.Results).To(BeEmpty())
			Expect(q.ObservedAt).To(BeNumerically(">", proof.Groups[0].PublishedAt))
		}
	}
	ginkgo.By("I30-K02.dynamic/edit：动态新增不重发Agent，管理员编辑也保留原目标")
	before, after := proof.DynamicBefore, proof.DynamicAfter
	identityProof30(before, proof.RuntimeConfigs["prometheus"])
	identityProof30(after, proof.RuntimeConfigs["prometheus"])
	Expect(after.Instance).To(Equal(before.Instance))
	Expect(after.CR.Spec).To(Equal(before.CR.Spec))
	Expect(after.CR.Metadata.Generation).To(Equal(before.CR.Metadata.Generation))
	Expect(after.CR.Metadata.UID).To(Equal(before.CR.Metadata.UID))
	Expect(after.Container).To(Equal(before.Container))
	user := proof.Groups[0]
	if user.Role != "user" {
		user = proof.Groups[1]
	}
	Expect(after.Instance.UID).To(Equal(user.Identity.Instance.UID))
	Expect(after.Instance.CurrentVersionID).To(Equal(user.Identity.Instance.CurrentVersionID))
	Expect(user.Adopted.ConfigSHA256).NotTo(Equal(user.InitialAdopted.ConfigSHA256))
	Expect(user.InitialAdopted.Jobs).To(HaveLen(len(user.Adopted.Jobs) - len(snapshots["user"][1].Companion.Spec["endpoints"].([]any))))
	edited := cm(cm(proof.AdminEditedDraft.Agent, "associations"), "serviceMonitor")
	Expect(cm(edited, "object")["mode"]).To(Equal("Labels"))
	Expect(cm(cm(edited, "object"), "selector")).To(Equal(cm(user.Identity.CR.Spec, "serviceMonitorSelector")))
	Expect(cm(edited, "namespace")["mode"]).To(Equal("Labels"))
	Expect(cm(cm(edited, "namespace"), "selector")).To(Equal(cm(user.Identity.CR.Spec, "serviceMonitorNamespaceSelector")))
	initialJobs := []string{}
	first := snapshots["user"][0].Companion
	for n := range first.Spec["endpoints"].([]any) {
		initialJobs = append(initialJobs, fmt.Sprintf("serviceMonitor/%s/%s/%d", first.Metadata.Namespace, first.Metadata.Name, n))
	}
	Expect(user.InitialAdopted.Jobs).To(ConsistOf(initialJobs))
	initialExpected, _, _ := targets30(user.Exports[0], user.CreatorUserID, user.Adopted.ExternalLabels["prometheus"], proof.RuntimeConfigs["ksm"])
	Expect(user.InitialSamples).To(HaveLen(len(initialExpected)))
	for _, sample := range user.InitialSamples {
		Expect(initialExpected).To(HaveKey(tuple30(sample.Query)))
		Expect(sample.Results).To(HaveLen(1))
		Expect(sample.Results[0].Value).To(HaveLen(2))
		Expect(number30(sample.Results[0].Value[0])).To(BeNumerically("<", user.Exports[1].StartedAt), "新增exporter前已取得原目标查询")
	}

	Expect(proof.DynamicAuditBefore).NotTo(BeNil())
	Expect(proof.DynamicAuditAfter).NotTo(BeNil())
	agentWrites := func(events []audit30) map[string]bool {
		out := map[string]bool{}
		for _, event := range events {
			if event.Username == proof.Credentials.Identity && event.ObjectRef.Resource == "prometheusagents" && event.ObjectRef.Namespace == before.CR.Metadata.Namespace && (event.ObjectRef.Name == "" || event.ObjectRef.Name == before.CR.Metadata.Name) && (event.Verb == "create" || event.Verb == "update" || event.Verb == "patch" || event.Verb == "delete" || event.Verb == "deletecollection") {
				Expect(event.AuditID).NotTo(BeEmpty())
				out[event.AuditID] = true
			}
		}
		return out
	}
	initialWrites := agentWrites(proof.DynamicAuditBefore)
	Expect(initialWrites).NotTo(BeEmpty(), "原Agent发布须有同身份写正控")
	Expect(agentWrites(proof.DynamicAuditAfter)).To(Equal(initialWrites), "动态发现期间连相同spec的重复写尝试也不允许")
	ginkgo.By("I30-K03.process：同原Prometheus Operator进程的Agent controller和ScrapeConfig informer能力")
	processProof30(proof.PrometheusOperator.Before, proof.RuntimeConfigs["promop"])
	Expect(proof.PrometheusOperator.After).To(Equal(proof.PrometheusOperator.Before))
	agentReady, scrapeObserved := false, false
	for _, metric := range proof.PrometheusOperator.Metrics {
		if strings.HasPrefix(metric.Metric, "prometheus_operator_ready{") && strings.Contains(metric.Metric, `controller="prometheus-agent"`) && metric.Value == 1 {
			agentReady = true
		}
		if strings.HasPrefix(metric.Metric, "prometheus_operator_kubernetes_client_http_request_duration_seconds_count{") && regexp.MustCompile(`endpoint="/apis/monitoring\.coreos\.com/v1alpha1/(namespaces/[^/]+/)?scrapeconfigs"`).MatchString(metric.Metric) && metric.Value > 0 {
			scrapeObserved = true
		}
	}
	Expect(agentReady).To(BeTrue())
	Expect(scrapeObserved).To(BeTrue())
	ginkgo.By("I30-K03.read-failure/forbidden：真实故障、真实SA读取拒绝和零匹配分开")
	for _, name := range []string{"readFailure", "permissionFailure", "recoveredPreview"} {
		required30(fields[name], "source", "lastAttemptAt", "lastSuccessAt", "error", "historical", "matches")
	}
	Expect(proof.ReadFailure.Error).To(HavePrefix("TARGET_ERROR"))
	Expect(proof.PermissionFailure.Error).To(HavePrefix("TARGET_FORBIDDEN"))
	Expect(proof.ReadFailure.Error).NotTo(Equal(proof.PermissionFailure.Error))
	for _, source := range []source30{proof.ReadFailure, proof.PermissionFailure} {
		Expect(source.LastSuccessAt).To(BeEmpty())
		Expect(source.LastAttemptAt).NotTo(BeEmpty())
		_, err := time.Parse(time.RFC3339Nano, source.LastAttemptAt)
		Expect(err).NotTo(HaveOccurred())
		Expect(source.Historical).NotTo(BeNil())
		Expect(*source.Historical).To(BeFalse())
		Expect(source.Matches).NotTo(BeNil())
		Expect(source.Matches).To(BeEmpty())
	}
	Expect(proof.RecoveredPreview.Error).To(BeEmpty())
	Expect(proof.RecoveredPreview.LastSuccessAt).NotTo(BeEmpty())
	Expect(proof.RecoveredPreview.Matches).To(HaveLen(2))
	Expect(proof.ProtectedAgentSave.Code).To(Equal("FORBIDDEN"))
	ginkgo.By("I30-K01.audit：真实平台SA无SM写权限，所有写尝试为0，并有父写/SM读/Operator创建正控")
	credentials := proof.Credentials
	Expect(credentials.Identity).To(HavePrefix("system:serviceaccount:"))
	Expect(credentials.ServiceAccountUID).NotTo(BeEmpty())
	Expect(credentials.IdentityReview.Status.UserInfo.Username).To(Equal(credentials.Identity))
	perms := map[string]bool{}
	for _, permission := range credentials.Permissions {
		Expect(permission.Status.Allowed).NotTo(BeNil())
		if permission.Resource == "servicemonitors" {
			Expect(permission.Group).To(Equal("monitoring.coreos.com"))
			perms[permission.Verb] = *permission.Status.Allowed
		}
	}
	for _, verb := range []string{"get", "list", "watch"} {
		Expect(perms).To(HaveKeyWithValue(verb, true))
	}
	for _, verb := range []string{"create", "update", "patch", "delete", "deletecollection"} {
		Expect(perms).To(HaveKeyWithValue(verb, false))
	}
	Expect(proof.Audit).NotTo(BeEmpty())
	parentWrites, smReads, forbiddenReads := 0, 0, 0
	created := map[string]bool{}
	writes := map[string]bool{"create": true, "update": true, "patch": true, "delete": true, "deletecollection": true}
	for _, event := range proof.Audit {
		Expect(event.AuditID).NotTo(BeEmpty())
		Expect(event.Username).NotTo(BeEmpty())
		Expect(event.Verb).NotTo(BeEmpty())
		if event.Username == credentials.Identity {
			if event.ObjectRef.Resource == "servicemonitors" {
				Expect(writes[event.Verb]).To(BeFalse(), "任何审计stage/失败/dryRun写尝试均不能遗漏")
				if event.Stage == "ResponseComplete" && event.ResponseStatus.Code >= 200 && event.ResponseStatus.Code < 300 {
					smReads++
				}
				if event.Stage == "ResponseComplete" && event.ResponseStatus.Code == 403 {
					forbiddenReads++
				}
			}
			if event.ObjectRef.Resource == "kubestatemetrics" && writes[event.Verb] && event.Stage == "ResponseComplete" && event.ResponseStatus.Code >= 200 && event.ResponseStatus.Code < 300 {
				parentWrites++
			}
		}
		if event.Username == proof.OperatorIdentity && event.ObjectRef.Resource == "servicemonitors" && event.Verb == "create" && event.Stage == "ResponseComplete" && event.ResponseStatus.Code == 201 {
			created[event.ObjectRef.Namespace+"/"+event.ObjectRef.Name] = true
		}
	}
	Expect(parentWrites).To(BeNumerically(">", 0))
	Expect(smReads).To(BeNumerically(">", 0))
	Expect(forbiddenReads).To(BeNumerically(">", 0))
	Expect(proof.OperatorIdentity).To(HavePrefix("system:serviceaccount:"))
	Expect(proof.OperatorIdentity).NotTo(Equal(credentials.Identity))
	for _, groupSnapshots := range snapshots {
		for _, snapshot := range groupSnapshots {
			Expect(created).To(HaveKey(snapshot.Companion.Metadata.Namespace + "/" + snapshot.Companion.Metadata.Name))
		}
	}
	ginkgo.By("I30-K01：实际monitor-op RC4容器绑定原发布index/manifest/config/source")
	Expect(proof.OperatorContent).NotTo(BeEmpty())
	configs := map[string]string{}
	for _, content := range proof.OperatorContent {
		Expect(content.OriginalContentBytesVerified).NotTo(BeNil())
		Expect(*content.OriginalContentBytesVerified).To(BeTrue())
		Expect(content.Source).To(Equal("8fb5ccb8ec2e9c0742fc7b83316d01c833af9aee"))
		Expect(content.IndexDigest).To(Equal("sha256:1a9ec41de21c7b2b633ee7179f5f3f4c1a9e098f1dc4e74a9e720dbee73f19ff"))
		Expect(content.ConfigDigest).To(MatchRegexp("^sha256:[0-9a-f]{64}$"))
		expected := map[string]string{"linux/arm64": "sha256:0eadb4af76f5a1c72eb3f5bb4b8372eab43dbabb0a4dfb194866d58cf854ebde", "linux/amd64": "sha256:76fb66b77b9b9ed6094ef70fc84fb85a0f22d7b63416414bde09c6ca289f9065"}
		Expect(expected).To(HaveKeyWithValue(content.Platform, content.ManifestDigest))
		configs[content.Node] = content.ConfigDigest
	}
	Expect(proof.MonitorOpAfter).To(Equal(proof.MonitorOpBefore))
	Expect(configs).To(HaveKey(proof.MonitorOpBefore.Node))
	processProof30(proof.MonitorOpBefore, configs[proof.MonitorOpBefore.Node])
	foundPrometheus := false
	foundOperator := false
	for _, operator := range proof.OperatorProofs {
		if operator.Target == "data" && operator.Deployment == "prometheus-operator" {
			foundPrometheus = true
			Expect(operator.PodUID).To(Equal(proof.PrometheusOperator.Before.PodUID))
			Expect(operator.ContainerID).To(Equal(proof.PrometheusOperator.Before.ContainerID))
		}
		if operator.Target == "data" && operator.Source == "8fb5ccb8ec2e9c0742fc7b83316d01c833af9aee" {
			foundOperator = true
			Expect(operator.PodUID).To(Equal(proof.MonitorOpBefore.PodUID))
			Expect(operator.ContainerID).To(Equal(proof.MonitorOpBefore.ContainerID))
			Expect(operator.ContainerID).To(MatchRegexp("^[0-9a-f]{64}$"))
			Expect(configs).To(HaveKeyWithValue(operator.Node, operator.ConfigDigest))
		}
	}
	Expect(foundOperator).To(BeTrue())
	Expect(foundPrometheus).To(BeTrue())
	// 每个空数组/false/数值由明确原始字段提供；包括嵌套样本和受限权限的false。
	for _, rawGroup := range groupFields {
		group := required30(rawGroup)
		for _, rawExport := range items30(group["exports"]) {
			exported := required30(rawExport, "snapshot", "startedAt", "pods", "workloads", "containers", "targets")
			snapshot := required30(exported["snapshot"], "parent", "companion", "sources", "version", "services", "endpoints")
			for _, source := range items30(snapshot["sources"]) {
				required30(source, "source", "error", "historical", "stale", "companion")
			}
		}
		for _, name := range []string{"samples", "initialSamples"} {
			for _, sample := range items30(group[name]) {
				required30(sample, "query", "results", "timestampResults", "notBefore", "receiverPodUID")
			}
		}
	}
	ginkgo.AddReportEntry("I30-K01/K02/K03", "真实身份、全部Endpoint样本、双向负查询、精确集合、动态不重发和零SM写分别通过")
}
