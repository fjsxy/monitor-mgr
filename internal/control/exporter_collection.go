// exporter 配套配置由 Operator 维护；平台只发布意图与只读核验。
package control

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// prepareCollectionIntent 在当前实例读取后固定受控归属；管理员确认权限不能代替创建证据。
func prepareCollectionIntent(i *rpc.ObservationInstance, cfg configuration) error {
	ksm, ok := cfg.(*KSMConfig)
	if !ok || ksm.ServiceMonitor == nil {
		return nil
	}
	if ksm.ServiceMonitor.Enabled && i.CreatorUserId == "" {
		return Error("CONFLICT", "实例创建用户未知，不能启用配套采集；管理权限确认不会补造创建身份")
	}
	ksm.creatorUserID = i.CreatorUserId
	return nil
}

// schemaProperty 只读取 served CRD 的明确类型，不把 preserve-unknown 当作能力证明。
func schemaProperty(schema map[string]any, path string) map[string]any {
	for _, part := range strings.Split(path, ".") {
		schema = objectMap(objectMap(schema, "properties"), part)
	}
	return schema
}

// checkExporterCapability 核对 RC4 公开机器契约，防止旧 schema 静默剪裁新字段。
func (t *Target) checkExporterCapability(ctx context.Context) error {
	var crd map[string]any
	if e := t.Request(ctx, "GET", "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/kubestatemetrics.monitoring.fjsxy.github.io", nil, &crd); e != nil {
		return Error("CONFLICT", "无法读取 KubeStateMetrics served schema，不能确认配套采集能力："+safeSourceError(e))
	}
	spec := objectMap(crd, "spec")
	if objectString(crd, "apiVersion") != "apiextensions.k8s.io/v1" || objectString(crd, "kind") != "CustomResourceDefinition" || objectString(objectMap(crd, "metadata"), "name") != "kubestatemetrics.monitoring.fjsxy.github.io" || objectString(spec, "group") != "monitoring.fjsxy.github.io" || objectString(spec, "scope") != "Cluster" || objectString(objectMap(spec, "names"), "kind") != "KubeStateMetrics" {
		return Error("CONFLICT", "不支持配套采集：CRD 身份或作用域不匹配")
	}
	versions, _ := spec["versions"].([]any)
	for _, raw := range versions {
		v, _ := raw.(map[string]any)
		if objectString(v, "name") != "v1alpha1" || v["served"] != true {
			continue
		}
		schema := objectMap(objectMap(v, "schema"), "openAPIV3Schema")
		for path, want := range map[string]string{
			"spec.serviceMonitor": "object", "spec.serviceMonitor.enabled": "boolean", "spec.serviceMonitor.labels": "object", "spec.serviceMonitor.interval": "string", "spec.serviceMonitor.scrapeTimeout": "string",
			"status.serviceMonitor": "object", "status.serviceMonitor.observedGeneration": "integer", "status.serviceMonitor.state": "string", "status.serviceMonitor.reason": "string", "status.serviceMonitor.message": "string",
			"status.serviceMonitor.resource": "object", "status.serviceMonitor.resource.apiVersion": "string", "status.serviceMonitor.resource.kind": "string", "status.serviceMonitor.resource.namespace": "string", "status.serviceMonitor.resource.name": "string", "status.serviceMonitor.resource.uid": "string",
		} {
			field := schemaProperty(schema, path)
			if objectString(field, "type") != want || field["x-kubernetes-preserve-unknown-fields"] == true {
				return Error("CONFLICT", "不支持配套采集：served schema 字段缺失或类型不匹配 "+path)
			}
		}
		if schemaProperty(schema, "spec.serviceMonitor.enabled")["default"] != false || objectString(objectMap(schemaProperty(schema, "spec.serviceMonitor.labels"), "additionalProperties"), "type") != "string" {
			return Error("CONFLICT", "不支持配套采集：enabled 必须默认 false，labels 必须为 string map")
		}
		states, _ := schemaProperty(schema, "status.serviceMonitor.state")["enum"].([]any)
		found := map[string]bool{}
		for _, state := range states {
			if value, ok := state.(string); ok {
				found[value] = true
			}
		}
		for _, state := range []string{"Unknown", "Disabled", "Ready", "Degraded", "Deleting"} {
			if !found[state] {
				return Error("CONFLICT", "不支持配套采集：缺少观察状态 "+state)
			}
		}
		return nil
	}
	return Error("CONFLICT", "不支持配套采集：缺少 served v1alpha1 schema")
}

// collectionDependencies 不要求平台拥有 SM 写权限；开启时只读确认 CRD 与观察权限。
func (t *Target) collectionDependencies(ctx context.Context, desired map[string]any) error {
	monitor := objectMap(desired, "serviceMonitor")
	if monitor == nil {
		return nil
	}
	if e := t.checkExporterCapability(ctx); e != nil {
		return e
	}
	if monitor["enabled"] != true {
		return nil
	}
	var list map[string]any
	if e := t.Request(ctx, "GET", "/apis/monitoring.coreos.com/v1/namespaces/"+url.PathEscape(objectString(desired, "targetNamespace"))+"/servicemonitors?limit=1", nil, &list); e != nil {
		return Error("CONFLICT", "ServiceMonitor CRD 或观察读取权限不可用："+safeSourceError(e))
	}
	// list 成功不能证明单个 GET 的独立 RBAC，使用真实当前身份的 SSAR 核验。
	for _, verb := range []string{"get", "list"} {
		var review struct {
			Status struct {
				Allowed bool `json:"allowed"`
			} `json:"status"`
		}
		body := map[string]any{"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview", "spec": map[string]any{"resourceAttributes": map[string]string{"group": "monitoring.coreos.com", "resource": "servicemonitors", "verb": verb, "namespace": objectString(desired, "targetNamespace")}}}
		if e := t.Request(ctx, "POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", body, &review); e != nil {
			return e
		}
		if !review.Status.Allowed {
			return Error("FORBIDDEN", "ServiceMonitor 观察读取权限不足")
		}
	}
	return nil
}

// collectionDuration 先复用 Prometheus 语法与溢出检查，再比较正 duration。
func collectionDuration(value string) (time.Duration, bool) {
	if !validPrometheusDuration(value) {
		return 0, false
	}
	total := time.Duration(0)
	units := map[string]time.Duration{"ms": time.Millisecond, "s": time.Second, "m": time.Minute, "h": time.Hour, "d": 24 * time.Hour, "w": 7 * 24 * time.Hour, "y": 365 * 24 * time.Hour}
	for _, part := range regexp.MustCompile(`([0-9]+)(ms|[ywdhms])`).FindAllStringSubmatch(value, -1) {
		n, _ := strconv.ParseInt(part[1], 10, 64)
		total += time.Duration(n) * units[part[2]]
	}
	return total, total > 0
}

// validateCollection 填充公开契约默认参数，非法配置不进入草稿或版本。
func (c *KSMConfig) validateCollection() error {
	m := c.ServiceMonitor
	if m == nil {
		return nil
	}
	if m.Interval == "" {
		m.Interval = "30s"
	}
	if m.ScrapeTimeout == "" {
		m.ScrapeTimeout = "10s"
	}
	interval, ok := collectionDuration(m.Interval)
	if !ok {
		return fieldError("serviceMonitor.interval", "须为正 Prometheus duration")
	}
	timeout, ok := collectionDuration(m.ScrapeTimeout)
	if !ok {
		return fieldError("serviceMonitor.scrapeTimeout", "须为正 Prometheus duration")
	}
	if timeout > interval {
		return fieldError("serviceMonitor.scrapeTimeout", "不能大于 interval")
	}
	return nil
}

// historicalSMDocumentFingerprint 绑定旧冻结文档原字节，供显式管理员处理记录复核使用。
func historicalSMDocumentFingerprint(document []byte) string {
	sum := sha256.Sum256(document)
	return "sha256:" + hex.EncodeToString(sum[:])
}

// validateHistoricalCollection 在新启用前盘点全部旧 SM；已成功和不同名称也可能造成重复采集。
func validateHistoricalCollection(ctx context.Context, q clusterQuery, i *rpc.ObservationInstance, desired map[string]any, t *Target) error {
	if i.Component != KSMComponent || objectMap(desired, "serviceMonitor")["enabled"] != true {
		return nil
	}
	steps, e := historicalServiceMonitorSteps(ctx, q, i.Id)
	if e != nil {
		return Error("UNAVAILABLE", "旧采集配置历史无法核验")
	}
	for _, step := range steps {
		if !step.possiblySent && step.originalUID == "" && step.confirmedUID == "" && step.state != "SUCCEEDED" {
			continue
		}
		uid := step.confirmedUID
		if uid == "" {
			uid = step.originalUID
		}
		var exists int
		q.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type='table' AND name='historical_servicemonitor_resolutions'`).Scan(&exists)
		var n int
		// 关键：#29 的显式处理记录独立于旧步骤状态和只读 refresh；未知 UID 仍为空；独立处理记录不补认旧任务。
		if exists == 0 || q.QueryRowContext(ctx, `SELECT count(*) FROM historical_servicemonitor_resolutions WHERE instance_id=? AND operation_id=? AND resource_key=? AND object_path=? AND original_uid=? AND document_fingerprint=? AND resolved_by<>'' AND resolved_at<>''`, i.Id, step.operationID, step.resourceKey, step.path, uid, historicalSMDocumentFingerprint(step.document)).Scan(&n) != nil || n == 0 {
			return Error("CONFLICT", "仍有旧平台 ServiceMonitor 可能存在或结果待确认；先由集群管理员显式盘点采集影响并处理旧对象，再启用 Operator 配套采集")
		}
		// 过去已处理不等于当前无目标；同路径重建/替代仍可能被 Agent 选中，不能自动放行。
		if t == nil || !strings.HasPrefix(step.path, "/apis/monitoring.coreos.com/v1/namespaces/") || strings.ContainsAny(step.path, "?%") || strings.Contains(step.path, "..") {
			return Error("CONFLICT", "旧 ServiceMonitor 冻结路径无法安全复核")
		}
		var obj map[string]any
		e := t.Request(ctx, "GET", step.path, nil, &obj)
		if apiCode(e) != "TARGET_NOT_FOUND" {
			return Error("CONFLICT", "旧 ServiceMonitor 路径仍存在对象或读取结果未知；须重新显式处理")
		}
	}
	return nil
}
