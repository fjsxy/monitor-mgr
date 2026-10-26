// 采集意图的真实集群原始证据由本文件执行 Ginkgo/Gomega 业务断言。
package e2e_test

import (
	"encoding/json"
	"os"
	"sort"

	"github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"gopkg.in/yaml.v3"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
)

func init() {
	registerIssueEvidence("issue28", assertIssue28Evidence)
	requireIssueEvidence("E2E-03", "issue28")
}

type collectionMeta struct {
	Name, Namespace, UID, ResourceVersion string
	Generation                            int64
	Labels                                map[string]string
	OwnerReferences                       []struct {
		APIVersion, Kind, Name, UID string
		Controller                  bool
	}
}
type collectionObject struct {
	APIVersion, Kind string
	Metadata         collectionMeta
	Spec, Status     map[string]any
	Subsets          []struct {
		Addresses []struct{ IP string }
		Ports     []struct {
			Name string
			Port int
		}
	}
}
type collectionSnapshot struct {
	Instance  struct{ ID, UID, Name, CreatorUserID, CurrentVersionID, Lifecycle string }
	Parent    collectionObject
	Companion *collectionObject
	Sources   []struct {
		Source, Error     string
		Historical, Stale bool
		Companion         struct {
			State, Reason, ParentUID, Yaml string
			Resource                       struct{ APIVersion, Kind, Namespace, Name, UID string }
		}
	}
	Version struct {
		VersionID, Yaml string
		Resources       []struct{ Key, APIVersion, Kind, Namespace, Name, Yaml string }
	}
	Services, Endpoints []collectionObject
	Draft               struct {
		KSM struct{ ServiceMonitor map[string]any }
	}
}

func cm(m map[string]any, key string) map[string]any { v, _ := m[key].(map[string]any); return v }
func cs(m map[string]any, key string) string         { v, _ := m[key].(string); return v }

// assertCompanion 不复制任何 Operator 名称/端口/labels 算法，只核公开引用和原父所有权。
func assertCompanion(s collectionSnapshot) {
	Expect(s.Parent.APIVersion).To(Equal("monitoring.fjsxy.github.io/v1alpha1"))
	Expect(s.Parent.Kind).To(Equal("KubeStateMetrics"))
	Expect(s.Parent.Metadata.Namespace).To(BeEmpty())
	Expect(s.Parent.Metadata.UID).NotTo(BeEmpty())
	Expect(s.Instance.UID).To(Equal(s.Parent.Metadata.UID))
	Expect(s.Parent.Metadata.Name).To(Equal(s.Instance.Name))
	status := cm(s.Parent.Status, "serviceMonitor")
	Expect(status["state"]).To(Equal("Ready"))
	Expect(status["observedGeneration"]).To(BeNumerically("==", s.Parent.Metadata.Generation))
	ref := cm(status, "resource")
	Expect(s.Companion).NotTo(BeNil())
	child := s.Companion
	Expect(child.APIVersion).To(Equal("monitoring.coreos.com/v1"))
	Expect(child.Kind).To(Equal("ServiceMonitor"))
	Expect(ref["apiVersion"]).To(Equal(child.APIVersion))
	Expect(ref["kind"]).To(Equal(child.Kind))
	Expect(ref["namespace"]).To(Equal(child.Metadata.Namespace))
	Expect(ref["name"]).To(Equal(child.Metadata.Name))
	Expect(ref["uid"]).To(Equal(child.Metadata.UID))
	Expect(child.Metadata.Namespace).To(Equal(cs(s.Parent.Spec, "targetNamespace")))
	Expect(child.Metadata.UID).NotTo(BeEmpty())
	owners := 0
	for _, owner := range child.Metadata.OwnerReferences {
		if owner.Controller {
			owners++
			Expect(owner.APIVersion).To(Equal(s.Parent.APIVersion))
			Expect(owner.Kind).To(Equal(s.Parent.Kind))
			Expect(owner.Name).To(Equal(s.Parent.Metadata.Name))
			Expect(owner.UID).To(Equal(s.Parent.Metadata.UID))
		}
	}
	Expect(owners).To(Equal(1))
	Expect(s.Instance.CreatorUserID).NotTo(BeEmpty())
	Expect(child.Metadata.Labels["monitor-mgr.io/user-id"]).To(Equal(s.Instance.CreatorUserID))
	intent := cm(s.Parent.Spec, "serviceMonitor")
	Expect(intent["enabled"]).To(BeTrue())
	Expect(cm(intent, "labels")["monitor-mgr.io/user-id"]).To(Equal(s.Instance.CreatorUserID))
	Expect(s.Sources).To(HaveLen(1))
	source := s.Sources[0]
	Expect(source.Source).To(Equal("ServiceMonitor"))
	Expect(source.Historical).To(BeFalse())
	Expect(source.Stale).To(BeFalse())
	Expect(source.Error).To(BeEmpty())
	Expect(source.Companion.State).To(Equal("Ready"))
	Expect(source.Companion.ParentUID).To(Equal(s.Parent.Metadata.UID))
	Expect(source.Companion.Resource.UID).To(Equal(child.Metadata.UID))
	Expect(source.Companion.Yaml).NotTo(BeEmpty())
	Expect(s.Version.Resources).To(HaveLen(1))
	Expect(s.Version.Resources[0].Kind).To(Equal("KubeStateMetrics"))
	Expect(s.Version.Resources[0].Key).To(Equal("component"))
	var frozen map[string]any
	Expect(yaml.Unmarshal([]byte(s.Version.Yaml), &frozen)).To(Succeed())
	Expect(frozen["kind"]).To(Equal("KubeStateMetrics"))
	Expect(cm(cm(frozen, "spec"), "serviceMonitor")).To(Equal(intent))
	endpoints, _ := child.Spec["endpoints"].([]any)
	Expect(endpoints).NotTo(BeEmpty())
	for _, raw := range endpoints {
		e := raw.(map[string]any)
		Expect(e["interval"]).To(Equal(intent["interval"]))
		Expect(e["scrapeTimeout"]).To(Equal(intent["scrapeTimeout"]))
	}
	// 服务集合由实际 ServiceMonitor selector 求得，独立比较父所有权和真实 Endpoints 命名端口。
	selectorRaw, _ := json.Marshal(child.Spec["selector"])
	var spec metav1.LabelSelector
	Expect(json.Unmarshal(selectorRaw, &spec)).To(Succeed())
	selector, err := metav1.LabelSelectorAsSelector(&spec)
	Expect(err).NotTo(HaveOccurred())
	selected, owned := []string{}, []string{}
	for _, service := range s.Services {
		if selector.Matches(labels.Set(service.Metadata.Labels)) {
			selected = append(selected, service.Metadata.Name)
			for _, endpoint := range endpoints {
				port := cs(endpoint.(map[string]any), "port")
				Expect(port).NotTo(BeEmpty())
				hasServicePort := false
				ports, _ := service.Spec["ports"].([]any)
				for _, p := range ports {
					if cs(p.(map[string]any), "name") == port {
						hasServicePort = true
					}
				}
				Expect(hasServicePort).To(BeTrue(), "SM endpoint 必须引用真实 Service 命名端口")
				hasReadyEndpoint := false
				for _, actual := range s.Endpoints {
					if actual.Metadata.Name == service.Metadata.Name {
						for _, subset := range actual.Subsets {
							for _, p := range subset.Ports {
								if p.Name == port && len(subset.Addresses) > 0 {
									hasReadyEndpoint = true
								}
							}
						}
					}
				}
				if s.Parent.Spec["suspended"] != true {
					Expect(hasReadyEndpoint).To(BeTrue(), "SM 目标必须有真实 Ready Endpoints")
				}
			}
		}
		for _, owner := range service.Metadata.OwnerReferences {
			if owner.Controller && owner.UID == s.Parent.Metadata.UID {
				owned = append(owned, service.Metadata.Name)
			}
		}
	}
	sort.Strings(selected)
	sort.Strings(owned)
	Expect(selected).NotTo(BeEmpty())
	Expect(selected).To(Equal(owned), "配套选择集合应恰好覆盖原父拥有的指标 Service")
}

// assertIssue28Evidence 把真实受限身份、零尝试审计和 Operator 生命周期分别核验。
func assertIssue28Evidence(evidence map[string]json.RawMessage) {
	var proof struct {
		TestedCommit                                                                                                                            string
		Topologies, Initial                                                                                                                     []collectionSnapshot
		Updated, OtherAfterUpdate, Rebuilt, Conflict, Repaired, Suspended, Closed, Disabled, Restored, OtherFinal, OtherBaseline, ReadRecovered collectionSnapshot
		ReadFailure                                                                                                                             []struct {
			Error      string
			Historical bool
		}
		Foreign         collectionObject
		DeletedInstance struct{ Lifecycle string }
		Deletion        struct {
			State     string
			Resources []struct{ Target struct{ Kind string } }
		}
		Credentials struct {
			Identity, ServiceAccountUID string
			IdentityReview              struct {
				Status struct{ UserInfo struct{ Username string } }
			}
			Permissions []struct {
				Group, Resource, Verb string
				Status                struct{ Allowed bool }
			}
		}
		OperatorIdentity string
		OperatorProofs   []struct{ Target, Deployment, Source, PodUID, ContainerID, ConfigDigest string }
		Audit            []struct {
			AuditID, Stage, Username, Verb string
			ObjectRef                      struct{ APIGroup, Resource, Subresource, Namespace, Name, UID string }
			ResponseStatus                 struct{ Code int }
		}
	}
	Expect(json.Unmarshal(evidence["issue28"], &proof)).To(Succeed())
	Expect(proof.TestedCommit).To(Equal(os.Getenv("MONITOR_MGR_E2E_CANDIDATE")))
	Expect(proof.TestedCommit).To(MatchRegexp("^[0-9a-f]{40}$"))
	ginkgo.By("I28-K01：三种真实部署模式只按公开 status 引用核验配套")
	Expect(proof.Topologies).To(HaveLen(3))
	for n, mode := range []string{"Deployment", "StatefulSetSharded", "DaemonSetSharded"} {
		Expect(cm(proof.Topologies[n].Parent.Spec, "deployment")["mode"]).To(Equal(mode))
		assertCompanion(proof.Topologies[n])
	}
	ginkgo.By("I28-K01：平台实际 SA 无 SM 写权限，发布/更新/关闭/删除仍成功")
	credentials := proof.Credentials
	Expect(credentials.Identity).To(ContainSubstring("system:serviceaccount:"))
	Expect(credentials.ServiceAccountUID).NotTo(BeEmpty())
	Expect(credentials.IdentityReview.Status.UserInfo.Username).To(Equal(credentials.Identity))
	perms := map[string]bool{}
	for _, p := range credentials.Permissions {
		if p.Resource == "servicemonitors" {
			perms[p.Verb] = p.Status.Allowed
		}
	}
	for _, verb := range []string{"get", "list", "watch"} {
		Expect(perms).To(HaveKeyWithValue(verb, true))
	}
	for _, verb := range []string{"create", "update", "patch", "delete", "deletecollection"} {
		Expect(perms).To(HaveKeyWithValue(verb, false))
	}
	Expect(proof.Initial).To(HaveLen(2))
	for _, snapshot := range proof.Initial {
		Expect(snapshot.Draft.KSM.ServiceMonitor["enabled"]).To(BeTrue())
		assertCompanion(snapshot)
	}
	Expect(proof.Initial[0].Parent.Metadata.UID).NotTo(Equal(proof.Initial[1].Parent.Metadata.UID))
	Expect(proof.Initial[0].Companion.Metadata.UID).NotTo(Equal(proof.Initial[1].Companion.Metadata.UID))
	for _, snapshot := range []collectionSnapshot{proof.Updated, proof.Rebuilt, proof.Repaired, proof.Suspended, proof.Restored, proof.OtherFinal, proof.OtherAfterUpdate} {
		assertCompanion(snapshot)
	}
	Expect(cm(proof.Updated.Parent.Spec, "serviceMonitor")["interval"]).To(Equal("45s"))
	Expect(cm(proof.Updated.Parent.Spec, "serviceMonitor")["scrapeTimeout"]).To(Equal("12s"))
	ginkgo.By("I28-K01/I28-K02：子 UID 正常重建与冲突恢复不改变父 UID 或发布版本")
	for _, snapshot := range []collectionSnapshot{proof.Rebuilt, proof.Conflict, proof.Repaired} {
		Expect(snapshot.Parent.Metadata.UID).To(Equal(proof.Updated.Parent.Metadata.UID))
		Expect(snapshot.Instance.CurrentVersionID).To(Equal(proof.Updated.Instance.CurrentVersionID))
	}
	Expect(proof.Rebuilt.Companion.Metadata.UID).NotTo(Equal(proof.Updated.Companion.Metadata.UID))
	Expect(cm(proof.Conflict.Parent.Status, "serviceMonitor")["state"]).To(Equal("Degraded"))
	Expect(cm(proof.Conflict.Parent.Status, "serviceMonitor")["reason"]).To(Equal("ResourceConflict"))
	Expect(proof.Foreign.Metadata.UID).NotTo(BeEmpty())
	Expect(proof.Foreign.Metadata.OwnerReferences).To(BeEmpty())
	Expect(proof.Repaired.Companion.Metadata.UID).NotTo(Equal(proof.Foreign.Metadata.UID))
	Expect(proof.Suspended.Parent.Spec["suspended"]).To(BeTrue())
	Expect(proof.Suspended.Companion.Metadata.UID).To(Equal(proof.Repaired.Companion.Metadata.UID))
	for _, snapshot := range []collectionSnapshot{proof.Closed, proof.Disabled} {
		Expect(cm(snapshot.Parent.Status, "serviceMonitor")["state"]).To(Equal("Disabled"))
		Expect(snapshot.Companion).To(BeNil())
	}
	Expect(cm(proof.Closed.Parent.Spec, "serviceMonitor")["enabled"]).To(BeFalse())
	Expect(proof.Closed.Parent.Spec["enabled"]).To(BeTrue())
	Expect(proof.Disabled.Parent.Spec["enabled"]).To(BeFalse())
	Expect(proof.Deletion.State).To(Equal("SUCCEEDED"))
	Expect(proof.Deletion.Resources).To(HaveLen(1))
	Expect(proof.Deletion.Resources[0].Target.Kind).To(Equal("KubeStateMetrics"))
	Expect(proof.DeletedInstance.Lifecycle).To(Equal("DELETED"))
	for _, snapshot := range []collectionSnapshot{proof.OtherAfterUpdate, proof.OtherFinal} {
		Expect(snapshot.Parent.Metadata.UID).To(Equal(proof.OtherBaseline.Parent.Metadata.UID))
		Expect(snapshot.Instance.CurrentVersionID).To(Equal(proof.OtherBaseline.Instance.CurrentVersionID))
		Expect(snapshot.Companion.Metadata.UID).To(Equal(proof.OtherBaseline.Companion.Metadata.UID))
		Expect(snapshot.Parent.Spec).To(Equal(proof.OtherBaseline.Parent.Spec))
	}
	Expect(proof.ReadFailure).To(HaveLen(1))
	Expect(proof.ReadFailure[0].Error).To(ContainSubstring("FORBIDDEN"))
	Expect(proof.ReadFailure[0].Historical).To(BeTrue())
	assertCompanion(proof.ReadRecovered)
	ginkgo.By("I28-K01：真实 Metadata audit 正控包含父 CR 写与 SM 读，平台所有 SM 写尝试为零")
	platformMutations, platformReads, operatorCreates := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, event := range proof.Audit {
		Expect(event.AuditID).NotTo(BeEmpty())
		Expect(event.Stage).To(BeElementOf("RequestReceived", "ResponseComplete"))
		if event.Username == credentials.Identity {
			if event.ObjectRef.Resource == "servicemonitors" {
				Expect(event.Verb).To(BeElementOf("get", "list", "watch"))
				platformReads[event.AuditID] = true
			}
			if event.ObjectRef.Resource == "kubestatemetrics" && event.ObjectRef.Subresource == "" && (event.Verb == "create" || event.Verb == "update" || event.Verb == "delete") {
				platformMutations[event.Verb] = true
			}
		}
		if event.Username == proof.OperatorIdentity && event.ObjectRef.Resource == "servicemonitors" && event.Verb == "create" {
			operatorCreates[event.AuditID] = true
		}
	}
	Expect(platformReads).NotTo(BeEmpty())
	Expect(platformMutations).To(HaveKey("create"))
	Expect(platformMutations).To(HaveKey("update"))
	Expect(platformMutations).To(HaveKey("delete"))
	Expect(operatorCreates).NotTo(BeEmpty())
	locked := false
	for _, p := range proof.OperatorProofs {
		if p.Target == "control" && p.Source == "8fb5ccb8ec2e9c0742fc7b83316d01c833af9aee" {
			locked = true
			Expect(p.PodUID).NotTo(BeEmpty())
			Expect(p.ContainerID).NotTo(BeEmpty())
			Expect(p.ConfigDigest).To(MatchRegexp("^sha256:[0-9a-f]{64}$"))
		}
	}
	Expect(locked).To(BeTrue(), "必须绑定锁定 RC4 实际 Pod/容器")
}
