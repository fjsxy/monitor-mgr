// Operator 配套观察独立于平台写入结果与 workload 可用性，只读引用对象。
package control

import (
	"context"
	"encoding/json"
	"net/url"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"k8s.io/apimachinery/pkg/util/validation"
)

const companionSource = "ServiceMonitor"
const serviceMonitorAPI = "monitoring.coreos.com/v1"

// companionReference 只接受公开契约的完整路径，不能按 labels、猜测名称或未知 UID 认领。
func companionReference(cr map[string]any, ref map[string]any) (*rpc.OperatorResourceReference, error) {
	r := &rpc.OperatorResourceReference{ApiVersion: objectString(ref, "apiVersion"), Kind: objectString(ref, "kind"), Namespace: objectString(ref, "namespace"), Name: objectString(ref, "name"), Uid: objectString(ref, "uid")}
	if r.ApiVersion != serviceMonitorAPI || r.Kind != "ServiceMonitor" || r.Namespace != objectString(objectMap(cr, "spec"), "targetNamespace") || len(validation.IsDNS1123Label(r.Namespace)) != 0 || len(validation.IsDNS1123Subdomain(r.Name)) != 0 || r.Uid == "" {
		return nil, Error("CONFLICT", "Operator 配套引用身份不完整或不符合父 CR 目标位置；不猜测或认领")
	}
	return r, nil
}

// refreshExporterCompanion 保存独立成功时间；读取错误保留历史快照并立即失去当前可信度。
func (s *Store) refreshExporterCompanion(ctx context.Context, i *rpc.ObservationInstance, t *Target, cr map[string]any, parentError error) {
	if parentError != nil {
		s.saveSourceObservation(ctx, i, companionSource, nil, parentError)
		return
	}
	out := &rpc.CompanionObservation{State: "Unknown", Reason: "ObservationMissing", Message: "Operator 尚未报告配套状态", ParentUid: i.Uid}
	source := &rpc.SourceObservation{Source: companionSource, Companion: out}
	save := func(e error) { s.saveSourceObservation(ctx, i, companionSource, source, e) }
	if cr == nil {
		out.Message = "原父 CR 不存在，配套当前状态未知"
		save(nil)
		return
	}
	meta, status := objectMap(cr, "metadata"), objectMap(cr, "status")
	gen, gok := meta["generation"].(float64)
	observed := objectMap(status, "serviceMonitor")
	seen, sok := observed["observedGeneration"].(float64)
	if gok {
		out.Generation = int64(gen)
	}
	if sok {
		out.ObservedGeneration = int64(seen)
	}
	if observed == nil {
		save(nil)
		return
	}
	if !gok || !sok || gen < 1 || seen != gen {
		out.State = "Reconciling"
		out.Reason = "GenerationPending"
		out.Message = "Operator 配套观察尚未覆盖当前父 generation"
		save(nil)
		return
	}
	out.State = objectString(observed, "state")
	out.Reason = objectString(observed, "reason")
	out.Message = objectString(observed, "message")
	switch out.State {
	case "Unknown", "Disabled", "Ready", "Degraded", "Deleting":
	default:
		out.State = "Unknown"
		out.Reason = "InvalidState"
		out.Message = "Operator 配套状态不完整"
		save(nil)
		return
	}
	condition := ""
	conditions, _ := status["conditions"].([]any)
	for _, raw := range conditions {
		c, _ := raw.(map[string]any)
		if objectString(c, "type") == "ServiceMonitorReady" && c["observedGeneration"] == gen {
			condition = objectString(c, "status")
		}
	}
	expected := "True"
	if out.State == "Degraded" || out.State == "Deleting" {
		expected = "False"
	}
	if out.State == "Unknown" {
		expected = "Unknown"
	}
	if condition != expected {
		out.State = "Reconciling"
		out.Reason = "ConditionPending"
		out.Message = "Operator 配套 condition 尚未确认当前状态"
		save(nil)
		return
	}
	ref := objectMap(observed, "resource")
	if ref == nil {
		if out.State == "Ready" {
			out.State = "Unknown"
			out.Reason = "ReferenceMissing"
			out.Message = "Operator 报告 Ready 但缺少资源引用"
		}
		save(nil)
		return
	}
	r, e := companionReference(cr, ref)
	if e != nil {
		// 创建响应丢失/外部冲突时 Operator 可报告未确认 UID；只展示故障，不 GET 或采信它。
		if out.State == "Degraded" || out.State == "Unknown" {
			out.Message += "；资源引用尚未确认"
			save(nil)
			return
		}
		save(e)
		return
	}
	out.Resource = r
	var obj map[string]any
	path := "/apis/" + r.ApiVersion + "/namespaces/" + url.PathEscape(r.Namespace) + "/servicemonitors/" + url.PathEscape(r.Name)
	if e = t.Request(ctx, "GET", path, nil, &obj); e != nil {
		save(e)
		return
	}
	m := objectMap(obj, "metadata")
	if objectString(obj, "apiVersion") != r.ApiVersion || objectString(obj, "kind") != r.Kind || objectString(m, "name") != r.Name || objectString(m, "namespace") != r.Namespace || objectString(m, "uid") != r.Uid || !companionController(m, i) {
		save(Error("CONFLICT", "Operator "+out.State+"/"+out.Reason+"："+out.Message+"；配套引用 UID 或 controller owner 与原父 CR 不一致，不认领同名对象"))
		return
	}
	// 关键：正常子资源重建的新 UID 来自当前父 status，无需修改父 UID/版本，也不会产生平台写任务。
	out.Yaml = reference(yamlText(safeTargetDocument(obj)))
	save(nil)
}

// companionController 关键：配套对象的 controller owner 必须指向实例已确认的父 UID，不能仅按名称接管。
func companionController(meta map[string]any, i *rpc.ObservationInstance) bool {
	refs, _ := meta["ownerReferences"].([]any)
	for _, raw := range refs {
		r, _ := raw.(map[string]any)
		if r["controller"] == true {
			return objectString(r, "apiVersion") == ksmAPI && objectString(r, "kind") == "KubeStateMetrics" && objectString(r, "name") == i.Name && objectString(r, "uid") == i.Uid
		}
	}
	return false
}

// companionGraphCandidates 读取全部 namespace 的配套对象，原父 UID 是唯一认领根。
// 关键：未声明采集的旧版本不新增可选 CRD 依赖；已观察到的原子 UID 即使 owner 漂移仍由既有图保护。
func (s *Store) companionGraphCandidates(ctx context.Context, i *rpc.ObservationInstance, t *Target) ([]seenResource, error) {
	var raw []byte
	var seen int
	s.DB.QueryRowContext(ctx, `SELECT count(*) FROM seen_resources WHERE instance_id=? AND kind='ServiceMonitor'`, i.Id).Scan(&seen)
	declared := false
	if s.DB.QueryRowContext(ctx, `SELECT effective_spec FROM publication_versions WHERE id=? AND instance_id=?`, i.CurrentVersionId, i.Id).Scan(&raw) == nil {
		var spec map[string]any
		if json.Unmarshal(raw, &spec) == nil {
			_, declared = spec["serviceMonitor"]
		}
	}
	if !declared && seen == 0 {
		return nil, nil
	}
	return t.listGraphCandidates(ctx, "", []graphResourceKind{serviceMonitorGraphKind})
}
