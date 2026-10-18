// Agent辅助来源独立保存成功快照与失败时间；主CR成功不能刷新PVC或关联来源。
package control

import (
	"context"
	"encoding/json"
	"net/url"
	"sort"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

func (s *Store) saveAgentSource(ctx context.Context, i *rpc.ObservationInstance, source string, current *rpc.SourceObservation, cause error) *rpc.SourceObservation {
	now := s.Now().UTC().Format(time.RFC3339Nano)
	out := current
	if out == nil {
		out = &rpc.SourceObservation{Source: source}
	}
	if cause != nil {
		var raw []byte
		var uid string
		if s.DB.QueryRowContext(ctx, `SELECT document,uid FROM instance_source_observations WHERE instance_id=? AND source=?`, i.Id, source).Scan(&raw, &uid) == nil && uid == i.Uid {
			var previous rpc.SourceObservation
			if json.Unmarshal(raw, &previous) == nil {
				out = &previous
			}
		}
		out.Error = safeSourceError(cause)
		out.Historical = true
	} else {
		out.LastSuccessAt = now
		out.Error = ""
		out.Historical = false
	}
	out.Source = source
	out.LastAttemptAt = now
	raw, _ := json.Marshal(out)
	if _, e := s.DB.ExecContext(ctx, `INSERT INTO instance_source_observations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,source) DO UPDATE SET uid=excluded.uid,document=excluded.document,last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,error=excluded.error,epoch=excluded.epoch`, i.Id, source, i.Uid, raw, out.LastAttemptAt, out.LastSuccessAt, out.Error, s.observationEpoch); e != nil {
		out.Error = "辅助来源保存失败"
		out.Historical = true
	}
	return out
}
func safeSourceError(e error) string {
	if a, ok := e.(*rpc.APIError); ok {
		return a.Code + "：" + a.Message
	}
	return "辅助来源读取失败"
}
func (s *Store) agentSources(ctx context.Context, i *rpc.ObservationInstance) []*rpc.SourceObservation {
	out := []*rpc.SourceObservation{}
	rows, e := s.DB.QueryContext(ctx, `SELECT source,document,epoch FROM instance_source_observations WHERE instance_id=? AND uid=? ORDER BY source`, i.Id, i.Uid)
	if e != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var name, epoch string
		var raw []byte
		if rows.Scan(&name, &raw, &epoch) != nil {
			continue
		}
		v := &rpc.SourceObservation{Source: name}
		if json.Unmarshal(raw, v) != nil {
			v.Error = "历史来源记录不可读"
			v.Historical = true
		} else {
			stamp, e := time.Parse(time.RFC3339Nano, v.LastSuccessAt)
			if e != nil || epoch != s.observationEpoch || s.Now().Sub(stamp) > 30*time.Second || v.Error != "" {
				v.Historical = true
			}
		}
		out = append(out, v)
	}
	return out
}

// associationObservations逐类读取；只有实际CR来源可以保存运行历史。
func (s *Store) associationObservations(ctx context.Context, i *rpc.ObservationInstance, t *Target, cfg *AgentConfig, persist bool) []*rpc.SourceObservation {
	out := []*rpc.SourceObservation{}
	operator, operatorErr := t.agentOperator(ctx, cfg)
	if operatorErr == nil {
		operatorErr = t.verifyOperatorRuntime(ctx, operator, cfg.Associations.ScrapeConfig.Object.Mode != "None")
	}
	for _, kind := range associationKinds {
		source := "association:" + kind.kind
		rule := cfg.associationRules()[kind.kind]
		var items []map[string]any
		var e error
		if rule.Object.Mode == "None" {
			items = []map[string]any{}
		} else if operatorErr != nil {
			e = operatorErr
		} else {
			var namespaces []string
			namespaces, e = t.associationNamespaces(ctx, cfg.Namespace, rule.Namespace)
			if e == nil {
				for _, ns := range namespaces {
					if !operator.visible(ns, false) {
						e = Error("CONFLICT", "采集配置namespace不在Operator可见范围")
						break
					}
					for _, verb := range []string{"get", "list", "watch"} {
						if e = t.subjectAccess(ctx, operator.user, ns, "monitoring.coreos.com", kind.resource, verb); e != nil {
							break
						}
					}
					if e != nil {
						break
					}
				}
			}
			if e == nil {
				items, e = t.matchAssociation(ctx, cfg, kind)
			}
		}
		value := &rpc.SourceObservation{Source: source, Matches: []*rpc.MatchedResource{}}
		if e == nil {
			for _, item := range items {
				meta := objectMap(item, "metadata")
				value.Matches = append(value.Matches, &rpc.MatchedResource{Kind: kind.kind, Namespace: objectString(meta, "namespace"), Name: objectString(meta, "name"), Uid: objectString(meta, "uid")})
			}
			sort.Slice(value.Matches, func(a, b int) bool {
				return value.Matches[a].Namespace+"/"+value.Matches[a].Name < value.Matches[b].Namespace+"/"+value.Matches[b].Name
			})
		}
		if persist {
			value = s.saveAgentSource(ctx, i, source, value, e)
		} else {
			// 关键：草稿预览属于请求修订，不能覆盖已部署CR的来源或最后成功时间。
			value.LastAttemptAt = s.Now().UTC().Format(time.RFC3339Nano)
			if e != nil {
				value.Error = safeSourceError(e)
			} else {
				value.LastSuccessAt = value.LastAttemptAt
			}
		}
		out = append(out, value)
	}
	return out
}
func (s *Store) PreviewAssociations(ctx context.Context, c *rpc.RequestContext, r *rpc.AssociationPreviewRequest) (*rpc.AssociationPreview, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly {
		return nil, Error("FORBIDDEN", "外部实例无平台草稿关联规则")
	}
	if e = rejectUnknownFields(c, "context"); e != nil {
		return nil, e
	}
	if e = rejectUnknownFields(r, "request"); e != nil {
		return nil, e
	}
	if i.Component != AgentComponent {
		return nil, fieldError("component", "仅Agent具有采集关联")
	}
	value, rev, e := readDraft(ctx, s.DB, i.Id)
	if e != nil {
		return nil, e
	}
	if rev != r.DraftRevision {
		return nil, Error("CONFLICT", "草稿修订已变化")
	}
	cfg := value.(*AgentConfig)
	target, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer target.Close()
	sources := s.associationObservations(ctx, i, target, cfg, false)
	_, latest, e := readDraft(ctx, s.DB, i.Id)
	if e != nil {
		return nil, e
	}
	if latest != rev {
		return nil, Error("CONFLICT", "预览期间草稿已变化，请重新读取")
	}
	return &rpc.AssociationPreview{DraftRevision: rev, Sources: sources}, nil
}
func associationConfigFromCR(cr map[string]any) *AgentConfig {
	meta, spec := objectMap(cr, "metadata"), objectMap(cr, "spec")
	cfg := &AgentConfig{Name: objectString(meta, "name"), Namespace: objectString(meta, "namespace")}
	rule := func(prefix string) AssociationRule {
		parse := func(key, empty string) SelectorRule {
			raw, exists := spec[key]
			if !exists || raw == nil {
				return SelectorRule{Mode: empty}
			}
			m, ok := raw.(map[string]any)
			if !ok {
				return SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{}}
			}
			if len(m) == 0 {
				return SelectorRule{Mode: "All"}
			}
			r := SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{}}
			b, _ := json.Marshal(m)
			json.Unmarshal(b, r.Selector)
			return r
		}
		return AssociationRule{Object: parse(prefix+"Selector", "None"), Namespace: parse(prefix+"NamespaceSelector", "Own")}
	}
	cfg.Associations = AgentAssociations{ServiceMonitor: rule("serviceMonitor"), PodMonitor: rule("podMonitor"), Probe: rule("probe"), ScrapeConfig: rule("scrapeConfig")}
	return cfg
}
func (s *Store) refreshAgentAuxiliary(ctx context.Context, i *rpc.ObservationInstance, t *Target, cr map[string]any) {
	s.associationObservations(ctx, i, t, associationConfigFromCR(cr), true)
	volumes, e := s.readAgentVolumes(ctx, i, t)
	s.saveAgentSource(ctx, i, "PVC", &rpc.SourceObservation{Source: "PVC", Volumes: volumes}, e)
}
func (s *Store) readAgentVolumes(ctx context.Context, i *rpc.ObservationInstance, t *Target) ([]*rpc.VolumeObservation, error) {
	prefix := "/api/v1/namespaces/" + url.PathEscape(i.TargetNamespace)
	sts, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(i.TargetNamespace)+"/statefulsets")
	if e != nil {
		return nil, e
	}
	owners := map[string]bool{}
	for _, st := range sts {
		if agentOwnedBy(st, "PrometheusAgent", i.Uid) {
			uid := objectString(objectMap(st, "metadata"), "uid")
			if uid == "" {
				return nil, Error("TARGET_ERROR", "StatefulSet身份不完整")
			}
			owners[uid] = true
		}
	}
	pods, e := t.listObjects(ctx, prefix+"/pods")
	if e != nil {
		return nil, e
	}
	claims := map[string][]string{}
	for _, pod := range pods {
		owned := false
		for uid := range owners {
			if agentOwnedBy(pod, "StatefulSet", uid) {
				owned = true
			}
		}
		if !owned {
			continue
		}
		name := objectString(objectMap(pod, "metadata"), "name")
		for _, raw := range anySlice(objectMap(pod, "spec")["volumes"]) {
			v, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			claim := objectString(objectMap(v, "persistentVolumeClaim"), "claimName")
			if claim != "" {
				claims[claim] = append(claims[claim], name)
			}
		}
	}
	pvcs, e := t.listObjects(ctx, prefix+"/persistentvolumeclaims")
	if e != nil {
		return nil, e
	}
	out := []*rpc.VolumeObservation{}
	for _, pvc := range pvcs {
		meta := objectMap(pvc, "metadata")
		name := objectString(meta, "name")
		mounted, ok := claims[name]
		if !ok {
			continue
		}
		spec := objectMap(pvc, "spec")
		access := []string{}
		for _, raw := range anySlice(spec["accessModes"]) {
			if v, ok := raw.(string); ok {
				access = append(access, v)
			}
		}
		uid := objectString(meta, "uid")
		if uid == "" {
			return nil, Error("TARGET_ERROR", "PVC身份不完整")
		}
		out = append(out, &rpc.VolumeObservation{Namespace: i.TargetNamespace, Name: name, Uid: uid, Phase: objectString(objectMap(pvc, "status"), "phase"), RequestedStorage: objectString(objectMap(objectMap(spec, "resources"), "requests"), "storage"), AccessModes: access, StorageClass: objectString(spec, "storageClassName"), MountedByPods: mounted})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Name < out[b].Name })
	return out, nil
}
